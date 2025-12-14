import json
import time
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components

# =========================================================
# Avatharam 3.0 – LiveAvatar (FULL mode)
# - Session lifecycle via LiveAvatar REST (HTTP)
# - Speech + mic publishing via LiveKit in viewer.html (browser-side)
# - When "Speak" is pressed (viewer side):
#     avatar.interrupt -> wait 500ms -> avatar.speak_text
# =========================================================

st.set_page_config(page_title="Avatharam 3.0 – LiveAvatar", layout="wide")

# ---------------- Secrets (exact casing/spelling) ----------------
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

# ---------------- Simple in-app logger ----------------
def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    st.session_state.logs.append(f"[{ts}] {msg}")

def _http_post_xapi(url: str, payload: dict, timeout: int = 60) -> dict:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "X-API-KEY": HEYGEN_API_KEY,
    }
    _log(f"POST {url} payload_keys={list(payload.keys())}")
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    _log(f"HTTP {r.status_code} from {url}")
    # Keep body small in logs to avoid Streamlit redaction issues
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}
    if not r.ok:
        _log(f"ERROR body: {str(data)[:800]}")
        r.raise_for_status()
    return data

def create_session_token_full_mode() -> dict:
    payload = {
        "mode": "FULL",
        "avatar_id": FIXED_AVATAR["avatar_id"],
        "avatar_persona": {
            "voice_id": FIXED_AVATAR["voice_id"],
            "context_id": LIVEAVATAR_CONTEXT_ID,
            "language": "en",
        },
    }
    body = _http_post_xapi(EP_SESS_TOKEN, payload)
    # New API shape: {code, data:{session_id, session_token}, message}
    data = body.get("data") or {}
    sid = data.get("session_id")
    stok = data.get("session_token")
    if not sid or not stok:
        raise RuntimeError(f"Missing session_id/session_token in response: {body}")
    return {"session_id": sid, "session_token": stok, "raw": body}

def start_session(session_id: str, session_token: str) -> dict:
    # New API: uses Bearer session_token
    payload = {"session_id": session_id}
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {session_token}",
    }
    _log(f"POST {EP_SESS_START} (Bearer) session_id={session_id}")
    r = requests.post(EP_SESS_START, headers=headers, json=payload, timeout=60)
    _log(f"HTTP {r.status_code} from /sessions/start")
    body = r.json() if r.content else {}
    if not r.ok:
        _log(f"ERROR body: {str(body)[:800]}")
        r.raise_for_status()

    data = body.get("data") or {}
    livekit_url = data.get("livekit_url")
    livekit_client_token = data.get("livekit_client_token")
    if not livekit_url or not livekit_client_token:
        raise RuntimeError(f"Missing LiveKit connection info from /sessions/start: {body}")

    return {
        "session_id": data.get("session_id", session_id),
        "livekit_url": livekit_url,
        "livekit_client_token": livekit_client_token,
        "max_session_duration": data.get("max_session_duration"),
        "raw": body,
    }

def stop_session(session_id: str, session_token: str) -> dict:
    payload = {"session_id": session_id}
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {session_token}",
    }
    _log(f"POST {EP_SESS_STOP} (Bearer) session_id={session_id}")
    r = requests.post(EP_SESS_STOP, headers=headers, json=payload, timeout=60)
    _log(f"HTTP {r.status_code} from /sessions/stop")
    body = r.json() if r.content else {}
    if not r.ok:
        _log(f"ERROR body: {str(body)[:800]}")
    return body

def keep_alive(session_id: str, session_token: str) -> dict:
    payload = {"session_id": session_id}
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {session_token}",
    }
    _log(f"POST {EP_SESS_KEEPALIVE} (Bearer) session_id={session_id}")
    r = requests.post(EP_SESS_KEEPALIVE, headers=headers, json=payload, timeout=60)
    _log(f"HTTP {r.status_code} from /sessions/keep-alive")
    body = r.json() if r.content else {}
    if not r.ok:
        _log(f"ERROR body: {str(body)[:800]}")
    return body

# ---------------- Session state ----------------
ss = st.session_state
ss.setdefault("live_session", None)  # dict with session_id/session_token/livekit_url/livekit_client_token
ss.setdefault("last_error", "")
ss.setdefault("speak_nonce", 0)
ss.setdefault("logs", [])
ss.setdefault("keepalive_last", None)

# ---------------- Load editable speech text (optional) ----------------
default_speech = "Hello"
speech_path = Path("Speech.txt")
if speech_path.exists():
    try:
        default_speech = speech_path.read_text(encoding="utf-8").strip() or default_speech
    except Exception:
        pass

