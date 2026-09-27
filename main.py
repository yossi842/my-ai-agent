"""
my-ai-agent — סוכן AI בעברית מוכן ל-Render
Start: uvicorn main:app --host 0.0.0.0 --port $PORT
"""
import os
import re
import math
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="my-ai-agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ISRAEL_TZ = timezone(timedelta(hours=3))  # Asia/Jerusalem


class ChatMessage(BaseModel):
    role: str = "user"
    content: str = ""


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[ChatMessage]] = []


class ChatResponse(BaseModel):
    reply: str
    timestamp: str


HELP_TEXT = """היי, אני הסוכן שלך! הנה מה שאני יודע לעשות כבר עכשיו (בלי מפתח API):

- לענות בעברית על שאלות כלליות
- חישובים מתמטיים — כתוב למשל: חשב 12*8+5
- שעה / תאריך — כתוב: מה השעה? או מה התאריך?
- עזרה — כתוב: עזרה

טיפ: אם תוסיף משתנה סביבה OPENAI_API_KEY ב-Render, אשתמש בו אוטומטית לתשובות חכמות יותר."""

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


def agent_reply(user_text: str, history: List[ChatMessage]) -> str:
    text = (user_text or "").strip()
    if not text:
        return "כתוב לי משהו ואשמח לעזור"
    low = text.lower()
    if low in ["/help", "עזרה", "help", "מה אתה יודע", "מה אתה יודע לעשות"]:
        return HELP_TEXT
    if any(k in low for k in ["מה השעה", "שעה עכשיו", "what time"]):
        t = now_israel().strftime("%H:%M")
        return f"השעה עכשיו (ישראל): {t}"
    if any(k in low for k in ["מה התאריך", "תאריך היום", "what date", "איזה תאריך"]):
        d = now_israel().strftime("%d/%m/%Y")
        return f"התאריך היום (ישראל): {d}"
    math_result = try_math(text)
    if math_result:
        return math_result
    if low in ["היי", "היי!", "שלום", "הלו", "hi", "hello", "hey"]:
        return "היי! מה תרצה לעשות היום? אפשר לשאול שאלה, לבקש חישוב (חשב 7*9), או לכתוב עזרה."
    if "תודה" in low or "thanks" in low:
        return "בכיף! אם יש עוד משהו — אני כאן."
    if "מי אתה" in low or "מה אתה" in low or "who are you" in low:
        return "אני my-ai-agent — סוכן פייתון (FastAPI) שרץ על Render. נבניתי כדי לענות, לחשב ולעזור בעברית, ישירות מהדפדפן."
    if "render" in low or "רנדר" in low or "דפלוי" in low or "deploy" in low:
        return "כדי להעלות אותי ל-Render: חבר את הריפו מ-GitHub, בחר Python 3, Branch main, Region Frankfurt, Build: pip install -r requirements.txt, Start: uvicorn main:app --host 0.0.0.0 --port $PORT, בחר Free, ולחץ Create Web Service."
    return (
        f"קיבלתי: {text}\n\n"
        "אני בגרסת בסיס (ללא מודל חיצוני), אז אענה כמיטב יכולתי:\n"
        "- לחישוב כתוב חשב ... למשל חשב 200*0.15\n"
        "- לשעה/תאריך כתוב מה השעה?\n"
        "- לרשימת יכולות כתוב עזרה\n\n"
        "רוצה שאחבר מודל שפה חכם? הוסף OPENAI_API_KEY במשתני הסביבה ב-Render ועשה Redeploy."
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "time": now_israel().isoformat()}


@app.get("/api/info")
def info():
    has_key = bool(os.getenv("OPENAI_API_KEY"))
    return {"name": "my-ai-agent", "version": "1.0.0", "language": "python", "openai_connected": has_key}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            from urllib.request import Request as URLRequest, urlopen
            import json as _json
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            msgs = [{"role": "system", "content": "אתה סוכן AI ידידותי שעונה בעברית, קצר וברור."}]
            for h in (req.history or [])[-10:]:
                if h.role in ("user", "assistant") and h.content:
                    msgs.append({"role": h.role, "content": h.content})
            msgs.append({"role": "user", "content": req.message})
            data = _json.dumps({"model": model, "messages": msgs, "temperature": 0.7}).encode()
            r = URLRequest("https://api.openai.com/v1/chat/completions",
                           data=data,
                           headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            with urlopen(r, timeout=30) as resp:
                out = _json.loads(resp.read().decode())
            reply = out["choices"][0]["message"]["content"]
            return ChatResponse(reply=reply, timestamp=now_israel().isoformat())
        except Exception as e:
            fallback = agent_reply(req.message, req.history or [])
            return ChatResponse(reply=fallback + f"\n\n(חיבור OpenAI נכשל, עניתי מקומית)",
                                timestamp=now_israel().isoformat())
    reply = agent_reply(req.message, req.history or [])
    return ChatResponse(reply=reply, timestamp=now_israel().isoformat())


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
<div><h1><span class="dot"></span>my-ai-agent</h1><p>הסוכן האישי שלך - מחובר ופעיל</p></div>
<button class="clear" onclick="clearChat()">נקה צאט</button>
</header>
<div id="chat"></div>
<div class="examples">
<button onclick="ask('עזרה')">עזרה</button>
<button onclick="ask('מה השעה?')">מה השעה?</button>
<button onclick="ask('חשב 12*8+5')">חשב 12*8+5</button>
<button onclick="ask('מי אתה?')">מי אתה?</button>
</div>
<form onsubmit="send(event)">
<input id="inp" placeholder="כתוב הודעה לסוכן..." autocomplete="off">
<button class="send" type="submit">שלח</button>
</form>
<footer>Python FastAPI על Render - Frankfurt - Free Plan</footer>
</div>
<script>
let history=[];
const chat=document.getElementById('chat');
const inp=document.getElementById('inp');
function addMsg(t,c){const d=document.createElement('div');d.className='msg '+c;d.textContent=t;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d;}
addMsg('היי! אני הסוכן שלך. כתוב לי משהו או לחץ על אחת הדוגמאות למטה.','bot');
function ask(t){inp.value=t;send(new Event('submit'));}
async function send(e){e.preventDefault();const text=inp.value.trim();if(!text)return;inp.value='';addMsg(text,'user');history.push({role:'user',content:text});const tp=addMsg('מקליד...','bot typing');try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,history:history})});const j=await r.json();tp.remove();addMsg(j.reply,'bot');history.push({role:'assistant',content:j.reply});}catch(err){tp.remove();addMsg('שגיאת חיבור לשרת. נסה שוב.','bot');}}
function clearChat(){chat.innerHTML='';history=[];addMsg('הצאט נוקה. איך אפשר לעזור?','bot');}
</script>
</body>
</html>
"""
