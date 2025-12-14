import json
import time
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Avatharam 3.0 – LiveAvatar", layout="wide")

API_BASE = "https://api.liveavatar.com/v1"
API_TOKEN = f"{API_BASE}/sessions/token"
API_START = f"{API_BASE}/sessions/start"
API_STOP = f"{API_BASE}/sessions/stop"
API_KEEPALIVE = f"{API_BASE}/sessions/keep-alive"

def _headers(api_key: str) -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "X-API-KEY": api_key,
    }

def post_json(url: str, api_key: str, payload: dict) -> dict:
    r = requests.post(url, headers=_headers(api_key), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def build_viewer_html(template_path: str, avatar_name: str, livekit_url: str, livekit_token: str, speak_text: str, nonce: int) -> str:
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    payload = {"text": speak_text, "nonce": nonce}
    html = html.replace("__AVATAR_NAME__", avatar_name)
    html = html.replace("__LIVEKIT_URL__", livekit_url)
    html = html.replace("__LIVEKIT_TOKEN__", livekit_token)
    html = html.replace("__SPEAK_PAYLOAD_JSON__", json.dumps(payload))
    return html

# ---- Secrets ----
heygen_api_key = st.secrets.get("HeyGen", {}).get("heygen_api_key", "")
context_id = st.secrets.get("LiveAvatar", {}).get("context_id", "")

# ---- Session state ----
ss = st.session_state
ss.setdefault("session_id", None)
ss.setdefault("session_token", None)
ss.setdefault("livekit_url", None)
ss.setdefault("livekit_token", None)
ss.setdefault("avatar_name", "June HR")
ss.setdefault("last_start_resp", None)
ss.setdefault("last_keepalive_resp", None)
ss.setdefault("nonce", 0)

# ---- UI ----
st.title("Avatharam 3.0 – LiveAvatar")
st.caption("Stress-test harness for HeyGen LiveAvatar Sessions API (FULL mode) with LiveKit command events + mic publish.")

with st.sidebar:
    st.header("Avatharam Control Panel")

    if not heygen_api_key:
        st.error("Missing [HeyGen].heygen_api_key in Streamlit secrets.")
    if not context_id:
        st.warning("Missing [LiveAvatar].context_id in Streamlit secrets.")

    st.subheader("Session")

    col1, col2 = st.columns(2)
    with col1:
        start_btn = st.button("Start session", use_container_width=True)
    with col2:
        stop_btn = st.button("Stop session", use_container_width=True)

    keep_btn = st.button("Keep-alive ping", use_container_width=True)

    st.subheader("Instruction text")
    speak_text = st.text_area("Speech.txt content (editable)", value="Hello", height=120)

    st.subheader("Debug")
    st.write("**context_id**:", context_id)
    st.write("**session_id**:", ss.session_id)
    st.write("**nonce**:", ss.nonce)

    if ss.last_start_resp:
        st.write("**Last /sessions/start response:**")
        st.code(json.dumps(ss.last_start_resp, indent=2), language="json")

    if ss.last_keepalive_resp:
        st.write("**Last /keep-alive response:**")
        st.code(json.dumps(ss.last_keepalive_resp, indent=2), language="json")

# ---- Actions ----
if start_btn and heygen_api_key:
    try:
        token_resp = post_json(API_TOKEN, heygen_api_key, {"mode": "FULL"})
        data = token_resp.get("data", {})
        ss.session_id = data.get("session_id")
        ss.session_token = data.get("session_token")

        if not ss.session_id or not ss.session_token:
            st.error(f"Token response missing session_id/session_token: {token_resp}")
        else:
            start_payload = {
                "session_id": ss.session_id,
                "session_token": ss.session_token,
                "start_session_data": {
                    "mode": "FULL",
                    "avatar_id": "65f9e3c9-d48b-4118-b73a-4ae2e3cbb8f0",  # June HR
                    "avatar_persona": {
                        "context_id": context_id,
                        "language": "en",
                    }
                }
            }
            start_resp = post_json(API_START, heygen_api_key, start_payload)
            ss.last_start_resp = start_resp

            sdata = start_resp.get("data", {})
            ss.livekit_url = sdata.get("livekit_url")
            ss.livekit_token = sdata.get("livekit_client_token")
            ss.avatar_name = "June HR"

            if not ss.livekit_url or not ss.livekit_token:
                st.error(f"Missing livekit_url/livekit_client_token in start response: {start_resp}")
            else:
                st.success("Session started.")
                ss.nonce = 0  # viewer will boot with nonce 0

    except requests.HTTPError as e:
        st.error(f"Failed to start session: {e}")
    except Exception as e:
        st.error(f"Unexpected error: {e}")

if keep_btn and heygen_api_key and ss.session_id and ss.session_token:
    try:
        keep_payload = {"session_id": ss.session_id, "session_token": ss.session_token}
        keep_resp = post_json(API_KEEPALIVE, heygen_api_key, keep_payload)
        ss.last_keepalive_resp = keep_resp
        st.info("Keep-alive sent.")
    except Exception as e:
        st.error(f"Keep-alive failed: {e}")

if stop_btn and heygen_api_key and ss.session_id and ss.session_token:
    try:
        stop_payload = {"session_id": ss.session_id, "session_token": ss.session_token}
        stop_resp = post_json(API_STOP, heygen_api_key, stop_payload)
        st.info(f"Stop response: {stop_resp}")
        ss.session_id = None
        ss.session_token = None
        ss.livekit_url = None
        ss.livekit_token = None
        ss.last_start_resp = None
        ss.last_keepalive_resp = None
        ss.nonce = 0
    except Exception as e:
        st.error(f"Stop failed: {e}")

# ---- Main viewer area ----
left, right = st.columns([2, 1])

with left:
    st.subheader("Avatar")
    if ss.livekit_url and ss.livekit_token:
        # bump nonce on every rerun when text changes, to force iframe refresh without using components.html(key=...)
        # (Streamlit 1.38 IframeMixin._html() does not accept key)
        ss.nonce += 1
        html = build_viewer_html(
            template_path="viewer.html",
            avatar_name=ss.avatar_name,
            livekit_url=ss.livekit_url,
            livekit_token=ss.livekit_token,
            speak_text=speak_text,
            nonce=ss.nonce,
        )
        components.html(html, height=590, scrolling=False)
    else:
        st.warning("Start a session first.")

with right:
    st.subheader("Notes")
    st.markdown(
        """
**What this build does**
- Starts LiveAvatar FULL session via REST.
- Viewer connects via LiveKit and **shows avatar video/audio**.
- Viewer has **Enable Mic** (publishes your microphone audio).
- Viewer **Speak** button sends: `avatar.interrupt` → wait 500ms → `avatar.speak_text`.
- Viewer logs:
  - SDK discovery
  - TrackSubscribed (video/audio)
  - DataReceived events
  - Mic publish status
  - Command sends
"""
    )