# ---------------- Sidebar ----------------
with st.sidebar:
    st.markdown("## Avatharam Control Panel")

    st.markdown("### Session")
    colA, colB = st.columns(2)
    start_clicked = colA.button("Start session", use_container_width=True)
    stop_clicked = colB.button("Stop session", use_container_width=True)
    keep_clicked = st.button("Keep-alive ping", use_container_width=True)

    st.markdown("### Instruction text")
    speech_text = st.text_area(
        "Speech.txt content (editable)",
        value=default_speech,
        height=120,
    )

    st.markdown("### Speak")
    st.caption("This triggers the viewer-side Speak button flow: interrupt → 500ms → speak_text")
    speak_clicked = st.button("Send text to avatar", type="primary", use_container_width=True)

    st.markdown("### Debug")
    show_debug = st.checkbox("Show debug logs", value=True)

    if ss.last_error:
        st.error(ss.last_error)

# ---------------- Actions ----------------
def _clear_error():
    ss.last_error = ""

if start_clicked:
    _clear_error()
    try:
        _log("Start session clicked.")
        token_info = create_session_token_full_mode()
        sid = token_info["session_id"]
        stok = token_info["session_token"]

        sess = start_session(sid, stok)
        sess["session_token"] = stok  # keep bearer for stop/keepalive
        ss.live_session = sess
        _log("Session started successfully.")
    except Exception as e:
        ss.last_error = f"Failed to start session: {e}"
        _log(ss.last_error)

if stop_clicked:
    _clear_error()
    try:
        if not ss.live_session:
            raise RuntimeError("No active session.")
        _log("Stop session clicked.")
        body = stop_session(ss.live_session["session_id"], ss.live_session["session_token"])
        ss.live_session = None
        _log(f"Session stopped. resp_code={body.get('code')}")
    except Exception as e:
        ss.last_error = f"Failed to stop session: {e}"
        _log(ss.last_error)

if keep_clicked:
    _clear_error()
    try:
        if not ss.live_session:
            raise RuntimeError("No active session.")
        body = keep_alive(ss.live_session["session_id"], ss.live_session["session_token"])
        ss.keepalive_last = body
        _log(f"Keep-alive response: code={body.get('code')} message={body.get('message')}")
    except Exception as e:
        ss.last_error = f"Keep-alive failed: {e}"
        _log(ss.last_error)

if speak_clicked:
    # We "signal" viewer by bumping nonce; viewer reads payload and triggers speak flow.
    ss.speak_nonce += 1
    _log(f"Send-text clicked. nonce={ss.speak_nonce} chars={len(speech_text)}")

# ---------------- Main layout ----------------
left, right = st.columns([1.2, 1.0], gap="large")

with left:
    st.title("Avatharam 3.0 – LiveAvatar")
    st.caption("Stress-test harness for HeyGen LiveAvatar Sessions API (FULL mode) with LiveKit command events.")

    # Session status
    if ss.live_session:
        st.success("Session started.")
        st.json(
            {
                "session_id": ss.live_session["session_id"],
                "livekit_url": ss.live_session["livekit_url"],
                "avatar": FIXED_AVATAR["name"],
                "context_id": LIVEAVATAR_CONTEXT_ID,
            }
        )
        if ss.keepalive_last:
            st.markdown("**Last keep-alive response**")
            st.json(ss.keepalive_last)

        # Render viewer
        viewer_path = Path("viewer.html")
        if not viewer_path.exists():
            st.error("viewer.html not found in project root.")
        else:
            tmpl = viewer_path.read_text(encoding="utf-8")
            # Inject runtime values
            payload = {
                "nonce": ss.speak_nonce,
                "text": speech_text,
                "interrupt_before_speak": True,
                "interrupt_delay_ms": 500,
                "publish_microphone": True,
            }
            html = (
                tmpl.replace("__AVATAR_NAME__", FIXED_AVATAR["name"])
                .replace("__LIVEKIT_URL__", ss.live_session["livekit_url"])
                .replace("__LIVEKIT_TOKEN__", ss.live_session["livekit_client_token"])
                .replace("__PAYLOAD_JSON__", json.dumps(payload))
            )
            # IMPORTANT: streamlit.components.html does NOT accept key= in some versions.
            components.html(html, height=620, scrolling=False)

    else:
        st.info("No active session. Use the sidebar to start one.")
        st.image(FIXED_AVATAR["preview_url"], caption="Preview (no active session)")

with right:
    st.subheader("Notes / Debug")
    st.markdown(
        """
**How this works**
- Session lifecycle is handled by REST (`/sessions/token`, `/sessions/start`, `/sessions/stop`, `/sessions/keep-alive`).
- The browser viewer connects to LiveKit using `livekit_url` + `livekit_client_token`.
- The viewer **publishes microphone audio** (no camera), so you can talk and trigger the agent.
- When you press **Speak** (or when Streamlit triggers a nonce update), the viewer sends:
  1) `avatar.interrupt`
  2) waits 500 ms
  3) sends `avatar.speak_text` with your text

**Why we do this**
- We are testing whether `avatar.interrupt` changes agent state enough to accept `avatar.speak_text`.
- Even if the avatar does not speak, these logs are valuable for HeyGen support.
        """
    )

    if show_debug:
        st.markdown("**App logs**")
        st.text_area("logs", value="\n".join(ss.logs[-300:]), height=320)
