# streamlit_app.py
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests
import streamlit as st
import streamlit.components.v1 as components

# =========================
# CONFIG / CONSTANTS
# =========================
HEYGEN_API_BASE = "https://api.heygen.com/v1"
UA = "Avatharam-CustomMode/1.0 (+streamlit)"

# HeyGen Streaming API endpoints (Interactive Avatar / Streaming)
API_NEW_SESSION = f"{HEYGEN_API_BASE}/streaming.new"
API_START_SESSION = f"{HEYGEN_API_BASE}/streaming.start"
API_SEND_TASK = f"{HEYGEN_API_BASE}/streaming.task"
API_INTERRUPT = f"{HEYGEN_API_BASE}/streaming.interrupt"
API_KEEP_ALIVE = f"{HEYGEN_API_BASE}/streaming.keep_alive"
API_CLOSE_SESSION = f"{HEYGEN_API_BASE}/streaming.stop"

DEFAULT_AVATAR_ID = "65f9e3c9-d48b-4118-b73a-4ae2e3cbb8f0"  # June HR (example)
DEFAULT_VOICE_ID = "62bbb4b2-bb26-4727-bc87-cfb2bd4e0cc8"
DEFAULT_LANGUAGE = "en"

# =========================
# HELPERS
# =========================
def _now() -> str:
    return time.strftime("%H:%M:%S")

def log(msg: str) -> None:
    st.session_state.app_log.append(f"[{_now()}] {msg}")

def get_secret(path: Tuple[str, str], default: Optional[str] = None) -> Optional[str]:
    section, key = path
    try:
        return st.secrets[section][key]
    except Exception:
        return default

def heygen_headers(api_key: str) -> Dict[str, str]:
    # HeyGen docs use "X-Api-Key" header for REST API
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "X-Api-Key": api_key,
        "User-Agent": UA,
    }

