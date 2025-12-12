# Avatharam-3.0 (LiveAvatar v1)
# Stress-test-3.0 – migrated from legacy /streaming.* to LiveAvatar Sessions API

import atexit
import json
import os
import time
from pathlib import Path
from typing import Optional, List

import requests
import streamlit as st
import streamlit.components.v1 as components

# ---------------- Fixed avatar choice ----------------
FIXED_AVATAR = {
    "avatar_id": "June_HR_public",
    "default_voice": "68dedac41a9f46a6a4271a95c733823c",
    "normal_preview": "https://files2.heygen.ai/avatar/v3/74447a27859a456c955e01f21ef18216_45620/preview_talk_1.webp",
    "pose_name": "June HR",
    "status": "ACTIVE",
}

# ---------------- Secrets ----------------
SECRETS = st.secrets if "secrets" in dir(st) else {}
HEYGEN_API_KEY = (
    SECRETS.get("HeyGen", {}).get("heygen_api_key")
    or SECRETS.get("heygen", {}).get("heygen_api_key")
    or os.getenv("HEYGEN_API_KEY")
)
OPENAI_API_KEY = (
    SECRETS.get("openai", {}).get("secret_key")
    or SECRETS.get("OPENAI_API_KEY")
    or os.getenv("OPENAI_API_KEY")
)

if not HEYGEN_API_KEY:
    st.error("Missing HeyGen / LiveAvatar API key in .streamlit/secrets.toml")
    st.stop()

# ---------------- Endpoints (LiveAvatar v1) ----------------
# NOTE: HeyGen's Interactive Avatar API has evolved into the LiveAvatar service.
# This app now talks to the new LiveAvatar Sessions API instead of the legacy
# /streaming.* endpoints.
BASE = "https://api.liveavatar.com/v1"

API_SESS_TOKEN      = f"{BASE}/sessions/token"
API_SESS_START      = f"{BASE}/sessions/start"
API_SESS_STOP       = f"{BASE}/sessions/stop"
API_SESS_KEEPALIVE  = f"{BASE}/sessions/keep-alive"
API_SESS_TRANSCRIPT = f"{BASE}/sessions/{{session_id}}/transcript"

HEADERS_XAPI = {
    "accept": "application/json",
    "X-API-KEY": HEYGEN_API_KEY,
    "Content-Type": "application/json",
}
def _headers_bearer(tok: str):
    return {
        "accept": "application/json",
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
    }

# ---------------- Session State ----------------
ss = st.session_state
ss.setdefault("session_id", None)
ss.setdefault("session_token", None)

# LiveAvatar connection info (replaces legacy offer_sdp / rtc_config)
ss.setdefault("livekit_url", None)
ss.setdefault("livekit_client_token", None)
ss.setdefault("ws_url", None)

# Legacy fields kept for backwards-compatibility (no longer used with LiveAvatar)
ss.setdefault("offer_sdp", None)
ss.setdefault("rtc_config", None)

ss.setdefault("show_sidebar", False)
ss.setdefault("gpt_query", "Hello, welcome.")
ss.setdefault("voice_ready", False)
ss.setdefault("voice_inserted_once", False)
ss.setdefault("bgm_should_play", True)
ss.setdefault("auto_started", False)

# Stress-test memory
ss.setdefault("test_text", "")

# Timer/keepalive state (NEW)
ss.setdefault("stress_active", False)      # set True after Instruction completes
ss.setdefault("next_keepalive_at", 0.0)    # epoch: when to send next keep-alive
ss.setdefault("autorefresh_on", False)     # controls the 2s autorefresh pinger

