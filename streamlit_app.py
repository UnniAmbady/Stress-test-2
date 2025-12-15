import json
import time
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components

# =========================================================
# Avatharam 3.0 – LiveAvatar (FULL mode)
# - Session lifecycle via LiveAvatar REST (HTTP)
# - Viewer connects to LiveKit (browser-side)
# - Speech is sent via LiveKit data channel command events:
#     avatar.interrupt -> wait -> avatar.speak_text
# - Optional: publish microphone audio (no camera)
# =========================================================

st.set_page_config(page_title="Avatharam 3.0 – LiveAvatar", layout="wide")

# ---------------- Secrets (exact casing/spelling) ----------------
HEYGEN_API_KEY = st.secrets["HeyGen"]["heygen_api_key"]
LIVEAVATAR_CONTEXT_ID = st.secrets.get("LiveAvatar", {}).get("context_id", "")

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

# ---------------- Session state ----------------
if "logs" not in st.session_state:
    st.session_state.logs = []
if "live_session" not in st.session_state:
    st.session_state.live_session = None
if "keepalive_last" not in st.session_state:
    st.session_state.keepalive_last = None
if "last_error" not in st.session_state:
    st.session_state.last_error = ""
if "speak_nonce" not in st.session_state:
    st.session_state.speak_nonce = 0

ss = st.session_state


# ---------------- Logger ----------------
def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    ss.logs.append(f"[{ts}] {msg}")


def _clear_error():
    ss.last_error = ""


# ---------------- HTTP helpers ----------------
def _http_post_xapi(url: str, payload: dict, timeout: int = 60) -> dict:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "X-API-KEY": HEYGEN_API_KEY,
    }
    _log(f"POST {url} payload={payload}")
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    _log(f"HTTP {r.status_code} from {url}")
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:1200]}
    if not r.ok:
        _log(f"ERROR body: {str(data)[:2000]}")
        r.raise_for_status()
    return data


def _http_post_bearer(url: str, bearer_token: str, payload: dict, timeout: int = 60) -> dict:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {bearer_token}",
    }
    _log(f"POST {url} (Bearer) payload={payload}")
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    _log(f"HTTP {r.status_code} from {url}")
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:1200]}
    if not r.ok:
        _log(f"ERROR body: {str(data)[:2000]}")
        r.raise_for_status()
    return data


# ---------------- LiveAvatar REST calls ----------------
def create_session_token_full_mode() -> dict:
    # IMPORTANT: omit context_id if empty, otherwise 422
    persona = {"voice_id": FIXED_AVATAR["voice_id"], "language": "en"}
    if isinstance(LIVEAVATAR_CONTEXT_ID, str) and LIVEAVATAR_CONTEXT_ID.strip():
        persona["context_id"] = LIVEAVATAR_CONTEXT_ID.strip()

    payload = {
        "mode": "FULL",
        "avatar_id": FIXED_AVATAR["avatar_id"],
        "avatar_persona": persona,
    }

    body = _http_post_xapi(EP_SESS_TOKEN, payload)
    data = body.get("data") or {}
    sid = data.get("session_id")
    stok = data.get("session_token")

    if not sid or not stok:
        raise RuntimeError(f"Missing session_id/session_token in response: {body}")

    _log(f"Token OK. session_id={sid} token_len={len(stok)}")
    return {"session_id": sid, "session_token": stok, "raw": body}


def start_session(session_id: str, session_token: str) -> dict:
    body = _http_post_bearer(EP_SESS_START, session_token, {"session_id": session_id})
    data = body.get("data") or {}
    livekit_url = data.get("livekit_url")
    livekit_client_token = data.get("livekit_client_token")

    if not livekit_url or not livekit_client_token:
        raise RuntimeError(f"Missing LiveKit connection info from /sessions/start: {body}")

    _log(f"Start OK. livekit_url={livekit_url} lk_token_len={len(livekit_client_token)}")
    return {
        "session_id": data.get("session_id", session_id),
        "livekit_url": livekit_url,
        "livekit_client_token": livekit_client_token,
        "raw": body,
    }


def stop_session(session_id: str, session_token: str) -> dict:
    return _http_post_bearer(EP_SESS_STOP, session_token, {"session_id": session_id})


def keep_alive(session_id: str, session_token: str) -> dict:
    return _http_post_bearer(EP_SESS_KEEPALIVE, session_token, {"session_id": session_id})


# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Avatharam Control Panel")

    st.markdown("### Session")
    c1, c2 = st.columns(2)
    with c1:
        start_clicked = st.button("Start session", use_container_width=True)
    with c2:
        stop_clicked = st.button("Stop session", use_container_width=True)

    keep_clicked = st.button("Keep-alive ping", use_container_width=True)

    st.markdown("### Instruction text")
    speech_text = st.text_area("Text to send (for speak_text)", value="Hello", height=100)

    st.markdown("### Speak")
    publish_mic = st.checkbox("Publish microphone (audio only)", value=True)
    interrupt_before = st.checkbox("Interrupt before speak_text", value=True)
    delay_ms = st.number_input("Interrupt delay (ms)", min_value=0, max_value=5000, value=500, step=50)

    speak_clicked = st.button("Send text to avatar", type="primary", use_container_width=True)

    st.markdown("### Debug")
    show_debug = st.checkbox("Show debug logs", value=True)

    if ss.last_error:
        st.error(ss.last_error)


# ---------------- Actions ----------------
if start_clicked:
    _clear_error()
    try:
        _log("Start session clicked.")
        token_info = create_session_token_full_mode()
        sid = token_info["session_id"]
        stok = token_info["session_token"]

        sess = start_session(sid, stok)
        sess["session_token"] = stok  # bearer for keep/stop
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
        resp = stop_session(ss.live_session["session_id"], ss.live_session["session_token"])
        _log(f"Stop OK. resp_code={resp.get('code')} message={resp.get('message')}")
        ss.live_session = None
    except Exception as e:
        ss.last_error = f"Failed to stop session: {e}"
        _log(ss.last_error)

if keep_clicked:
    _clear_error()
    try:
        if not ss.live_session:
            raise RuntimeError("No active session.")
        _log("Keep-alive clicked.")
        resp = keep_alive(ss.live_session["session_id"], ss.live_session["session_token"])
        ss.keepalive_last = resp
        _log(f"Keep-alive response: {resp}")
    except Exception as e:
        ss.last_error = f"Keep-alive failed: {e}"
        _log(ss.last_error)

if speak_clicked:
    ss.speak_nonce += 1
    _log(f"Send-text clicked. nonce={ss.speak_nonce} chars={len(speech_text)}")


# ---------------- Main UI ----------------
st.title("Avatharam 3.0 – LiveAvatar")
st.caption("REST starts the session; LiveKit (iframe) renders avatar and sends command events.")

left, right = st.columns([1.2, 0.8], gap="large")

with left:
    if ss.live_session:
        st.success("Session started.")
        st.json(
            {
                "session_id": ss.live_session["session_id"],
                "livekit_url": ss.live_session["livekit_url"],
                "avatar": FIXED_AVATAR["name"],
                "context_id": LIVEAVATAR_CONTEXT_ID or "(none/omitted)",
            }
        )

        if ss.keepalive_last:
            st.markdown("**Last keep-alive response:**")
            st.json(ss.keepalive_last)

        viewer_file = Path("viewer.html")
        if not viewer_file.exists():
            st.error("viewer.html not found in project root.")
        else:
            tmpl = viewer_file.read_text(encoding="utf-8")

            # IMPORTANT: viewer expects __SPEAK_PAYLOAD_JSON__
            payload = {
                "nonce": ss.speak_nonce,
                "text": speech_text,
                "interrupt_before_speak": bool(interrupt_before),
                "interrupt_delay_ms": int(delay_ms),
                "publish_microphone": bool(publish_mic),
                "connect_delay_ms": 500,  # your requested delay after initialization
            }

            html = (
                tmpl.replace("__AVATAR_NAME__", FIXED_AVATAR["name"])
                .replace("__LIVEKIT_URL__", ss.live_session["livekit_url"])
                .replace("__LIVEKIT_TOKEN__", ss.live_session["livekit_client_token"])
                .replace("__SPEAK_PAYLOAD_JSON__", json.dumps(payload))
            )

            # Streamlit versions may not support key= here
            components.html(html, height=720, scrolling=False)

    else:
        st.info("No active session. Start one from the sidebar.")
        st.image(FIXED_AVATAR["preview_url"], caption="June HR preview")

with right:
    st.subheader("Notes / Debug")
    st.markdown(
        """
**If the avatar stays black + “SDK: checking…”**
- It almost always means the viewer JS did not run (syntax error / placeholder mismatch / blocked scripts).
- This version fixes the most common mismatch:
  `__SPEAK_PAYLOAD_JSON__` must be replaced (not `__PAYLOAD_JSON__`).

**If /sessions/token returns 422**
- Do NOT send `context_id=""` or `context_id=null`. Omit the field entirely.
"""
    )
    if show_debug:
        st.markdown("### App log")
        st.code("\n".join(ss.logs[-500:]) if ss.logs else "(no logs yet)")