def _post(url: str, api_key: str, payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    log(f"POST {url} payload_keys={list(payload.keys())}")
    r = requests.post(url, headers=heygen_headers(api_key), json=payload, timeout=timeout)
    log(f"HTTP {r.status_code} from {url}")
    r.raise_for_status()
    return r.json()

def _safe_get(d: Dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur

# =========================
# STREAMING API WRAPPERS
# =========================
@dataclass
class HeyGenSession:
    session_id: str
    # LiveKit connection info
    livekit_url: str
    livekit_token: str
    # meta
    avatar_id: str
    voice_id: str
    language: str
    # optional KB/context (chat mode), but we use repeat
    knowledge_base_id: Optional[str] = None

def create_session(api_key: str, avatar_id: str, voice_id: str, language: str,
                   knowledge_base_id: Optional[str] = None) -> HeyGenSession:
    """
    Custom/Streaming mode strategy:
    1) streaming.new -> returns session_id + LiveKit connection info
    2) streaming.start -> activates the session
    3) streaming.task(task_type=repeat) -> deterministic TTS
    """
    payload: Dict[str, Any] = {
        "avatar_id": avatar_id,
        "voice_id": voice_id,
        "language": language,
    }
    # Only include knowledge_base_id if user provided; omit otherwise
    if knowledge_base_id:
        payload["knowledge_base_id"] = knowledge_base_id

    resp = _post(API_NEW_SESSION, api_key, payload)
    # Typical response: {"code":1000,"data":{"session_id":"...","url":"wss://...","access_token":"..."},"message":"Success"}
    data = resp.get("data") or {}
    sid = data.get("session_id")
    lk_url = data.get("url") or data.get("livekit_url")
    lk_token = data.get("access_token") or data.get("livekit_client_token")
    if not (sid and lk_url and lk_token):
        raise RuntimeError(f"Missing session/LiveKit fields from streaming.new: {resp}")

    log(f"New OK. session_id={sid} lk_url={lk_url} lk_token_len={len(lk_token)}")
    # Start
    start_payload = {"session_id": sid}
    start_resp = _post(API_START_SESSION, api_key, start_payload)
    log(f"Start response: code={start_resp.get('code')} message={start_resp.get('message')}")
    return HeyGenSession(
        session_id=sid,
        livekit_url=lk_url,
        livekit_token=lk_token,
        avatar_id=avatar_id,
        voice_id=voice_id,
        language=language,
        knowledge_base_id=knowledge_base_id,
    )

def keep_alive(api_key: str, session_id: str) -> Dict[str, Any]:
    resp = _post(API_KEEP_ALIVE, api_key, {"session_id": session_id})
    return resp

def close_session(api_key: str, session_id: str) -> Dict[str, Any]:
    resp = _post(API_CLOSE_SESSION, api_key, {"session_id": session_id})
    return resp

def send_task_repeat(api_key: str, session_id: str, text: str, task_mode: str = "sync") -> Dict[str, Any]:
    # task_type: repeat (deterministic)
    payload = {"session_id": session_id, "text": text, "task_type": "repeat", "task_mode": task_mode}
    resp = _post(API_SEND_TASK, api_key, payload)
    return resp

def interrupt(api_key: str, session_id: str) -> Dict[str, Any]:
    resp = _post(API_INTERRUPT, api_key, {"session_id": session_id})
    return resp

# =========================
# UI / APP
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
st.caption("Stress-test harness for HeyGen Streaming Avatar (Custom mode) using deterministic TTS via task_type=repeat.")

# Secrets
api_key = get_secret(("HeyGen", "heygen_api_key"))
if not api_key:
    st.error('Missing HeyGen API key. Add it in Streamlit secrets as: [HeyGen] heygen_api_key = "..."')
    st.stop()

# Sidebar control panel
with st.sidebar:
    st.header("Avatharam Control Panel")

    st.subheader("Session")
    avatar_id = st.text_input("Avatar ID", value=DEFAULT_AVATAR_ID)
    voice_id = st.text_input("Voice ID", value=DEFAULT_VOICE_ID)
    language = st.text_input("Language", value=DEFAULT_LANGUAGE)
    knowledge_base_id = st.text_input("Knowledge base ID (optional)", value="").strip() or None

    colA, colB = st.columns(2)
    with colA:
        start_btn = st.button("Start session", use_container_width=True)
    with colB:
        stop_btn = st.button("Stop session", use_container_width=True)

    ka_btn = st.button("Keep-alive ping", use_container_width=True)

    st.subheader("Instruction text")
    # Load full Speech.txt (if present) as default body
    default_text = ""
    try:
        default_text = Path("Speech.txt").read_text(encoding="utf-8")
    except Exception:
        default_text = "Hello"

    text_to_send = st.text_area("Text to send (task_type=repeat)", value=default_text, height=220)

    st.subheader("Speak options")
    interrupt_first = st.checkbox("Interrupt before speak (recommended)", value=True)
    interrupt_delay_ms = st.slider("Interrupt delay (ms)", 0, 2000, 500, 50)

    send_btn = st.button("Send text (repeat)", use_container_width=True)

# Main layout
left, right = st.columns([2, 1], gap="large")

with left:
    st.subheader("Avatar")

    if start_btn:
        try:
            log("Start session clicked.")
            st.session_state.session = create_session(
                api_key=api_key,
                avatar_id=avatar_id.strip(),
                voice_id=voice_id.strip(),
                language=language.strip(),
                knowledge_base_id=knowledge_base_id,
            )
            st.session_state.viewer_nonce += 1
            log("Session started successfully.")
        except Exception as e:
            st.session_state.session = None
            log(f"ERROR starting session: {e}")
            st.error(f"Failed to start session: {e}")

    if stop_btn and st.session_state.session:
        try:
            log("Stop session clicked.")
            resp = close_session(api_key, st.session_state.session.session_id)
            log(f"Session stopped. code={resp.get('code')} message={resp.get('message')}")
            st.session_state.session = None
            st.session_state.viewer_nonce += 1
        except Exception as e:
            log(f"ERROR stopping session: {e}")
            st.error(f"Failed to stop session: {e}")

    if ka_btn and st.session_state.session:
        try:
            log("Keep-alive clicked.")
            resp = keep_alive(api_key, st.session_state.session.session_id)
            st.session_state.last_keepalive = resp
            log(f"Keep-alive response: code={resp.get('code')} message={resp.get('message')}")
        except Exception as e:
            log(f"ERROR keep-alive: {e}")
            st.error(f"Keep-alive failed: {e}")

    if send_btn and st.session_state.session:
        try:
            txt = (text_to_send or "").strip()
            if not txt:
                st.warning("Nothing to send.")
            else:
                log(f"Send-text clicked. nonce={st.session_state.viewer_nonce} chars={len(txt)}")
                if interrupt_first:
                    try:
                        log("Interrupt before speak…")
                        interrupt(api_key, st.session_state.session.session_id)
                        log("Interrupt sent.")
                    except Exception as ie:
                        log(f"Interrupt failed (continuing): {ie}")
                    if interrupt_delay_ms > 0:
                        time.sleep(interrupt_delay_ms / 1000.0)

                resp = send_task_repeat(api_key, st.session_state.session.session_id, txt, task_mode="sync")
                # Show response in UI
                log(f"Task response: {json.dumps(resp)[:500]}")
        except Exception as e:
            log(f"ERROR send task: {e}")
            st.error(f"Send text failed: {e}")

    # Build viewer HTML (embedded) if we have a session
    if st.session_state.session:
        sess = st.session_state.session
        payload = {
            "nonce": st.session_state.viewer_nonce,
            "session_id": sess.session_id,
            "livekit_url": sess.livekit_url,
            "livekit_token": sess.livekit_token,
            "avatar_name": "June HR",
            "connect_delay_ms": 500,
            # Custom mode: we do NOT need to publish mic by default,
            # but viewer has a button if user wants it.
            "auto_enable_mic": False,
        }
        viewer_path = Path("viewer.html")
        if not viewer_path.exists():
            st.error("viewer.html not found in project root.")
        else:
            html_template = viewer_path.read_text(encoding="utf-8", errors="ignore")
            html = html_template.replace("/*__PAYLOAD__*/", json.dumps(payload))
            # streamlit components.html does NOT accept key= in some versions
            components.html(html, height=780, scrolling=False)
    else:
        st.info("Start a session to load the avatar viewer.")

with right:
    st.subheader("App log")
    st.code("\n".join(st.session_state.app_log[-250:]), language="text")

    if st.session_state.last_keepalive is not None:
        st.subheader("Last keep-alive response")
        st.json(st.session_state.last_keepalive)