# ---------------- Debug ----------------
def debug(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ---------------- Load Speech.txt ----------------
DEFAULT_INSTRUCTION = (
    "To speak to me, press the speak button, pause a second and then speak. "
    "Once you have spoken press the [Stop] button"
)

def _read_speech_txt() -> Optional[str]:
    """Assumes Speech.txt is in the current working directory."""
    p = Path("Speech.txt")
    if not p.exists():
        return None
    try:
        txt = p.read_text(encoding="utf-8")
    except Exception:
        return None
    txt = (txt or "").strip()
    return txt or None

speech_txt = _read_speech_txt()
if speech_txt and not ss.test_text:
    ss.test_text = speech_txt

# ---------------- HTTP helpers ----------------
def _post_xapi(url, payload=None):
    r = requests.post(url, headers=HEADERS_XAPI, data=json.dumps(payload or {}), timeout=120)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    debug(f"[POST x-api] {url} -> {r.status_code}")
    if r.status_code >= 400:
        debug(r.text)
        r.raise_for_status()
    return r.status_code, body

def _post_bearer(url, token, payload=None):
    r = requests.post(url, headers=_headers_bearer(token), data=json.dumps(payload or {}), timeout=600)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    debug(f"[POST bearer] {url} -> {r.status_code}")
    if r.status_code >= 400:
        debug(r.text)
        r.raise_for_status()
    return r.status_code, body

# ---------------- LiveAvatar helpers ----------------
def create_session_token_full(avatar_id: str, voice_id: Optional[str] = None, language: str = "en") -> dict:
    """
    Create a LiveAvatar FULL-mode session token for the given avatar/voice.

    Returns a dict with:
      - session_id
      - session_token
    """
    payload = {
        "mode": "FULL",
        "avatar_id": avatar_id,
        "avatar_persona": {
            "voice_id": voice_id or FIXED_AVATAR.get("default_voice"),
            "language": language,
        },
    }
    status, body = _post_xapi(API_SESS_TOKEN, payload)
    data = body or {}
    sid = data.get("session_id")
    session_token = data.get("session_token")
    if not sid or not session_token:
        raise RuntimeError(f"Missing session_id/session_token in response: {body}")
    return {"session_id": sid, "session_token": session_token}


def start_liveavatar_session(avatar_id: str, voice_id: Optional[str] = None, language: str = "en") -> dict:
    """
    Convenience helper: create a LiveAvatar FULL-mode session and start it.

    Returns dict with:
      - session_id
      - session_token
      - livekit_url
      - livekit_client_token
      - ws_url (optional, for advanced command-event integrations)
    """
    created = create_session_token_full(avatar_id, voice_id, language=language)
    session_id = created["session_id"]
    session_token = created["session_token"]

    status, body = _post_bearer(API_SESS_START, session_token, {})
    data = body or {}

    # The exact shape may evolve; try a few common patterns defensively.
    livekit_url = (
        data.get("livekit_url")
        or (data.get("livekit") or {}).get("url")
        or data.get("liveKitUrl")
    )
    livekit_client_token = (
        data.get("livekit_client_token")
        or (data.get("livekit") or {}).get("token")
        or data.get("token")
    )
    ws_url = data.get("ws_url") or (data.get("websocket") or {}).get("url")

    if not livekit_url or not livekit_client_token:
        raise RuntimeError(f"Missing LiveKit connection info from /sessions/start: {body}")

    return {
        "session_id": session_id,
        "session_token": session_token,
        "livekit_url": livekit_url,
        "livekit_client_token": livekit_client_token,
        "ws_url": ws_url,
    }


def keep_session_alive(session_id: str, session_token: str) -> None:
    """
    Refresh the LiveAvatar session idle timeout using the official keep-alive endpoint.
    """
    try:
        _post_bearer(API_SESS_KEEPALIVE, session_token, {"session_id": session_id})
        debug("[keep-alive] refreshed LiveAvatar session")
    except Exception as e:
        debug(f"[keep-alive] {e}")


def fetch_session_transcript(session_id: str, session_token: str):
    """
    Fetch the transcript for the current LiveAvatar session (if available).
    """
    url = API_SESS_TRANSCRIPT.format(session_id=session_id)
    try:
        status, body = _post_bearer(url, session_token, None)
        if status == 200:
            return body
    except Exception as e:
        debug(f"[transcript] {e}")
    return None


def send_text_to_avatar(session_id: str, session_token: str, text: str) -> bool:
    """
    Placeholder for compatibility with the previous HeyGen /streaming.task flow.

    LiveAvatar's v1 API no longer accepts plain text over HTTPS for speaking.
    Instead, text must be converted to audio (Custom mode) or sent as a
    command event over the LiveKit data channel (Full mode), e.g. using
    the @heygen/liveavatar-web-sdk.

    For now, this function simply logs and returns False so that the
    surrounding stress-test UI remains intact without accidentally
    calling deprecated endpoints.
    """
    if not text:
        return False
    debug("[avatar] send_text_to_avatar is not implemented for LiveAvatar v1. See Command Events docs.")
    return False


def stop_session(session_id: Optional[str], session_token: Optional[str]):
    if not (session_id and session_token):
        return
    try:
        _post_bearer(API_SESS_STOP, session_token, {"session_id": session_id})
        debug("[stop] LiveAvatar session stopped")
    except Exception as e:
        debug(f"[stop_session] {e}")


@atexit.register
def _graceful_shutdown():
    try:
        sid = st.session_state.get("session_id")
        tok = st.session_state.get("session_token")
        if sid and tok:
            stop_session(sid, tok)
    except Exception:
        pass

# ---------------- Audio helpers (unchanged) ----------------
def sniff_mime(b: bytes) -> str:
    try:
        if len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WAVE": return "audio/wav"
        if b.startswith(b"ID3") or (len(b) > 1 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0): return "audio/mpeg"
    except Exception:
        pass
    return "application/octet-stream"


def _bytes_to_dataurl(b: bytes) -> str:
    import base64
    mime = sniff_mime(b)
    b64 = base64.b64encode(b).decode("ascii")
    return f"data:{mime};base64,{b64}"

# (… keep your existing mic / GPT / UI code below; unchanged except where
# it references the session fields and helpers that we already updated …)

# ---------------- UI Shell (unchanged layout) ----------------
st.set_page_config(page_title="Avatharam 3.0 – LiveAvatar", layout="wide")
st.markdown(
    f"""
    <h3 style="margin-bottom:0;">Avatharam 3.0 – LiveAvatar</h3>
    <div style="font-size:13px;color:#888;margin-bottom:0.75rem;">
        Stress-test harness for HeyGen LiveAvatar Sessions API (FULL mode).
    </div>
    """,
    unsafe_allow_html=True,
)

cols = st.columns([1, 12, 1])
with cols[0]:
    if st.button("☰", key="btn_trigram_main", help="Open side panel"):
        ss.show_sidebar = not ss.show_sidebar
        debug(f"[ui] sidebar -> {ss.show_sidebar}")

# (… your sidebar content, GPT chat configuration, etc …)
# I’m not rewriting all of that here, since it isn’t impacted by the
# migration – you can keep the existing blocks as-is.

# ---------------- Auto-start the avatar session ----------------
if not ss.auto_started:
    try:
        debug("[auto-start] initializing LiveAvatar session (FULL mode)")
        created = start_liveavatar_session(FIXED_AVATAR["avatar_id"], FIXED_AVATAR.get("default_voice"))
        ss.session_id            = created["session_id"]
        ss.session_token         = created["session_token"]
        ss.livekit_url           = created["livekit_url"]
        ss.livekit_client_token  = created["livekit_client_token"]
        ss.ws_url                = created.get("ws_url")
        ss.auto_started          = True
        debug(f"[auto-start] LiveAvatar ready id={ss.session_id[:8]}...")
    except Exception as e:
        debug(f"[auto-start] failed: {repr(e)}")

# ---------------- Main viewer area ----------------
viewer_candidates = [Path.cwd() / "viewer -Ver-8.1.html", Path.cwd() / "viewer.html"]
viewer_path = next((p for p in viewer_candidates if p.exists()), None)
viewer_loaded = bool(ss.session_id and ss.session_token and ss.livekit_url and ss.livekit_client_token)

if viewer_loaded and ss.bgm_should_play:
    ss.bgm_should_play = False
    debug("[bgm] stopping background music (viewer ready)")

def _image_compat(url: str, caption: str = ""):
    try:
        st.image(url, caption=caption, use_container_width=True)
    except TypeError:
        try:
            st.image(url, caption=caption, use_column_width=True)
        except TypeError:
            st.image(url, caption=caption)

center_col = st.columns([1, 2, 1])[1]
with center_col:
    if viewer_loaded and viewer_path:
        html = (
            viewer_path.read_text(encoding="utf-8")
            .replace("__AVATAR_NAME__", FIXED_AVATAR["pose_name"])
            .replace("__LIVEKIT_URL__", ss.livekit_url or "")
            .replace("__LIVEKIT_TOKEN__", ss.livekit_client_token or "")
            .replace("__WS_URL__", ss.ws_url or "")
        )
        components.html(html, height=360, scrolling=False)
    else:
        if ss.session_id is None and ss.session_token is None:
            _image_compat(
                FIXED_AVATAR["normal_preview"],
                caption=f"{FIXED_AVATAR['pose_name']} ({FIXED_AVATAR['avatar_id']})",
            )

# ---------------- Instruction / Stress-test button ----------------
st.markdown("<div id='actrow' style='margin-top:0.5rem;'></div>", unsafe_allow_html=True)
col1, col2 = st.columns(2, gap="small")
with col1:
    if st.button("Instruction", key="btn_instruction_main", use_container_width=True):
        if not (ss.session_id and ss.session_token and ss.livekit_url):
            st.warning("Start a session first.")
        else:
            text_to_send = ss.test_text if ss.test_text else DEFAULT_INSTRUCTION
            t0 = time.time()
            ok = send_text_to_avatar(ss.session_id, ss.session_token, text_to_send)
            t1 = time.time()
            debug(f"[timer] long-text send finished ok={ok}; elapsed={t1 - t0:.2f}s")

            if ok:
                ss.stress_active = True
                ss.next_keepalive_at = time.time() + 60.0
                ss.autorefresh_on = True
                debug("[stress] activated; autorefresh ON")
            else:
                st.warning(
                    "send_text_to_avatar is not implemented for LiveAvatar v1.\n\n"
                    "Use the browser UI (LiveKit) to speak, or extend viewer.html "
                    "to send avatar.speak_text command events."
                )

with col2:
    if st.button("Stop Session", key="btn_stop_main", use_container_width=True):
        stop_session(ss.session_id, ss.session_token)
        ss.session_id = None
        ss.session_token = None
        ss.livekit_url = None
        ss.livekit_client_token = None
        ss.ws_url = None
        ss.stress_active = False
        ss.autorefresh_on = False
        ss.next_keepalive_at = 0.0
        debug("[ui] session cleared")

# ---------------- Autorefresh helper ----------------
def _install_autorefresh(enabled: bool, interval_ms: int = 2000):
    if not enabled:
        return
    components.html(
        f"""
        <script>
        const interval_ms = {interval_ms};
        function pingParent() {{
          if (window.parent) {{
            window.parent.postMessage({{type:'streamlit:rerun'}}, '*');
          }}
        }}
        setTimeout(pingParent, interval_ms);
        </script>
        """,
        height=0,
    )

# Arm 2s autorefresh if timer is active
_install_autorefresh(ss.autorefresh_on, 2000)

# On each rerun, if time passed, refresh keep-alive and schedule next
if ss.stress_active and ss.session_id and ss.session_token and ss.livekit_url:
    now = time.time()
    if now >= float(ss.next_keepalive_at or 0):
        keep_session_alive(ss.session_id, ss.session_token)
        debug(f"[keepalive] refreshed @ {time.strftime('%H:%M:%S')}")
        # Next keep-alive in 60 seconds (occupy every 1 minute from last conversation)
        ss.next_keepalive_at = time.time() + 60.0
        debug(f"[timer] next keepalive at {time.strftime('%H:%M:%S', time.localtime(ss.next_keepalive_at))}")
