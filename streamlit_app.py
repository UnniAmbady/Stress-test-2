import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import streamlit as st
import streamlit.components.v1 as components

# =========================
# CONFIG
# =========================
HEYGEN_API_BASE = "https://api.heygen.com/v1"
UA = "Avatharam-CustomMode/1.1 (+streamlit)"

API_NEW_SESSION = f"{HEYGEN_API_BASE}/streaming.new"
API_START_SESSION = f"{HEYGEN_API_BASE}/streaming.start"      # (called from viewer, not backend)
API_TASK = f"{HEYGEN_API_BASE}/streaming.task"                # (called from viewer)
API_INTERRUPT = f"{HEYGEN_API_BASE}/streaming.interrupt"
API_KEEP_ALIVE = f"{HEYGEN_API_BASE}/streaming.keep_alive"
API_STOP = f"{HEYGEN_API_BASE}/streaming.stop"

# Custom-mode avatar_id is typically the "public id" string (e.g. June_HR_public),
# not the LiveAvatar UUID you used in FULL mode.
DEFAULT_AVATAR_ID = "June_HR_public"
# For public avatars, voice ids are often hex-like (from your Public AVATAR.json),
# not the UUID voice id you used in LiveAvatar.
DEFAULT_VOICE_ID = "68dedac41a9f46a6a4271a95c733823c"
DEFAULT_LANGUAGE = "en"

# =========================
# HELPERS
# =========================
def now_ts() -> str:
    return time.strftime("%H:%M:%S")

def log(msg: str) -> None:
    st.session_state.app_log.append(f"[{now_ts()}] {msg}")

def get_secret(path: list[str], default: str = "") -> str:
    cur: Any = st.secrets
    try:
        for k in path:
            cur = cur[k]
        return str(cur)
    except Exception:
        return default

def headers_x_api_key(api_key: str) -> Dict[str, str]:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "X-Api-Key": api_key,
        "User-Agent": UA,
    }

def headers_bearer(api_key: str) -> Dict[str, str]:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": UA,
    }

def post_json(url: str, api_key: str, payload: Dict[str, Any], auth_mode: str = "x-api-key", timeout: int = 45) -> Dict[str, Any]:
    log(f"POST {url} payload_keys={list(payload.keys())}")

    hdrs = headers_x_api_key(api_key) if auth_mode == "x-api-key" else headers_bearer(api_key)
    r = requests.post(url, headers=hdrs, json=payload, timeout=timeout)

    log(f"HTTP {r.status_code} from {url}")
    body = (r.text or "").strip()
    if body:
        log(f"Resp body (first 700 chars): {body[:700]}")

    r.raise_for_status()

    try:
        return r.json()
    except Exception:
        return {"_raw": r.text, "_status": r.status_code}

@dataclass
class HeyGenSession:
    session_id: str
    avatar_id: str
    voice_id: str
    language: str
    sdp_offer_type: str
    sdp_offer_sdp: str
    ice_servers: list

def create_session_new_only(api_key: str, avatar_id: str, voice_id: str, language: str, auth_mode: str) -> HeyGenSession:
    """
    Custom Mode: streaming.new returns a WebRTC SDP offer.
    The SDP answer must be generated in the browser and sent to streaming.start.
    Therefore backend does NOT call streaming.start.
    """
    resp = post_json(
        API_NEW_SESSION,
        api_key,
        {"avatar_id": avatar_id, "voice_id": voice_id, "language": language},
        auth_mode=auth_mode,
    )
    data = resp.get("data") or {}

    sid = data.get("session_id")
    sdp = data.get("sdp") or {}
    offer_type = sdp.get("type")
    offer_sdp = sdp.get("sdp")
    ice_servers = data.get("ice_servers") or data.get("iceServers") or []

    if not (sid and offer_type and offer_sdp):
        raise RuntimeError(f"Missing fields from streaming.new: {resp}")

    log(f"New OK. session_id={sid} offer_type={offer_type} offer_sdp_len={len(offer_sdp)} ice_servers={len(ice_servers)}")

    return HeyGenSession(
        session_id=sid,
        avatar_id=avatar_id,
        voice_id=voice_id,
        language=language,
        sdp_offer_type=offer_type,
        sdp_offer_sdp=offer_sdp,
        ice_servers=ice_servers,
    )

def keep_alive(api_key: str, session_id: str, auth_mode: str) -> Dict[str, Any]:
    return post_json(API_KEEP_ALIVE, api_key, {"session_id": session_id}, auth_mode=auth_mode)

def stop_session(api_key: str, session_id: str, auth_mode: str) -> Dict[str, Any]:
    return post_json(API_STOP, api_key, {"session_id": session_id}, auth_mode=auth_mode)

def interrupt(api_key: str, session_id: str, auth_mode: str) -> Dict[str, Any]:
    return post_json(API_INTERRUPT, api_key, {"session_id": session_id}, auth_mode=auth_mode)

# =========================
# STREAMLIT UI
# =========================
st.set_page_config(page_title="Avatharam 3.0 – HeyGen Custom Mode", layout="wide")

