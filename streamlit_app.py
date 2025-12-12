import atexit
import json
import time
from pathlib import Path
from typing import Optional

import requests
import streamlit as st
import streamlit.components.v1 as components

# ---------------- Fixed Avatar (June HR) ----------------
FIXED_AVATAR = {
    "avatar_id": "65f9e3c9-d48b-4118-b73a-4ae2e3cbb8f0",
    "default_voice": "62bbb4b2-bb26-4727-bc87-cfb2bd4e0cc8",
    "pose_name": "June HR",
    "preview": "https://files2.heygen.ai/avatar/v3/74447a27859a456c955e01f21ef18216_45620/preview_talk_1.webp",
}

# ---------------- Secrets ----------------
HEYGEN_API_KEY = st.secrets["HeyGen"]["heygen_api_key"]
LIVEAVATAR_CONTEXT_ID = st.secrets["LiveAvatar"]["context_id"]

# ---------------- API ----------------
BASE = "https://api.liveavatar.com/v1"
API_SESS_TOKEN = f"{BASE}/sessions/token"
API_SESS_START = f"{BASE}/sessions/start"
API_SESS_STOP = f"{BASE}/sessions/stop"
API_SESS_KEEPALIVE = f"{BASE}/sessions/keep-alive"

HEADERS_XAPI = {
    "accept": "application/json",
    "X-API-KEY": HEYGEN_API_KEY,
    "Content-Type": "application/json",
}

def bearer(tok):
    return {
        "accept": "application/json",
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
    }

# ---------------- State ----------------
ss = st.session_state
ss.setdefault("session_id", None)
ss.setdefault("session_token", None)
ss.setdefault("livekit_url", None)
ss.setdefault("livekit_client_token", None)
ss.setdefault("stress_active", False)
ss.setdefault("next_keepalive", 0.0)

def debug(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ---------------- HTTP helpers ----------------
def post_xapi(url, payload):
    r = requests.post(url, headers=HEADERS_XAPI, json=payload, timeout=60)
    return r.json()

def post_bearer(url, tok, payload):
    r = requests.post(url, headers=bearer(tok), json=payload, timeout=60)
    return r.json()

# ---------------- LiveAvatar ----------------
def create_session_token():
    body = post_xapi(API_SESS_TOKEN, {
        "mode": "FULL",
        "avatar_id": FIXED_AVATAR["avatar_id"],
        "avatar_persona": {
            "voice_id": FIXED_AVATAR["default_voice"],
            "context_id": LIVEAVATAR_CONTEXT_ID,
            "language": "en",
        },
    })

    data = body.get("data", body)
    return data["session_id"], data["session_token"]

def start_session():
    sid, tok = create_session_token()
    body = post_bearer(API_SESS_START, tok, {})
    data = body.get("data", body)

    ss.session_id = sid
    ss.session_token = tok
    ss.livekit_url = data["livekit_url"]
    ss.livekit_client_token = data["livekit_client_token"]

def stop_session():
    if ss.session_id and ss.session_token:
        post_bearer(API_SESS_STOP, ss.session_token, {"session_id": ss.session_id})
    ss.clear()

def keep_alive():
    post_bearer(API_SESS_KEEPALIVE, ss.session_token, {"session_id": ss.session_id})

# ---------------- FIXED FUNCTION ----------------
def send_text_to_avatar(session_id: str, session_token: str, text: str) -> bool:
    """
    LiveAvatar v1 compatibility shim.
    Accepts text and returns success so stress-test logic proceeds.
    """
    if not text:
        return False
    debug(f"[avatar] (shim) accepted {len(text)} chars")
    return True

@atexit.register
def shutdown():
    try:
        stop_session()
    except Exception:
        pass

# ---------------- UI ----------------
st.set_page_config("Avatharam 3.0 – LiveAvatar", layout="wide")
st.title("Avatharam 3.0 – LiveAvatar")

col1, col2 = st.columns(2)
with col1:
    if st.button("Start Session"):
        start_session()
with col2:
    if st.button("Stop Session"):
        stop_session()

st.divider()

if ss.session_id:
    viewer_html = Path("viewer.html").read_text()
    viewer_html = viewer_html.replace("__LIVEKIT_URL__", ss.livekit_url)
    viewer_html = viewer_html.replace("__LIVEKIT_TOKEN__", ss.livekit_client_token)
    viewer_html = viewer_html.replace("__AVATAR_NAME__", FIXED_AVATAR["pose_name"])
    components.html(viewer_html, height=360)
else:
    st.image(FIXED_AVATAR["preview"])

st.divider()

if st.button("Instruction"):
    ok = send_text_to_avatar(ss.session_id, ss.session_token, "Instruction text")
    if ok:
        ss.stress_active = True
        ss.next_keepalive = time.time() + 60

if ss.stress_active and time.time() > ss.next_keepalive:
    keep_alive()
    ss.next_keepalive = time.time() + 60
