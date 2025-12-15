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
UA = "Avatharam-CustomMode/1.0 (+streamlit)"

API_NEW_SESSION = f"{HEYGEN_API_BASE}/streaming.new"
API_START_SESSION = f"{HEYGEN_API_BASE}/streaming.start"
API_TASK = f"{HEYGEN_API_BASE}/streaming.task"
API_INTERRUPT = f"{HEYGEN_API_BASE}/streaming.interrupt"
API_KEEP_ALIVE = f"{HEYGEN_API_BASE}/streaming.keep_alive"
API_STOP = f"{HEYGEN_API_BASE}/streaming.stop"

DEFAULT_AVATAR_ID = "65f9e3c9-d48b-4118-b73a-4ae2e3cbb8f0"  # June HR
DEFAULT_VOICE_ID = "62bbb4b2-bb26-4727-bc87-cfb2bd4e0cc8"
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

def looks_like_uuidish(s: str) -> bool:
    return ("-" in s) and (len(s) <= 40)

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
    livekit_url: str
    livekit_token: str
    avatar_id: str
    voice_id: str
    language: str

def create_session(api_key: str, avatar_id: str, voice_id: str, language: str, auth_mode: str) -> HeyGenSession:
    # 1) create
    resp = post_json(
        API_NEW_SESSION,
        api_key,
        {"avatar_id": "June_HR_public", "voice_id": voice_id, "language": language},
        auth_mode=auth_mode,
    )
    data = resp.get("data") or {}

    sid = data.get("session_id")
    lk_url = data.get("url") or data.get("livekit_url")
    lk_token = data.get("access_token") or data.get("livekit_client_token")

    if not (sid and lk_url and lk_token):
        raise RuntimeError(f"Missing fields from streaming.new: {resp}")

    log(f"New OK. session_id={sid} livekit_url={lk_url} lk_token_len={len(lk_token)}")

    # 2) start
    start_resp = post_json(API_START_SESSION, api_key, {"session_id": sid}, auth_mode=auth_mode)
    log(f"Start resp: code={start_resp.get('code')} message={start_resp.get('message')}")

    return HeyGenSession(
        session_id=sid,
        livekit_url=lk_url,
        livekit_token=lk_token,
        avatar_id=avatar_id,
        voice_id=voice_id,
        language=language,
    )

def keep_alive(api_key: str, session_id: str, auth_mode: str) -> Dict[str, Any]:
    return post_json(API_KEEP_ALIVE, api_key, {"session_id": session_id}, auth_mode=auth_mode)

def stop_session(api_key: str, session_id: str, auth_mode: str) -> Dict[str, Any]:
    return post_json(API_STOP, api_key, {"session_id": session_id}, auth_mode=auth_mode)

def interrupt(api_key: str, session_id: str, auth_mode: str) -> Dict[str, Any]:
    return post_json(API_INTERRUPT, api_key, {"session_id": session_id}, auth_mode=auth_mode)

def speak_repeat(api_key: str, session_id: str, text: str, auth_mode: str) -> Dict[str, Any]:
    # Deterministic narration per HeyGen support: task_type=REPEAT
    payload = {
        "session_id": session_id,
        "text": text,
        "task_type": "repeat",
        "task_mode": "sync",
    }
    return post_json(API_TASK, api_key, payload, auth_mode=auth_mode)

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
st.caption("Deterministic TTS via `streaming.task` with `task_type=repeat`.")

api_key = get_secret(["HeyGen", "heygen_api_key"], "")
if not api_key:
    st.error('Missing HeyGen key. Add to secrets: [HeyGen] heygen_api_key = "..."')
    st.stop()

if looks_like_uuidish(api_key):
    st.warning(
        "Your [HeyGen].heygen_api_key looks UUID-like. "
        "That is usually a LiveAvatar key. Custom Mode needs the HeyGen API key from HeyGen."
    )

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
    text_to_send = st.text_area("Speech.txt content (editable)", value=default_text, height=260)

    st.subheader("Speak")
    do_interrupt = st.checkbox("Interrupt before speak", value=False)
    interrupt_delay_ms = st.slider("Interrupt delay (ms)", 0, 2000, 500, 50)

    send_btn = st.button("Send text (repeat)", use_container_width=True)

left, right = st.columns([2, 1], gap="large")

with left:
    st.subheader("Avatar")

    if start_btn:
        try:
            log("Start session clicked.")
            st.session_state.session = create_session(api_key, avatar_id, voice_id, language, auth_mode)
            st.session_state.viewer_nonce += 1
            log("Session started successfully.")
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
        except Exception as e:
            log(f"ERROR stopping session: {e}")
            st.error(f"Failed to stop session: {e}")

    if ka_btn and st.session_state.session:
        try:
            log("Keep-alive clicked.")
            resp = keep_alive(api_key, st.session_state.session.session_id, auth_mode)
            st.session_state.last_keepalive = resp
            log(f"Keep-alive: code={resp.get('code')} message={resp.get('message')}")
        except Exception as e:
            log(f"ERROR keep-alive: {e}")
            st.error(f"Keep-alive failed: {e}")

    if send_btn and st.session_state.session:
        try:
            txt = (text_to_send or "").strip()
            if not txt:
                st.warning("Nothing to send.")
            else:
                log(f"Send text clicked. chars={len(txt)}")
                if do_interrupt:
                    log("Interrupt…")
                    try:
                        interrupt(api_key, st.session_state.session.session_id, auth_mode)
                        log("Interrupt sent.")
                    except Exception as ie:
                        log(f"Interrupt failed (continuing): {ie}")
                    if interrupt_delay_ms > 0:
                        time.sleep(interrupt_delay_ms / 1000.0)

                resp = speak_repeat(api_key, st.session_state.session.session_id, txt, auth_mode)
                log(f"Task resp: {json.dumps(resp)[:700]}")
        except Exception as e:
            log(f"ERROR speak_repeat: {e}")
            st.error(f"Send text failed: {e}")

    # Viewer
    if st.session_state.session:
        sess = st.session_state.session
        payload = {
            "nonce": st.session_state.viewer_nonce,
            "session_id": sess.session_id,
            "livekit_url": sess.livekit_url,
            "livekit_token": sess.livekit_token,
            "connect_delay_ms": 500,
            "auto_enable_mic": False,
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
    st.code("\n".join(st.session_state.app_log[-400:]), language="text")

    if st.session_state.last_keepalive is not None:
        st.subheader("Last keep-alive response")
        st.json(st.session_state.last_keepalive)