if "app_log" not in st.session_state:
    st.session_state.app_log = []
if "session" not in st.session_state:
    st.session_state.session: Optional[HeyGenSession] = None
if "viewer_nonce" not in st.session_state:
    st.session_state.viewer_nonce = 0
if "last_keepalive" not in st.session_state:
    st.session_state.last_keepalive = None

st.title("Avatharam 3.0 – HeyGen Custom Mode")
st.caption("Deterministic TTS via `streaming.task` with `task_type=repeat` (WebRTC SDP offer/answer in browser).")

api_key = get_secret(["HeyGen", "heygen_api_key"], "")
if not api_key:
    st.error('Missing HeyGen key. Add to secrets: [HeyGen] heygen_api_key = "sk_..."')
    st.stop()

with st.sidebar:
    st.header("Avatharam Control Panel")

    st.subheader("Auth")
    auth_mode = st.radio("Header mode", ["x-api-key", "bearer"], index=0, horizontal=True)

    st.subheader("Session")
    avatar_id = st.text_input("Avatar ID", DEFAULT_AVATAR_ID).strip()
    voice_id = st.text_input("Voice ID", DEFAULT_VOICE_ID).strip()
    language = st.text_input("Language", DEFAULT_LANGUAGE).strip()

    c1, c2 = st.columns(2)
    with c1:
        start_btn = st.button("Start session", use_container_width=True)
    with c2:
        stop_btn = st.button("Stop session", use_container_width=True)

    ka_btn = st.button("Keep-alive ping", use_container_width=True)

    st.subheader("Text to read (repeat)")
    try:
        default_text = Path("Speech.txt").read_text(encoding="utf-8")
    except Exception:
        default_text = "Hello"
    text_to_send = st.text_area("Speech.txt content (editable)", value=default_text, height=220)

    st.subheader("Optional: Interrupt")
    do_interrupt = st.checkbox("Send interrupt (REST) before Speak", value=False)
    interrupt_delay_ms = st.slider("Interrupt delay (ms)", 0, 2000, 500, 50)

left, right = st.columns([2, 1], gap="large")

with left:
    st.subheader("Avatar")

    if start_btn:
        try:
            log("Start session clicked.")
            st.session_state.session = create_session_new_only(api_key, avatar_id, voice_id, language, auth_mode)
            st.session_state.viewer_nonce += 1
            log("Session created (offer received). Viewer will complete WebRTC and call streaming.start.")
            st.success("Session created. Viewer should connect in a few seconds.")
        except Exception as e:
            st.session_state.session = None
            log(f"ERROR starting session: {e}")
            st.error(f"Failed to start session: {e}")

    if stop_btn and st.session_state.session:
        try:
            log("Stop session clicked.")
            resp = stop_session(api_key, st.session_state.session.session_id, auth_mode)
            log(f"Stop resp: {json.dumps(resp)[:500]}")
            st.session_state.session = None
            st.session_state.viewer_nonce += 1
            st.success("Session stopped.")
        except Exception as e:
            log(f"ERROR stopping session: {e}")
            st.error(f"Failed to stop session: {e}")

    if ka_btn and st.session_state.session:
        try:
            log("Keep-alive clicked.")
            resp = keep_alive(api_key, st.session_state.session.session_id, auth_mode)
            st.session_state.last_keepalive = resp
            log(f"Keep-alive: code={resp.get('code')} message={resp.get('message')}")
            st.success("Keep-alive sent.")
        except Exception as e:
            log(f"ERROR keep-alive: {e}")
            st.error(f"Keep-alive failed: {e}")

    # Viewer
    if st.session_state.session:
        sess = st.session_state.session

        # NOTE: For this stress-test harness we include the API key in the viewer payload so it can call
        # streaming.start + streaming.task directly. That’s the simplest way to unblock the SDP answer flow.
        payload = {
            "nonce": st.session_state.viewer_nonce,
            "api_base": HEYGEN_API_BASE,
            "auth_mode": auth_mode,
            "api_key": api_key,
            "session_id": sess.session_id,
            "avatar_name": sess.avatar_id,
            "offer": {"type": sess.sdp_offer_type, "sdp": sess.sdp_offer_sdp},
            "ice_servers": sess.ice_servers,
            "connect_delay_ms": 500,
            "default_text": (text_to_send or "").strip(),
            "do_interrupt": bool(do_interrupt),
            "interrupt_delay_ms": int(interrupt_delay_ms),
        }

        viewer_path = Path("viewer.html")
        if not viewer_path.exists():
            st.error("viewer.html not found in project root.")
        else:
            template = viewer_path.read_text(encoding="utf-8", errors="ignore")
            html = template.replace("/*__PAYLOAD__*/", json.dumps(payload))
            components.html(html, height=780, scrolling=False)
    else:
        st.info("Start a session to load the avatar viewer.")

with right:
    st.subheader("App log")
    st.code("\n".join(st.session_state.app_log[-450:]), language="text")

    if st.session_state.last_keepalive is not None:
        st.subheader("Last keep-alive response")
        st.json(st.session_state.last_keepalive)
