"""
לולאת הסוכן: חשוב -> מפעיל כלים -> מתבונן -> חוזר, עד תשובה סופית.

תומך בשלושה ספקים, כולם דרך פרוטוקול OpenAI:
  GROQ_API_KEY    -> https://api.groq.com/openai/v1        (מומלץ, חינם)
  OPENAI_API_KEY  -> https://api.openai.com/v1
  GEMINI_API_KEY  -> https://generativelanguage.googleapis.com/v1beta/openai/
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from urllib.request import Request as URLRequest, urlopen

from tools import TOOL_SCHEMAS, quick_reply, run_tool

ISRAEL_TZ_OFFSET = 3
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
DEFAULT_SYSTEM_PROMPT = (
    "אתה סוכן AI ידידותי ומקצועי, עונה תמיד בעברית אלא אם המשתמש ביקש אחרת.\n"
    "- ענה קצר וממוקד, בלי מילות מיותרות ובלי התנצלות.\n"
    "- אתה משתמש בכלים כדי לבצע משימות במקום לנחש. אם יש כלי מתאים — השתמש בו.\n"
    "- אם תשובה מבוססת מידע על מקור חיצוני, חפש בוויקיפדיה והבא את העובדה בפועל.\n"
    "- אל תמציא עובדות. אם אינך יודע, אמר שאינך יודע והצע איך לבדוק.\n"
    "- חשוב: מותר לקרוא את שם המשתמש מהזיכרון אם נשמר, אך אל תשמור מידע רגיש."
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


# ------------------------------------------------------------- provider list
def providers() -> List[Dict[str, str]]:
    """מחזיר את ספקי המודל הזמינים לפי סדר עדיפות."""
    out: List[Dict[str, str]] = []
    if os.getenv("GROQ_API_KEY", "").strip():
        out.append({
            "id": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": os.environ["GROQ_API_KEY"].strip(),
            "model": (os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
                      or "llama-3.3-70b-versatile"),
        })
    if os.getenv("OPENAI_API_KEY", "").strip():
        out.append({
            "id": "openai",
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip(),
            "api_key": os.environ["OPENAI_API_KEY"].strip(),
            "model": (os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"),
        })
    gem = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if gem:
        out.append({
            "id": "gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key": gem,
            "model": (os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"),
        })
    return out


def active_provider() -> str:
    provs = providers()
    return f"{provs[0]['id']}:{provs[0]['model']}" if provs else "local"


# ------------------------------------------------------------------ transport
def _api_headers(api_key: str) -> dict:
    """כותרות הבקשה.

    חשוב: urllib שולח User-Agent כמו 'Python-urllib/3.x'. מאחורי חלק מהספקים
    (Groq למשל) יושבת Cloudflare עם Browser Integrity Check שמחזיר
    403 "error code: 1010" לכל User-Agent שאינו דפדפן. לכן אנחנו מציגים
    עצמנו כלקוח רגיל.
    """
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": BROWSER_UA,
        "Accept-Language": "en-US,en;q=0.9",
    }


def _post_json(url: str, payload: dict, api_key: str, timeout: int) -> dict:
    """שולח בקשה ל-API תואם OpenAI. ממיר שגיאות HTTP להודעה קריאה."""
    from urllib.error import HTTPError, URLError

    body = json.dumps(payload).encode()
    req = URLRequest(url, data=body, headers=_api_headers(api_key))
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")[:600]
        except Exception:
            pass
        try:
            parsed = json.loads(raw)
            err = parsed.get("error", parsed)
            msg = err.get("message") if isinstance(err, dict) else err
            msg = msg or raw
        except Exception:
            msg = raw or str(e.reason)
        raise RuntimeError(f"HTTP {e.code}: {msg}") from None
    except URLError as e:
        raise RuntimeError(f"network error: {e.reason}") from None


def _chat_once(provider: Dict[str, str], messages: List[dict], tools: list, timeout: int) -> dict:
    payload = {
        "model": provider["model"],
        "messages": messages,
        "temperature": float(os.getenv("AGENT_TEMPERATURE", "0.3") or 0.3),
        "max_tokens": _int_env("AGENT_MAX_TOKENS", 1500),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return _post_json(
        provider["base_url"].rstrip("/") + "/chat/completions",
        payload, provider["api_key"], timeout,
    )


def _tool_results_to_message(tool_calls: list, results: List[dict]) -> dict:
    return {
        "role": "tool",
        "tool_call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
        "name": (tc.get("function") or {}).get("name", ""),
        "content": json.dumps(result, ensure_ascii=False),
    }


# --------------------------------------------------------- session memory file
def _history_path() -> str:
    return os.path.join(os.getcwd(), "data", "history.json")


def _load_history(session_id: str, limit: int) -> List[dict]:
    try:
        with open(_history_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    msgs = data.get(session_id) or []
    return msgs[-limit:]


def _save_history(session_id: str, messages: List[dict], max_sessions: int = 100) -> None:
    path = _history_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[session_id] = messages[-60:]
    if len(data) > max_sessions:
        data = dict(list(data.items())[-max_sessions:])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


# ------------------------------------------------------------------ the loop
def run_agent(
    goal: str,
    session_id: str = "default",
    on_event: Optional[Callable[[dict], None]] = None,
) -> dict:
    """מריץ את הסוכן על משימה ומחזיר dict עם התשובה, הצעדים והזמן."""
    started = time.time()
    emit = on_event if callable(on_event) else (lambda _e: None)

    def quick(message: str, provider: str = "local") -> dict:
        return {
            "ok": True, "provider": provider, "answer": message,
            "steps": [], "duration_ms": int((time.time() - started) * 1000),
            "used_tools": False,
        }

    goal = (goal or "").strip()
    if not goal:
        return quick("כתוב לי משהו ואשמח לעזור.")

    # מעקה מקומי — חוסך קריאה למודל עבור שאלות פשוטות ומיידיות
    fast = quick_reply(goal)
    if fast:
        return quick(fast)

    provs = providers()
    if not provs:
        return quick(
            "אין מפתח API מוגדר בשרת, ולכן אני במצב חזרתי מקומי בלבד.\n"
            "בדוק את /api/info. אם זו התקלה הראשונה שלך — צריך להוסיף GROQ_API_KEY "
            "ב-Render → Environment ואז לבצע redeploy."
        )

    max_steps = _int_env("AGENT_MAX_STEPS", 8)
    timeout = _int_env("AGENT_TIMEOUT", 90)
    use_tools = os.getenv("AGENT_TOOLS", "1").strip() not in ("0", "false", "no")

    system_prompt = (os.getenv("AGENT_SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT)
    memory_limit = _int_env("MEMORY_MAX_MESSAGES", 12)

    history: List[dict] = [{"role": "system", "content": system_prompt}]
    history += _load_history(session_id, memory_limit)
    history.append({"role": "user", "content": goal})

    steps: List[dict] = []
    used_tools = False
    answer = ""
    last_errors: List[str] = []

    for provider in provs:
        convo = list(history)
        try:
            for step in range(1, max_steps + 1):
                if time.time() - started > timeout:
                    answer = "עצרתי את עצמי אחרי יותר מדי צעדים בלי להגיע למסקנה."
                    break
                try:
                    raw = _chat_once(provider, convo, TOOL_SCHEMAS if use_tools else [], timeout)
                except Exception as e:
                    last_errors.append(f"{provider['id']}: {e}")
                    break

                choice = (raw.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                convo.append(msg)

                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    answer = (msg.get("content") or "").strip()
                    emit({"type": "final", "content": answer})
                    if not answer:
                        answer = "לא הצלחתי להפיק תשובה. נסה לנסח את השאלה אחרת."
                    break

                used_tools = True
                results: List[dict] = []
                for call in tool_calls:
                    name = (call.get("function") or {}).get("name", "")
                    raw_args = (call.get("function") or {}).get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except Exception:
                        args = {"_raw": str(raw_args)[:200]}
                    result = run_tool(name, args)
                    steps.append({"step": step, "tool": name, "args": args, "result": result})
                    emit({"type": "tool", "name": name, "args": args})
                    results.append(result)
                    convo.append(_tool_results_to_message(call, [result]))
            if answer:
                break
        except Exception as e:
            last_errors.append(f"{provider['id']}: {e}")

    if not answer:
        detail = (" | ".join(last_errors))[:300] if last_errors else "ללא פרטים"
        answer = (
            "לא הצלחתי לקבל תשובה מהמודל כרגע.\n"
            f"סיבה אפשרית: {detail}\n"
            "אפשר לנסות שוב בעוד רגע או לבדוק את המפתח והמכסה ב-/api/info."
        )

    # שומר רק user/assistant נקיים, בלי payloads של כלים
    clean = [
        {"role": m["role"], "content": str(m.get("content") or "")[:2000]}
        for m in history[1:]
        if m.get("role") in ("user", "assistant") and m.get("content")
        and not m.get("tool_calls")
    ]
    try:
        _save_history(session_id, clean)
    except Exception:
        pass

    return {
        "ok": True,
        "provider": f"{provider['id']}:{provider['model']}",
        "answer": answer,
        "steps": steps,
        "used_tools": used_tools,
        "duration_ms": int((time.time() - started) * 1000),
    }


_run_lock = threading.Semaphore(_int_env("AGENT_MAX_CONCURRENCY", 4))


def run_agent_limited(**kwargs) -> dict:
    """עוטף run_agent עם הגבלת הרצות מקביליות (חשוב בתוכנית חינמית)."""
    if not _run_lock.acquire(blocking=False):
        return {
            "ok": False, "provider": "busy", "answer": "השרת עמוס כרגע, נסה שוב בעוד רגע.",
            "steps": [], "used_tools": False, "duration_ms": 0,
        }
    try:
        return run_agent(**kwargs)
    finally:
        _run_lock.release()
