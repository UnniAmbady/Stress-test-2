import json
import time
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components

# =========================================================
# Avatharam 3.0 – LiveAvatar (FULL mode)
# - Session lifecycle via LiveAvatar REST
# - Speech via LiveKit "Command Events" (browser-side)
# =========================================================

st.set_page_config(page_title="Avatharam 3.0 – LiveAvatar", layout="wide")

# ---------------- Secrets ----------------
# NOTE: Keep exact casing/spelling as user requested.
HEYGEN_API_KEY = st.secrets["HeyGen"]["heygen_api_key"]
LIVEAVATAR_CONTEXT_ID = st.secrets["LiveAvatar"]["context_id"]

# ---------------- Fixed Avatar (June HR) ----------------
FIXED_AVATAR = {
    "avatar_id": "65f9e3c9-d48b-4118-b73a-4ae2e3cbb8f0",
    "voice_id": "62bbb4b2-bb26-4727-bc87-cfb2bd4e0cc8",
    "name": "June HR",
    "preview_url": "https://files2.heygen.ai/avatar/v3/74447a27859a456c955e01f21ef18216_45620/preview_talk_1.webp",
}

# ---------------- LiveAvatar REST endpoints ----------------
BASE = "https://api.liveavatar.com/v1"
EP_SESS_TOKEN = f"{BASE}/sessions/token"
EP_SESS_START = f"{BASE}/sessions/start"
EP_SESS_STOP = f"{BASE}/sessions/stop"
EP_SESS_KEEPALIVE = f"{BASE}/sessions/keep-alive"

def _post_xapi(url: str, payload: dict, timeout: int = 60) -> dict:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "X-API-KEY": HEYGEN_API_KEY,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _post_bearer(url: str, token: str, payload: dict, timeout: int = 60) -> dict:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()

def create_session_token_full(avatar_id: str, voice_id: str, context_id: str, language: str = "en") -> dict:
    payload = {
        "mode": "FULL",
        "avatar_id": avatar_id,
        "avatar_persona": {
            "voice_id": voice_id,
            "context_id": context_id,
            "language": language,
        },
    }
    body = _post_xapi(EP_SESS_TOKEN, payload)
    data = (body or {}).get("data", body or {})
    sid = data.get("session_id")
    stok = data.get("session_token")
    if not sid or not stok:
        raise RuntimeError(f"Missing session_id/session_token in response: {body}")
    return {"session_id": sid, "session_token": stok}

def start_session_full() -> dict:
    created = create_session_token_full(
        avatar_id=FIXED_AVATAR["avatar_id"],
        voice_id=FIXED_AVATAR["voice_id"],
        context_id=LIVEAVATAR_CONTEXT_ID,
        language="en",
    )
    sid = created["session_id"]
    stok = created["session_token"]

    body = _post_bearer(EP_SESS_START, stok, {})
    data = (body or {}).get("data", body or {})

    lk_url = data.get("livekit_url")
    lk_token = data.get("livekit_client_token")
    if not lk_url or not lk_token:
        raise RuntimeError(f"Missing LiveKit connection info from /sessions/start: {body}")

    return {
        "session_id": sid,
        "session_token": stok,
        "livekit_url": lk_url,
        "livekit_client_token": lk_token,
    }

def stop_session_full(session_id: str, session_token: str) -> None:
    _post_bearer(EP_SESS_STOP, session_token, {"session_id": session_id})

def keepalive(session_id: str, session_token: str) -> None:
    _post_bearer(EP_SESS_KEEPALIVE, session_token, {"session_id": session_id})

# ---------------- Session state ----------------
ss = st.session_state
ss.setdefault("live_session", None)   # dict with session_id/session_token/livekit_url/livekit_client_token
ss.setdefault("last_error", "")
ss.setdefault("speak_nonce", 0)

# ---------------- Load editable speech text (optional) ----------------
default_speech = "Hello, Hello, Hello."
speech_path = Path("Speech.txt")
if speech_path.exists():
    try:
        default_speech = speech_path.read_text(encoding="utf-8")
    except Exception:
        pass

