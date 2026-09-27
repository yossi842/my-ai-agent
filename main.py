"""
my-ai-agent — סוכן AI בעברית מוכן ל-Render
Start: uvicorn main:app --host 0.0.0.0 --port $PORT
Providers (by env vars, no keys in code):
  1. GROQ_API_KEY (+ GROQ_MODEL, default llama-3.3-70b-versatile) — OpenAI-compatible, fast free tier
  2. GEMINI_API_KEY / GOOGLE_API_KEY (+ GEMINI_MODEL, default gemini-2.0-flash)
  3. OPENAI_API_KEY (+ OPENAI_MODEL, default gpt-4o-mini)
Fallback: local rule-based agent (works without any key).
"""
import os
import re
import math
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="my-ai-agent", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ISRAEL_TZ = timezone(timedelta(hours=3))  # Asia/Jerusalem
SYSTEM_PROMPT = "אתה סוכן AI ידידותי שעונה בעברית, קצר, ברור ועוזר. ענה תמיד בעברית אלא אם המשתמש ביקש אחרת."


class ChatMessage(BaseModel):
    role: str = "user"
    content: str = ""


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[ChatMessage]] = []


class ChatResponse(BaseModel):
    reply: str
    timestamp: str
    provider: str = "local"


HELP_TEXT = """היי, אני הסוכן שלך עם מודל AI אמיתי!

אני יודע:
- לענות בעברית על כל שאלה
- לכתוב, לסכם, לתרגם, לעזור בקוד
- חישובים — כתוב למשל: חשב 12*8+5
- שעה / תאריך — כתוב: מה השעה?

פשוט כתוב לי מה שתרצה."""


def now_israel():
    return datetime.now(ISRAEL_TZ)


def try_math(text: str) -> Optional[str]:
    lowered = text.strip().lower()
    m = re.search(r"(?:חשב|calculate|calc|compute)\s*[:\-]?\s*(.+)", lowered)
    expr_raw = None
    if m:
        expr_raw = m.group(1)
    elif re.fullmatch(r"[\d\s\+\-\*\/\%\.\(\)\^\!x×÷]+", lowered) and re.search(r"\d", lowered):
        expr_raw = lowered
    if not expr_raw:
        return None
    expr = expr_raw.replace("x", "*").replace("×", "*").replace("÷", "/").replace("^", "**").strip()
    expr = re.sub(r"[^0-9\+\-\*\/\%\.\(\)\s\!]", "", expr).strip()
    if not expr or not re.search(r"\d", expr):
        return None
    if re.search(r"(__|import|os|sys|open|eval|exec)", expr):
        return None
    try:
        if expr.endswith("!"):
            n = int(expr[:-1].strip())
            if 0 <= n <= 20:
                return f"התוצאה של {expr_raw.strip()} היא: {math.factorial(n)}"
            return None
        allowed = {"__builtins__": {}}
        result = eval(expr, allowed, {"math": math, "pi": math.pi, "e": math.e})
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"התוצאה של {expr_raw.strip()} היא: {result}"
    except Exception:
        return "לא הצלחתי לחשב את זה. נסה למשל: חשב 12*8+5"


def agent_reply_local(user_text: str) -> str:
    text = (user_text or "").strip()
    if not text:
        return "כתוב לי משהו ואשמח לעזור"
    low = text.lower()
    if low in ["/help", "עזרה", "help", "מה אתה יודע", "מה אתה יודע לעשות"]:
        return HELP_TEXT
    if any(k in low for k in ["מה השעה", "שעה עכשיו", "what time"]):
        return f"השעה עכשיו (ישראל): {now_israel().strftime('%H:%M')}"
    if any(k in low for k in ["מה התאריך", "תאריך היום", "what date", "איזה תאריך"]):
        return f"התאריך היום (ישראל): {now_israel().strftime('%d/%m/%Y')}"
    math_result = try_math(text)
    if math_result:
        return math_result
    if low in ["היי", "היי!", "שלום", "הלו", "hi", "hello", "hey"]:
        return "היי! מה תרצה לעשות היום?"
    if "תודה" in low or "thanks" in low:
        return "בכיף! אם יש עוד משהו — אני כאן."
    if "מי אתה" in low or "what model" in low or "איזה מודל" in low:
        return "אני my-ai-agent — רץ על Render עם מודל Groq מהיר (Llama) וגיבוי מקומי לעזרה בעברית."
    return (
        f"קיבלתי: {text}\n\n"
        "אני בגרסת בסיס מקומית כרגע. בדוק חיבור מודל ב-/api/info."
    )


