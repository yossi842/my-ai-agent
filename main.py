"""
my-ai-agent v3 — סוכן AI אוטונומי בעברית, Python + FastAPI על Render.

נקודת כניסה: uvicorn main:app --host 0.0.0.0 --port $PORT

נתיבים:
  GET  /                 ממשק צ'אט
  GET  /healthz          בדיקת חיים (גם ל-HEAD) — ברירת מחדל של Render
  GET  /api/info         מידע: ספק פעיל, כלים, מצב
  POST /api/chat         הרצת סוכן. דורש X-API-Key כשמוגדר AGENT_API_KEY
  GET  /api/notes        צפייה בזיכרון הקבוע
  GET  /api/history      היסטוריית שיחה לפי sessionId
"""
from __future__ import annotations

import hmac
import os
import re
import time
from collections import defaultdict, deque
from typing import List

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from agent import providers, run_agent_limited
from stt import stt_backends as _stt_backends, transcribe as transcribe_audio
from tools import TOOL_SCHEMAS, _load_notes

VERSION = "3.0.0"

app = FastAPI(title="my-ai-agent", version=VERSION, docs_url="/api/docs", redoc_url=None)

# ------------------------------------------------------------------- settings
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


API_KEY = os.getenv("AGENT_API_KEY", "").strip()
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
]
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "20") or 20)
MAX_CONCURRENT = int(os.getenv("AGENT_MAX_CONCURRENCY", "4") or 4)
VOICE_BASE_URL = os.getenv("VOICE_BASE_URL", "").strip()

# CORS: כברירת מחדל סגור לגמרי (same-origin בלבד). לפתיחה לדומיין חיצוני -
# הגדר ALLOWED_ORIGINS=https://example.com
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# ------------------------------------------------------------------ rate limit
_hits: dict = defaultdict(deque)


def rate_limit(request: Request) -> None:
    if RATE_LIMIT_PER_MIN <= 0:
        return
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="יותר מדי בקשות. נסה שוב בעוד דקה.")
    q.append(now)
    if len(_hits) > 2000:  # ניקוי זיכרון
        for key in [k for k, v in _hits.items() if not v]:
            _hits.pop(key, None)


def require_key(x_api_key: str = Header(default="")) -> None:
    """מאמת את מפתח ה-API. אם לא הוגדר AGENT_API_KEY — מותר (מצב פתוח)."""
    if not API_KEY:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key.strip(), API_KEY):
        raise HTTPException(status_code=401, detail="מפתח API לא חוקי (חסר כותרת X-API-Key).")


