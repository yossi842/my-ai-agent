"""
שכבת טלפוניה - סוכן קולי דרך Twilio.

הזרימה (TwiML, ללא ספריית Twilio - XML ידני כדי לשמור על תלויות אפס):
  חיוג  -> /voice/webhook -> ברוך הדבר -> <Gather input="speech">
         -> /voice/turn   -> טקסט מהדובר -> הסוכן עונה -> <Say> -> <Gather> שוב
         -> עד מספר תורות -> פרידה -> <Hangup>

⚠️ המלכודת הגדולה ביותר בעברית:
  • הקראה קולית  <Say>    -> language="he-IL"   (תקין)
  • זיהוי דיבור  <Gather> -> language="iw-IL"   (לא he-IL!)
  עם "he-IL" ב-Gather מתקבל Warning 13331 והטקסט חוזר באנגלית, ללא שגיאה גלויה.
  בנוסף: עברית אינה נתמכת ב-Google STT v2, ולכן לא מגדירים speech_model=google_v2.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Optional
from urllib.parse import urlencode

from agent import run_agent_limited

# --------------------------------------------------------------------- config
def _env(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


VOICE_ENABLED = _env("VOICE_ENABLED", "0") in ("1", "true", "yes", "on")
GREETING = _env(
    "VOICE_GREETING",
    "שלום, אני הסוכן שלך. איך אפשר לעזור?",
)
VOICE = _env("VOICE_VOICE", "Google.he-IL-Wavenet-A")
VOICE_LANG = _env("VOICE_LANG", "he-IL")          # להקראה
STT_LANG = _env("VOICE_STT_LANG", "iw-IL")        # לזיהוי - iw-IL בלבד!
MAX_TURNS = int(_env("VOICE_MAX_TURNS", "12") or 12)
MAX_SAY_CHARS = int(_env("VOICE_MAX_SAY_CHARS", "450") or 450)
GATHER_TIMEOUT = int(_env("VOICE_GATHER_TIMEOUT", "12") or 12)
SPEECH_TIMEOUT = _env("VOICE_SPEECH_TIMEOUT", "auto")
TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
VOICE_BASE_URL = _env("VOICE_BASE_URL")           # למשל https://my-ai-agent-xxx.onrender.com

HANGUP_WORDS = (
    "להתקשר", "להתקשר לך", "תודה", "ביי", "שלום", "להפסיק", "סיום", "נתראה",
    "end call", "bye", "goodbye", "hang up",
)

# --------------------------------------------------------------- XML helpers
def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _clean_for_speech(text: str) -> str:
    """הופך תשובת LLM לטקסט שניתן להקריא בטלפון."""
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)          # בלוקי קוד
    t = re.sub(r"`([^`]*)`", r"\1", t)                    # קוד חד-שורתי
    t = re.sub(r"https?://\S+", "כתובת אינטרנט", t)      # קישורים
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)                # הדגשה
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", t)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)          # כותרות
    t = re.sub(r"\s*[-–—•]\s+", ". ", t)                  # רשימות
    t = t.replace("*", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\n", ". ", t)
    t = re.sub(r"\.\s*\.\s*", ". ", t)
    t = t.strip()
    if len(t) > MAX_SAY_CHARS:
        cut = t[:MAX_SAY_CHARS]
        # לעצור בפסק, לא באמצע מילה
        for stop in (". ", "! ", "? "):
            if stop in cut[-120:]:
                cut = cut[: cut.rindex(stop) + 1]
                break
        t = cut.rstrip() + "."
    return t


def _say(text: str, loop: int = 1) -> str:
    return (
        f'<Say voice="{_esc(VOICE)}" language="{_esc(VOICE_LANG)}" loop="{loop}">'
        f"{_esc(text)}</Say>"
    )


def _gather(inner: str, action: str, hint: str = "") -> str:
    hints = f' hints="{_esc(hint)}"' if hint else ""
    return (
        f'<Gather input="speech dtmf" method="POST" action="{_esc(action)}"'
        f' language="{STT_LANG}" timeout="{GATHER_TIMEOUT}"'
        f' speechTimeout="{_esc(SPEECH_TIMEOUT)}" numDigits="1"'
        f' finishOnKey=""{hints}>'
        f"{inner or _say('אני מקשיב.')}"
        f"</Gather>"
    )


def _twiml(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>{body}</Response>'


# ---------------------------------------------------------- security (HMAC)
def verify_twilio_signature(
    url: str, params: dict, signature: str, auth_token: str
) -> bool:
    """מאמת את חתימת Twilio כדי למנוע שימוש חינמי בסוכן מהאינטרנט."""
    if not auth_token or not signature:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(
        auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature.strip())


# ----------------------------------------------------------------- sessions
_sessions: dict[str, dict] = {}
SESSION_TTL = 60 * 60 * 3


def _get_session(call_sid: str, caller: str = "") -> dict:
    now = time.time()
    s = _sessions.get(call_sid)
    if not s or now - s.get("started", 0) > SESSION_TTL:
        for k in list(_sessions):
            if now - _sessions[k].get("started", 0) > SESSION_TTL:
                _sessions.pop(k, None)
        s = {"started": now, "turns": 0, "session_id": f"call:{call_sid[-8:]}", "caller": caller}
        _sessions[call_sid] = s
    return s


def _should_hangup(text: str, turns: int) -> bool:
    if turns >= MAX_TURNS:
        return True
    t = (text or "").strip().lower().rstrip(" .!?,׳")
    return any(t == w or t in (w + " ") or (len(t) < 25 and w in t) for w in HANGUP_WORDS)


# ------------------------------------------------------------------ webhook
def handle_voice_webhook(base_url: str, params: dict) -> str:
    call_sid = params.get("CallSid", "")
    caller = params.get("From", "")
    session = _get_session(call_sid, caller)
    print(f"[voice] call started sid={call_sid} from={caller}", flush=True)

    turn_url = f"{base_url.rstrip('/')}/voice/turn"
    if not params.get("CallStatus") == "in-progress":
        return _twiml("")

    return _twiml(
        _gather(
            _say(GREETING),
            turn_url,
            hint="עזרה מזג חישוב זיכרון שלום",
        )
    )


def handle_voice_turn(base_url: str, params: dict) -> str:
    call_sid = params.get("CallSid", "")
    speech = (params.get("SpeechResult") or "").strip()
    confidence = params.get("Confidence") or "?"
    session = _get_session(call_sid)
    session["turns"] += 1
    turn_url = f"{base_url.rstrip('/')}/voice/turn"

    # סיום שיחה
    call_status = params.get("CallStatus", "")
    if call_status in ("completed", "busy", "failed", "no-answer", "canceled"):
        _sessions.pop(call_sid, None)
        return _twiml("")

    if not speech:
        # DTMF או שקט - מציעים לחזור
        prompt = "לא הצלחתי לשמוע. אפשר לדבר שוב?"
        if session["turns"] >= 2:
            prompt = "לא הצלחתי לשמוע. להפסיק את השיחה לחץ על 1."
        return _twiml(_gather(_say(prompt), turn_url))

    print(
        f"[voice] turn={session['turns']} conf={confidence} said={speech[:90]}",
        flush=True,
    )

    if _should_hangup(speech, session["turns"]):
        _sessions.pop(call_sid, None)
        return _twiml(_say("להתראות!") + "<Hangup/>")

    t0 = time.time()
    try:
        result = run_agent_limited(
            goal=speech,
            session_id=session["session_id"],
        )
        answer = _clean_for_speech(result.get("answer") or "")
        provider = result.get("provider", "local")
        tools = len(result.get("steps") or [])
    except Exception as e:
        print(f"[voice] agent error: {e}", flush=True)
        answer, provider, tools = "אירעה תקלה, נסה שוב.", "error", 0

    ms = int((time.time() - t0) * 1000)
    print(
        f"[voice] answered via {provider} in {ms}ms "
        f"(tools={tools}, chars={len(answer)})",
        flush=True,
    )

    if not answer:
        answer = "לא הצלחתי להבין, אפשר לנסח את השאלה אחרת?"

    if session["turns"] >= MAX_TURNS:
        return _twiml(_say(answer) + _say("זו הייתה שיחתי האחרונה. להתראות!") + "<Hangup/>")

    return _twiml(_say(answer) + _gather(_say("איך עוד אפשר לעזור?"), turn_url))


def status_report() -> dict:
    return {
        "enabled": VOICE_ENABLED,
        "voice": VOICE,
        "tts_language": VOICE_LANG,
        "stt_language": STT_LANG,
        "max_turns": MAX_TURNS,
        "signature_check": bool(TWILIO_AUTH_TOKEN),
        "active_sessions": len(_sessions),
        "greeting": GREETING,
    }