# ---------------- UI ----------------
st.title("Avatharam 3.0 – LiveAvatar")
st.caption("Stress-test harness for HeyGen LiveAvatar Sessions API (FULL mode) with LiveKit command events.")

with st.sidebar:
    st.header("Avatharam Control Panel")

    st.subheader("Session")
    colA, colB = st.columns(2)
    with colA:
        if st.button("Start session", use_container_width=True):
            ss.last_error = ""
            try:
                ss.live_session = start_session_full()
            except Exception as e:
                ss.live_session = None
                ss.last_error = f"Failed to start session: {e}"

    with colB:
        if st.button("Stop session", use_container_width=True):
            ss.last_error = ""
            try:
                if ss.live_session:
                    stop_session_full(ss.live_session["session_id"], ss.live_session["session_token"])
                ss.live_session = None
            except Exception as e:
                ss.last_error = f"Failed to stop session: {e}"

    if ss.live_session:
        st.success("Session started.")
        if st.button("Keep-alive ping", use_container_width=True):
            try:
                keepalive(ss.live_session["session_id"], ss.live_session["session_token"])
                st.info("Keep-alive sent.")
            except Exception as e:
                ss.last_error = f"Keep-alive failed: {e}"
    else:
        st.warning("No active session.")

    st.subheader("Instruction text")
    instruction_text = st.text_area(
        "Speech.txt content (editable)",
        value=default_speech,
        height=180,
    )

    st.subheader("Speak controls")
    auto_speak_on_connect = st.checkbox("Auto-speak after connect", value=False)
    stress_mode = st.checkbox("Stress mode (repeat speak_text)", value=False)
    interval_s = st.number_input("Interval (seconds)", min_value=1.0, max_value=60.0, value=3.0, step=1.0)
    repeat_n = st.number_input("Repeat count", min_value=1, max_value=500, value=5, step=1)

    speak_clicked = st.button("Speak now", type="primary", use_container_width=True)
    if speak_clicked:
        ss.speak_nonce += 1  # forces a new speak request on next render

    if ss.last_error:
        st.error(ss.last_error)

# ---------------- Main layout ----------------
left, right = st.columns([1, 1], gap="large")

with left:
    st.subheader("Avatar")
    if ss.live_session:
        # Render viewer with LiveKit URL + token + speak request payload
        viewer_template = Path("viewer.html").read_text(encoding="utf-8")

        speak_payload = {
            "nonce": ss.speak_nonce,
            "auto": bool(auto_speak_on_connect),
            "stress": bool(stress_mode),
            "interval_ms": int(interval_s * 1000),
            "repeat": int(repeat_n),
            "text": instruction_text.strip(),
        }

        # Replace placeholders
        html = (viewer_template
                .replace("__LIVEKIT_URL__", ss.live_session["livekit_url"])
                .replace("__LIVEKIT_TOKEN__", ss.live_session["livekit_client_token"])
                .replace("__AVATAR_NAME__", FIXED_AVATAR["name"])
                .replace("__SPEAK_PAYLOAD_JSON__", json.dumps(speak_payload)))

        # key changes when nonce changes -> forces iframe refresh
        components.html(html, height=560, scrolling=False")
    else:
        st.image(FIXED_AVATAR["preview_url"], caption="Preview (no active session)")

with right:
    st.subheader("Notes")
    st.markdown(
        """
**What changed (v1 FULL mode):**
- Session lifecycle is handled by REST (`/sessions/token`, `/sessions/start`, `/sessions/stop`, `/keep-alive`).
- **Speech is triggered in the browser** via LiveKit **Command Events**, e.g. `avatar.speak_text`.

**This app intentionally does NOT publish your camera/mic**
- The viewer joins LiveKit **without** enabling camera/mic, so your video should not appear and you should not be prompted for permissions.
- The viewer only subscribes to the remote avatar video/audio tracks and sends command events over the data channel.
        """
    )

    if ss.live_session:
        st.json(
            {
                "session_id": ss.live_session["session_id"],
                "livekit_url": ss.live_session["livekit_url"],
                "avatar": FIXED_AVATAR["name"],
            }
        )