# --------------------------------------------------------------------- models
class ChatTurn(BaseModel):
    role: str = "user"
    content: str = Field(default="", max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    sessionId: str = Field(default="default", max_length=64)
    history: List[ChatTurn] = Field(default_factory=list, max_length=20)

    def clean_session(self) -> str:
        """מנקה את מזהה השיחה — מונע הרמת קבצים משונים בשרת."""
        return re.sub(r"[^A-Za-z0-9_:\-]", "", self.sessionId)[:64] or "default"


# ---------------------------------------------------------------------- pages
@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz() -> dict:
    return {"status": "ok", "version": VERSION, "provider": providers()[0]["id"] if providers() else "local"}


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


@app.get("/api/info")
def info() -> dict:
    provs = providers()
    return {
        "name": "my-ai-agent",
        "version": VERSION,
        "status": "ok",
        "agent_mode": True,
        "provider": f"{provs[0]['id']}:{provs[0]['model']}" if provs else "local",
        "providers_available": [p["id"] for p in provs],
        "stt_backends": [b["id"] for b in _stt_backends()],
        "voice_agent": os.getenv("VOICE_ENABLED", "0") in ("1", "true", "yes", "on"),
        "auth_required": bool(API_KEY),
        "cors_origins": ALLOWED_ORIGINS or "same-origin only",
        "rate_limit_per_min": RATE_LIMIT_PER_MIN,
        "max_concurrent_runs": MAX_CONCURRENT,
        "tools": [t["function"]["name"] for t in TOOL_SCHEMAS],
        "notes_stored": len(_load_notes()),
        "uptime_sec": int(time.time() - os.stat("/proc/self").st_ctime) if os.path.exists("/proc/self") else None,
    }


@app.post("/api/transcribe", dependencies=[Depends(rate_limit), Depends(require_key)])
async def transcribe(request: Request) -> JSONResponse:
    """ממיר שמע לטקסט. הגוף הוא קובץ השמע הגולמי, לא JSON."""
    body = await request.body()
    content_type = request.headers.get("content-type", "audio/webm")
    language = request.headers.get("x-language", "he")
    result = transcribe_audio(body, content_type, language)
    return JSONResponse(status_code=200 if "error" not in result else 502, content=result)


@app.get("/api/stt", dependencies=[Depends(require_key)])
def stt_info() -> dict:
    from stt import stt_backends
    return {
        "backends": [{"id": b["id"], "model": b["model"]} for b in stt_backends()],
        "max_bytes": 8 * 1024 * 1024,
        "note": "POST raw audio body to /api/transcribe",
    }


@app.post("/api/chat", dependencies=[Depends(rate_limit), Depends(require_key)])
def chat(req: ChatRequest, provider: str = "") -> JSONResponse:
    session = req.clean_session()
    only = provider.strip().lower() or None
    if only and only not in ("groq", "openai", "gemini"):
        raise HTTPException(status_code=400, detail="provider חייב להיות groq/openai/gemini")
    result = run_agent_limited(
        goal=req.message, session_id=session, provider_filter=only
    )
    status = 200 if result.get("ok") else 503
    return JSONResponse(status_code=status, content=result)


# ============================================================ טלפוניה (Twilio)
@app.get("/voice/status", dependencies=[Depends(require_key)])
def voice_status() -> dict:
    from voice import status_report
    return status_report()


def _twilio_guard(request: Request, form: dict) -> None:
    """מאמת חתימת Twilio. בלי זה, כל אחד יכול להפעיל את הסוכן בחינם."""
    from voice import TWILIO_AUTH_TOKEN, verify_twilio_signature

    if not TWILIO_AUTH_TOKEN:
        return
    base = VOICE_BASE_URL or str(request.base_url).rstrip("/")
    url = f"{base}{request.url.path}"
    sig = request.headers.get("x-twilio-signature", "")
    if not verify_twilio_signature(url, form, sig, TWILIO_AUTH_TOKEN):
        raise HTTPException(status_code=403, detail="Twilio signature invalid")


@app.post("/voice/webhook")
async def voice_webhook(request: Request) -> Response:
    from voice import handle_voice_webhook, VOICE_ENABLED

    form = dict(await request.form())
    _twilio_guard(request, form)
    if not VOICE_ENABLED:
        return Response(_voice_off_twiml(), media_type="application/xml")
    base = VOICE_BASE_URL or str(request.base_url).rstrip("/")
    return Response(handle_voice_webhook(base, form), media_type="application/xml")


@app.post("/voice/turn")
async def voice_turn(request: Request) -> Response:
    from voice import handle_voice_turn, VOICE_ENABLED

    form = dict(await request.form())
    _twilio_guard(request, form)
    if not VOICE_ENABLED:
        return Response(_voice_off_twiml(), media_type="application/xml")
    base = VOICE_BASE_URL or str(request.base_url).rstrip("/")
    return Response(handle_voice_turn(base, form), media_type="application/xml")


def _voice_off_twiml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>'
        '<Say language="he-IL">שירות הטלפון כבוי כרגע.</Say>'
        '<Hangup/></Response>'
    )


@app.get("/api/notes", dependencies=[Depends(require_key)])
def notes() -> dict:
    return {"notes": _load_notes()}