def build_openai_messages(message: str, history: List[ChatMessage]):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-10:]:
        if h.role in ("user", "assistant") and h.content:
            msgs.append({"role": h.role, "content": h.content})
    msgs.append({"role": "user", "content": message})
    return msgs


def call_openai_compatible(api_key: str, base_url: str, model: str, message: str, history: List[ChatMessage]) -> str:
    from urllib.request import Request as URLRequest, urlopen
    import json as _json
    msgs = build_openai_messages(message, history)
    data = _json.dumps({"model": model, "messages": msgs, "temperature": 0.7, "max_tokens": 1024}).encode()
    r = URLRequest(base_url.rstrip("/") + "/chat/completions",
                   data=data,
                   headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urlopen(r, timeout=40) as resp:
        out = _json.loads(resp.read().decode())
    return out["choices"][0]["message"]["content"]


def call_gemini(api_key: str, model: str, message: str, history: List[ChatMessage]) -> str:
    from urllib.request import Request as URLRequest, urlopen
    from urllib.parse import quote
    import json as _json
    contents = []
    for h in (history or [])[-10:]:
        if not h.content:
            continue
        role = "model" if h.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": h.content}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model)}/:generateContent?key={api_key}"
    # NOTE: key is sent as query param per Google API spec (env var only, never logged)
    data = _json.dumps(body).encode()
    r = URLRequest(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(r, timeout=40) as resp:
        out = _json.loads(resp.read().decode())
    return out["candidates"][0]["content"]["parts"][0]["text"]


def active_provider() -> str:
    if os.getenv("GROQ_API_KEY", "").strip():
        return "groq:" + os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    gem = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if gem:
        return "gemini:" + os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai:" + os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return "local"


@app.get("/healthz")
def healthz():
    return {"status": "ok", "time": now_israel().isoformat(), "provider": active_provider()}


@app.get("/api/info")
def info():
    groq = bool(os.getenv("GROQ_API_KEY", "").strip())
    gem = bool(os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip())
    oai = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return {
        "name": "my-ai-agent",
        "version": "2.0.0",
        "language": "python",
        "provider": active_provider(),
        "groq_connected": groq,
        "gemini_connected": gem,
        "openai_connected": oai,
        "groq_model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    history = req.history or []
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile"
            reply = call_openai_compatible(groq_key, "https://api.groq.com/openai/v1", model, req.message, history)
            return ChatResponse(reply=reply, timestamp=now_israel().isoformat(), provider=f"groq:{model}")
        except Exception as e:
            err = str(e)[:200]
            # fall through to next provider, keep err for debugging via logs only
            print(f"Groq failed: {err}")
    gem_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if gem_key:
        try:
            model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
            reply = call_gemini(gem_key, model, req.message, history)
            return ChatResponse(reply=reply, timestamp=now_israel().isoformat(), provider=f"gemini:{model}")
        except Exception as e:
            print(f"Gemini failed: {str(e)[:200]}")
    oai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if oai_key:
        try:
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
            reply = call_openai_compatible(oai_key, "https://api.openai.com/v1", model, req.message, history)
            return ChatResponse(reply=reply, timestamp=now_israel().isoformat(), provider=f"openai:{model}")
        except Exception as e:
            print(f"OpenAI failed: {str(e)[:200]}")
    # Local instant answers for time/date/math/help even when keys exist
    local = agent_reply_local(req.message)
    if local and not local.startswith("קיבלתי:"):
        return ChatResponse(reply=local, timestamp=now_israel().isoformat(), provider="local")
    if not groq_key and not gem_key and not oai_key:
        return ChatResponse(reply=local, timestamp=now_israel().isoformat(), provider="local")
    # Keys exist but all providers failed
    fallback = agent_reply_local(req.message)
    return ChatResponse(reply=fallback + "\n\n(כל המודלים נכשלו זמנית — עניתי מקומית. בדוק לוגים / מכסה חינמית.)",
                        timestamp=now_israel().isoformat(), provider="local")


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE


HTML_PAGE = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>my-ai-agent - הסוכן שלי</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(135deg,#0f172a,#1e1b4b 60%,#0f172a);color:#fff;min-height:100vh;display:flex;justify-content:center;padding:20px}
.wrap{width:100%;max-width:760px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.15);border-radius:20px;overflow:hidden;display:flex;flex-direction:column;min-height:85vh}
header{padding:18px 22px;background:rgba(0,0,0,.25);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,.12)}
header h1{margin:0;font-size:20px}
header p{margin:2px 0 0;font-size:13px;opacity:.75}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#22c55e;margin-left:8px}
#chat{flex:1;padding:20px;display:flex;flex-direction:column;gap:12px;overflow-y:auto;max-height:60vh}
.msg{max-width:85%;padding:12px 15px;border-radius:14px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word;font-size:15px}
.user{align-self:flex-start;background:#2563eb}
.bot{align-self:flex-end;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.15)}
.examples{display:flex;flex-wrap:wrap;gap:8px;padding:0 20px 10px}
.examples button{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);color:#fff;border-radius:20px;padding:7px 13px;cursor:pointer;font-size:13px}
form{display:flex;gap:10px;padding:16px 20px;background:rgba(0,0,0,.25);border-top:1px solid rgba(255,255,255,.12)}
input{flex:1;padding:13px 15px;border-radius:12px;border:1px solid rgba(255,255,255,.2);background:rgba(255,255,255,.1);color:#fff;font-size:15px;outline:none}
button.send{background:#22c55e;border:none;color:#052e16;font-weight:bold;border-radius:12px;padding:0 22px;cursor:pointer;font-size:15px}
button.clear{background:transparent;border:1px solid rgba(255,255,255,.25);color:#fff;border-radius:10px;padding:6px 12px;cursor:pointer;font-size:12px}
.typing{opacity:.7;font-style:italic}
footer{text-align:center;font-size:12px;opacity:.6;padding:10px}
</style>
</head>
<body>
<div class="wrap">
<header>
<div><h1><span class="dot"></span>my-ai-agent</h1><p id="prov">הסוכן האישי שלך - מחובר ופעיל</p></div>
<button class="clear" onclick="clearChat()">נקה צאט</button>
</header>
<div id="chat"></div>
<div class="examples">
<button onclick="ask('עזרה')">עזרה</button>
<button onclick="ask('מה השעה?')">מה השעה?</button>
<button onclick="ask('חשב 12*8+5')">חשב 12*8+5</button>
<button onclick="ask('כתוב לי בדיחה בעברית')">בדיחה</button>
</div>
<form onsubmit="send(event)">
<input id="inp" placeholder="כתוב הודעה לסוכן..." autocomplete="off">
<button class="send" type="submit">שלח</button>
</form>
<footer>Groq + Gemini על Render - Frankfurt - Free Plan</footer>
</div>
<script>
let history=[];
const chat=document.getElementById('chat');
const inp=document.getElementById('inp');
fetch('/api/info').then(r=>r.json()).then(j=>{document.getElementById('prov').textContent='מחובר: '+j.provider;}).catch(()=>{});
function addMsg(t,c){const d=document.createElement('div');d.className='msg '+c;d.textContent=t;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d;}
addMsg('היי! אני הסוכן החכם שלך עם AI אמיתי. שאל אותי כל דבר בעברית.','bot');
function ask(t){inp.value=t;send(new Event('submit'));}
async function send(e){e.preventDefault();const text=inp.value.trim();if(!text)return;inp.value='';addMsg(text,'user');history.push({role:'user',content:text});const tp=addMsg('מקליד...','bot typing');try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,history:history})});const j=await r.json();tp.remove();addMsg(j.reply,'bot');history.push({role:'assistant',content:j.reply});}catch(err){tp.remove();addMsg('שגיאת חיבור לשרת. נסה שוב.','bot');}}
function clearChat(){chat.innerHTML='';history=[];addMsg('הצאט נוקה. איך אפשר לעזור?','bot');}
</script>
</body>
</html>
"""