@app.get("/api/models", dependencies=[Depends(require_key)])
def models() -> dict:
    """רשימת המודלים שהמפתח מורשה להשתמש בהם — לאבחון בעיות 403/404."""
    import json as _json
    from urllib.error import HTTPError
    from urllib.request import Request as URLRequest, urlopen

    from agent import BROWSER_UA

    out = []
    for p in providers():
        entry = {
            "provider": p["id"],
            "configured_model": p["model"],
            "available": [],
            "error": None,
        }
        try:
            req = URLRequest(
                p["base_url"].rstrip("/") + "/models",
                headers={
                    "Authorization": f"Bearer {p['api_key']}",
                    "Accept": "application/json",
                    "User-Agent": BROWSER_UA,
                },
            )
            with urlopen(req, timeout=20) as resp:
                data = _json.loads(resp.read().decode())
            entry["available"] = sorted(m.get("id", "") for m in data.get("data", []))
        except HTTPError as e:
            try:
                entry["error"] = f"HTTP {e.code}: " + e.read().decode("utf-8", "replace")[:200]
            except Exception:
                entry["error"] = f"HTTP {e.code}"
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
        out.append(entry)
    return {"providers": out}


@app.get("/api/history", dependencies=[Depends(require_key)])
def history(sessionId: str = "default", limit: int = 20) -> dict:
    from agent import _load_history
    safe = re.sub(r"[^A-Za-z0-9_:\-]", "", sessionId)[:64] or "default"
    return {"sessionId": safe, "messages": _load_history(safe, max(1, min(limit, 100)))}


@app.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    return Response("User-agent: *\nDisallow: /api/\nDisallow: /healthz\n", media_type="text/plain")


@app.exception_handler(500)
def on_500(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": "שגיאה פנימית", "detail": str(exc)[:300]})


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>my-ai-agent — הסוכן שלי</title>
<style>
*{box-sizing:border-box}
:root{--bg:#0b1120;--card:rgba(255,255,255,.06);--line:rgba(255,255,255,.14);--accent:#22c55e;--blue:#3b82f6}
body{margin:0;font-family:'Segoe UI',Arial,sans-serif;
  background:radial-gradient(1200px 600px at 80% -10%,#1e1b4b,transparent),linear-gradient(160deg,#0b1120,#111827);
  color:#e5e7eb;min-height:100vh;display:flex;justify-content:center;padding:18px}
.wrap{width:100%;max-width:820px;display:flex;flex-direction:column;min-height:92vh;
  background:var(--card);border:1px solid var(--line);border-radius:20px;overflow:hidden;backdrop-filter:blur(8px)}
header{padding:16px 20px;background:rgba(0,0,0,.28);border-bottom:1px solid var(--line);
  display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
h1{margin:0;font-size:19px;display:flex;align-items:center;gap:9px}
.dot{width:10px;height:10px;border-radius:50%;background:#94a3b8;box-shadow:0 0 0 3px rgba(148,163,184,.18)}
.dot.live{background:var(--accent);box-shadow:0 0 0 3px rgba(34,197,94,.2)}
.badge{font-size:12px;padding:5px 10px;border-radius:999px;background:rgba(255,255,255,.09);
  border:1px solid var(--line);white-space:nowrap}
#log{flex:1;padding:18px 20px;display:flex;flex-direction:column;gap:12px;overflow-y:auto;max-height:64vh}
.msg{max-width:88%;padding:11px 14px;border-radius:14px;line-height:1.65;white-space:pre-wrap;
  word-wrap:break-word;font-size:15px}
.user{align-self:flex-start;background:var(--blue);color:#fff}
.bot{align-self:flex-end;background:rgba(255,255,255,.1);border:1px solid var(--line)}
.bot .prov{font-size:11px;opacity:.55;margin-top:6px;display:block}
.err{align-self:flex-end;background:rgba(239,68,68,.16);border:1px solid rgba(239,68,68,.4)}
.steps{font-size:12px;opacity:.7;margin-top:6px;border-top:1px dashed var(--line);padding-top:6px}
.examples{display:flex;flex-wrap:wrap;gap:8px;padding:0 20px 10px}
.examples button{background:rgba(255,255,255,.08);border:1px solid var(--line);color:#e5e7eb;
  border-radius:18px;padding:7px 12px;cursor:pointer;font-size:13px}
.examples button:hover{background:rgba(255,255,255,.16)}
form{display:flex;gap:10px;padding:14px 20px;background:rgba(0,0,0,.28);border-top:1px solid var(--line);align-items:center}
input{flex:1;padding:12px 14px;border-radius:12px;border:1px solid var(--line);
  background:rgba(255,255,255,.07);color:#fff;font-size:15px;outline:none}
input:focus{border-color:var(--accent)}
button.send{background:var(--accent);border:none;color:#052e16;font-weight:700;border-radius:12px;
  padding:0 20px;cursor:pointer;font-size:15px}
button.send:disabled{opacity:.5;cursor:not-allowed}
#mic{width:46px;height:46px;min-width:46px;border-radius:50%;border:1px solid var(--line);
  background:rgba(255,255,255,.08);color:#e5e7eb;cursor:pointer;font-size:19px;line-height:1}
#mic.rec{background:rgba(239,68,68,.85);border-color:#ef4444;animation:pulse 1.1s infinite}
#mic:disabled{opacity:.4;cursor:not-allowed}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(239,68,68,.7)}100%{box-shadow:0 0 0 14px rgba(239,68,68,0)}}
#tts{width:46px;height:46px;min-width:46px;border-radius:50%;border:1px solid var(--line);
  background:rgba(255,255,255,.08);color:#e5e7eb;cursor:pointer;font-size:17px;line-height:1}
#tts.on{background:var(--accent);color:#052e16}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.ghost{background:transparent;border:1px solid var(--line);color:#e5e7eb;border-radius:10px;
  padding:6px 11px;cursor:pointer;font-size:12px}
.typing{opacity:.65}
footer{text-align:center;font-size:12px;opacity:.55;padding:9px}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1><span class="dot" id="dot"></span>my-ai-agent</h1>
  <div style="display:flex;gap:8px;align-items:center">
    <span class="badge" id="prov">טוען…</span>
    <button class="ghost" onclick="reset()">ניקוי</button>
  </div>
</header>
<div id="log"></div>
<div class="examples">
  <button onclick="ask('מה המזג בתל אביב?')">מזג</button>
  <button onclick="ask('מי היה אלכסנדר פוסקין?')">פוסקין</button>
  <button onclick="ask('חשב 12*8+5')">חישוב</button>
  <button onclick="ask('זכור שאני מתכנן טיול ליפאן באוקטובר')">זיכרון</button>
  <button onclick="ask('מה שמרת ממני?')">מה זכרת</button>
</div>
<form onsubmit="send(event)">
  <button id="mic" type="button" title="הקלדה מדיבור (מיקרופון)" aria-label="הקלדה מדיבור">🎙️</button>
  <input id="inp" placeholder="כתוב משימה לסוכן, או לחץ על המיקרופון ודבר…" autocomplete="off" maxlength="8000">
  <button id="tts" type="button" title="הקרא את התשובות בקול" aria-label="הקרא תשובות בקול">🔊</button>
  <button class="send" id="btn" type="submit">שלח</button>
</form>
<footer id="foot">Render · Frankfurt · Free</footer>
</div>
<script>
const log=document.getElementById('log'), inp=document.getElementById('inp'),
      btn=document.getElementById('btn'), dot=document.getElementById('dot'),
      provEl=document.getElementById('prov');
let KEY=new URLSearchParams(location.search).get('key')||localStorage.getItem('agent_key')||'';
const SID='s-'+Math.random().toString(36).slice(2,10);
let history=[];

fetch('/api/info').then(r=>r.json()).then(j=>{
  provEl.textContent=j.provider;
  dot.classList.toggle('live', j.providers_available.length>0);
  document.getElementById('foot').textContent =
    'Render · Frankfurt · '+(j.providers_available.length?j.tools.length+' כלים פעילים':'ללא מפתח מודל');
}).catch(()=>{provEl.textContent='שגיאת חיבור'});

function add(t,c,extra){
  const d=document.createElement('div');
  d.className='msg '+c; d.textContent=t;
  if(extra){const s=document.createElement('span');s.className='prov';s.textContent=extra;d.appendChild(s);}
  log.appendChild(d); log.scrollTop=log.scrollHeight; return d;
}
function addSteps(node,steps){
  if(!steps||!steps.length) return;
  const s=document.createElement('div');
  s.className='steps';
  s.textContent='כלים: '+steps.map(x=>x.tool).join(', ');
  node.appendChild(s);
}
add('היי! אני סוכן AI אוטונומי. אני יכול לחשב, לחפש בוויקיפדיה, לקרוא אתרים ולזכור עובדות ביניינו. מה לעשות?','bot');
function ask(t){inp.value=t; send(new Event('submit'));}
function reset(){log.innerHTML='';history=[];add('נוקה. מה לעשות עכשיו?','bot');}

/* ================= קלט קולי =================
   מסלול 1 (מועדף): Web Speech API של הדפדפן - מקומי, מיידי, בחינם, תומך בעברית.
   מסלול 2 (גיבוי): הקלטה דרך MediaRecorder + שליחה לשרת, שמתמלל
   בעזרת whisper של Groq או gemini-transcribe. מתאים לדפדפנים בלי Web Speech
   (Firefox, Safari) ולמכשירים ניידים. */
const mic=document.getElementById('mic'), ttsBtn=document.getElementById('tts');
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
let rec=null, recActive=false, mr=null, mrChunks=[];

function stopSpeech(){
  if(rec){ try{rec.stop()}catch(e){} }
  if(mr&&mr.state!=='inactive'){ try{mr.stop()}catch(e){} }
  mic.classList.remove('rec'); mic.textContent='🎙️'; recActive=false;
}
mic.addEventListener('click',()=>{ recActive?stopSpeech():startListening(); });

function startListening(){
  if(SR){ startWebSpeech(); } else { startRecorder(); }
}

function startWebSpeech(){
  rec=new SR();
  rec.lang='he-IL'; rec.interimResults=true; rec.continuous=false; rec.maxAlternatives=1;
  recActive=true; mic.classList.add('rec'); mic.textContent='⏹️';
  let base=inp.value.trim()?inp.value.trim()+' ':'';
  rec.onresult=e=>{
    let final='',interim='';
    for(let i=e.resultIndex;i<e.results.length;i++){
      const t=e.results[i][0].transcript;
      e.results[i].isFinal?final+=t:interim+=t;
    }
    inp.value=base+(final||interim);
    if(final){ stopSpeech(); inp.focus(); }
  };
  rec.onerror=e=>{
    if(e.error==='not-allowed'||e.error==='service-not-allowed'){
      add('אין הרשאה למיקרופון. אפשר לחלופין להקליד או להשתמש בהקלטה.','err');
      stopSpeech();
    } else if(e.error!=='aborted' && e.error!=='no-speech'){
      add('בעיית זיהוי דיבור: '+e.error,'err'); stopSpeech();
    }
  };
  rec.onend=()=>{ if(recActive) stopSpeech(); };
  try{ rec.start(); }catch(e){ stopSpeech(); }
}

async function startRecorder(){
  if(!navigator.mediaDevices||!window.MediaRecorder){
    add('הדפדפן הזה לא תומך בהקלטה. אפשר להשתמש בדפדפן Chrome או Edge.','err');
    return;
  }
  let stream;
  try{ stream=await navigator.mediaDevices.getUserMedia({audio:true}); }
  catch(e){ add('אין הרשאה למיקרופון.','err'); return; }
  mrChunks=[];
  try{ mr=new MediaRecorder(stream); } catch(e){ add('הקלטה לא נתמכה בדפדפן הזה.','err'); return; }
  mr.ondataavailable=e=>{ if(e.data && e.data.size) mrChunks.push(e.data); };
  mr.onstop=async()=>{
    stream.getTracks().forEach(t=>t.stop());
    const blob=new Blob(mrChunks,{type:mr.mimeType||'audio/webm'});
    stopSpeech();
    if(blob.size<1200){ add('לא הקלטתי כלום.','err'); return; }
    add('מתמלל…','bot typing');
    const pending=log.lastElementChild;
    try{
      const headers={'Content-Type':blob.type};
      if(KEY) headers['X-API-Key']=KEY;
      const r=await fetch('/api/transcribe',{method:'POST',headers,body:blob});
      const j=await r.json();
      pending.remove();
      if(j.error){ add('תמלול נכשל: '+j.error,'err'); return; }
      const said=(j.text||'').trim();
      if(!said){ add('לא הצלחתי לזהות דיבור.','err'); return; }
      inp.value=said;
      add('🎤 '+said,'user'); history.push({role:'user',content:said});
      await submitText(said);
    }catch(err){
      pending.remove();
      add('שגיאה בתמלול.','err');
    }
  };
  mr.start();
  recActive=true; mic.classList.add('rec'); mic.textContent='⏹️';
}

/* ================= הקראה קולית ================= */
let ttsOn=localStorage.getItem('agent_tts')==='1';
function applyTts(){
  ttsBtn.classList.toggle('on',ttsOn);
  ttsBtn.textContent=ttsOn?'🔊':'🔇';
  ttsBtn.title=ttsOn?'הקראה קולית פעילה':'הקראה קולית כבויה';
}
applyTts();
ttsBtn.addEventListener('click',()=>{
  ttsOn=!ttsOn; localStorage.setItem('agent_tts',ttsOn?'1':'0'); applyTts();
  if(!ttsOn && window.speechSynthesis) window.speechSynthesis.cancel();
});
function speak(text){
  if(!ttsOn||!window.speechSynthesis) return;
  try{
    window.speechSynthesis.cancel();
    const u=new SpeechSynthesisUtterance(text);
    u.lang='he-IL'; u.rate=1.02;
    const vs=window.speechSynthesis.getVoices();
    const he=vs.find(v=>/^he([-_]|$)/i.test(v.lang));
    if(he) u.voice=he;
    window.speechSynthesis.speak(u);
  }catch(e){}
}

async function send(e){
  e.preventDefault();
  const text=inp.value.trim();
  if(!text) return;
  inp.value=''; add(text,'user');
  history.push({role:'user',content:text});
  await submitText(text);
}

async function submitText(text){
  const typing=add('מקליד…','bot typing');
  btn.disabled=true;
  const t0=performance.now();
  try{
    const headers={'Content-Type':'application/json'};
    if(KEY) headers['X-API-Key']=KEY;
    const r=await fetch('/api/chat',{method:'POST',headers,
      body:JSON.stringify({message:text,sessionId:SID,history:history.slice(-10)})});
    if(r.status===401){
      const k=prompt('השרת דורש מפתח API. הדבק את הערך של AGENT_API_KEY:');
      if(k){ localStorage.setItem('agent_key',k.trim()); location.search='?key='+encodeURIComponent(k.trim()); }
      typing.className='msg err'; typing.textContent='נדרש מפתח API — הדבק אותו כדי להמשיך.';
      return;
    }
    const j=await r.json();
    typing.className='msg bot';
    if(j.error && !j.answer){ typing.className='msg err'; typing.textContent=j.detail||j.error; }
    else{
      typing.textContent=j.answer||'אין תשובה.';
      if(j.steps) addSteps(typing,j.steps);
      const tag=j.provider+' · '+(j.duration_ms||Math.round(performance.now()-t0))+'ms'
        +(j.used_tools?' · עם כלים':'');
      const s=document.createElement('span'); s.className='prov'; s.textContent=tag; typing.appendChild(s);
      history.push({role:'assistant',content:j.answer||''});
      speak(j.answer||'');
    }
  }catch(err){
    typing.className='msg err'; typing.textContent='שגיאת חיבור לשרת. נסה שוב.';
  }finally{
    btn.disabled=false; typing.classList.remove('typing'); inp.focus();
  }
}
</script>
</body>
</html>
"""
