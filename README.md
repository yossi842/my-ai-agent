# my-ai-agent — סוכן AI אוטונומי בעברית

סוכן AI עם לולאת כלים אמיתית, Python + FastAPI, רץ על Render בתוכנית חינמית.
כל הממשק והתשובות בעברית.

## מה הסוכן יודע לעשות

לולאה אמיתית של `חשוב → מפעיל כלי → מתבונן → חוזר` עד שמגיע לתשובה:

| כלי | מה הוא עושה |
|---|---|
| `current_time` | תאריך ושעה בישראל |
| `calculate` | ביטויים מתמטיים — כולל `sqrt`, `^`, `factorial` |
| `wikipedia_search` | חיפוש בוויקיפדיה העברית |
| `wikipedia_page` | קריאת פתיחת ערך מוויקיפדיה |
| `read_url` | הורדת דף וחילוץ טקסט קריא |
| `remember` | שמירת עובדה לזיכרון קבוע |
| `recall` | קריאה מהזיכרון |
| `forget` | מחיקת עובדה |

בנוסף: **זיכרון שיחה** שנשמר בין בקשות לפי `sessionId`, ו**תשובה מקומית מיידית** ל"מה השעה?" ו"חשב 12*8+5" בלי לקרוא למודל.

## הגדרה

| משתנה | ברירת מחדל | תיאור |
|---|---|---|
| `GROQ_API_KEY` | — | **מומלץ.** חינם, בלי כרטיס — [console.groq.com/keys](https://console.groq.com/keys) |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | |
| `GEMINI_API_KEY` | — | חלופה |
| `GEMINI_MODEL` | `gemini-2.0-flash` | |
| `OPENAI_API_KEY` | — | חלופה |
| `OPENAI_MODEL` | `gpt-4o-mini` | |
| `AGENT_API_KEY` | ריק | אם מוגדר — `/api/chat` דורש כותרת `X-API-Key` |
| `ALLOWED_ORIGINS` | ריק | ריק = דפדפן בלבד. לפתיחה לדומיין חיצוני: `https://x.com` |
| `RATE_LIMIT_PER_MIN` | `20` | הגבלת קצב לפי IP |
| `AGENT_MAX_STEPS` | `8` | מקסימום קריאות כלים בריצה |
| `AGENT_TIMEOUT` | `90` | טיימאוט לריצה בשניות |
| `AGENT_TOOLS` | `1` | `0` מבטל את הכלים (צ'אט פשוט) |
| `AGENT_TEMPERATURE` | `0.3` | |
| `AGENT_MAX_CONCURRENCY` | `4` | הרצות מקביליות מרביות |
| `MEMORY_MAX_MESSAGES` | `12` | כמה הודעות מהשיחה נשמרות |
| `AGENT_SYSTEM_PROMPT` | ריק | מאפיין את הסוכן |

סדר עדיפות הספקים: **Groq → OpenAI → Gemini**. אם אחד נכשל, עוברים לבא.

## סוכן קולי בטלפון (Twilio)

השרת יכול לקבל שיחות טלפון ולנהל אותן בקול מלא בעברית.

### ⚠️ המלכודת הגדולה ביותר בעברית

| פעולה | קוד שפה **נכון** | מה קורה אם כותבים `he-IL` במקום |
|---|---|---|
| הקראה קולית `<Say>` | `he-IL` | — |
| זיהוי דיבור `<Gather>` | **`iw-IL`** | Warning 13331 → הטקסט חוזר **באנגלית**, בלי שגיאה גלויה |

בנוסף: **עברית אינה נתמכת ב-Google STT v2**, ולכן אין להגדיר `speechModel="google_v2"`.

קולות עברית זמינים:
`Google.he-IL-Wavenet-A` (נקבה) · `Google.he-IL-Wavenet-B` (זכר) · `Google.he-IL-Standard-A/B/C/D` (רגיל, זול יותר)

### הגדרה

1. חשבון ב-[twilio.com](https://www.twilio.com) → קנה מספר ישראלי (או השתמש ב-`+15005550006` לבדיקות)
2. העתק את `Account SID` ואת `Auth Token`
3. ב-Render הוסף:
   - `VOICE_ENABLED` = `1`
   - `TWILIO_AUTH_TOKEN` = ה-Auth Token
   - `VOICE_BASE_URL` = `https://my-ai-agent-wpuz.onrender.com`
4. ב-Twilio Console → Phone Numbers → המספר שלך → **A Call Comes In** → Webhook:
   `https://my-ai-agent-wpuz.onrender.com/voice/webhook` (POST)
5. השתמש ב-HTTP POST בלבד — החתימה נבדקת מול `X-Twilio-Signature`

### משתנים

| משתנה | ברירת מחדל | תיאור |
|---|---|---|
| `VOICE_ENABLED` | `0` | הפעלת הסוכן הקולי |
| `TWILIO_AUTH_TOKEN` | — | **חובה** לאימות חתימות |
| `VOICE_BASE_URL` | — | כתובת השרת הציבורית |
| `VOICE_GREETING` | `שלום, אני הסוכן שלך. איך אפשר לעזור?` | בריכה |
| `VOICE_VOICE` | `Google.he-IL-Wavenet-A` | קול |
| `VOICE_LANG` | `he-IL` | שפת ההקראה |
| `VOICE_STT_LANG` | `iw-IL` | שפת זיהוי הדיבור |
| `VOICE_MAX_TURNS` | `12` | מקסימום תורות בשיחה |
| `VOICE_MAX_SAY_CHARS` | `450` | אורך מקסימלי לתשובה קולית |

### עלות — לא חינם

| רכיב | מחיר |
|---|---|
| שיחה למספר ישראלי | ≈ $0.01 לדקה |
| זיהוי דיבור (`<Gather>`) | מחויב בנפרד לדקת דיבור |
| הקראה קולית | חינמית בקולות בסיסיים, בתשלום בנוירליים |

**השרת עצמו נשאר חינמי** — העלות היא רק על השיחה עצמה.

### הזרימה

```
חיוג → /voice/webhook → <Say> בריכה → <Gather input="speech" language="iw-IL">
      → /voice/turn  ← SpeechResult (טקסט עברי)
      → הסוכן חושב (עם כלים) → <Say> התשובה → <Gather> שוב
      → עד VOICE_MAX_TURNS → נפרד → <Hangup>
```

`GET /voice/status` מחזיר את מצב שכבת הטלפוניה.

### למה יש כאן `User-Agent` של דפדפן

`urllib` של Python שולח `User-Agent: Python-urllib/3.x`. מאחורי `api.groq.com` יושבת Cloudflare עם
**Browser Integrity Check** שחוסם כל `User-Agent` שאינו דפדפן ומחזיר:

```
HTTP 403: error code: 1010
```

זה נראה בדיוק כמו מפתח לא תקין, אבל זו לא הבעיה — הפתרון הוא להציג עצמנו כלקוח רגיל
(ראה `_api_headers` ב-`agent.py`).

### למה יש רשימת מודלי גיבוי

Groq מוציא מודלים מהקטלוג מדי פעם. `llama-3.3-70b-versatile` — שהיה ברירת המחדל הישנה — הוסר,
ואז כל בקשה החזירה `404 ... does not exist`. לכן `MODEL_FALLBACKS` ב-`agent.py` מנסה מודלים
אחרים אוטומטית. `GET /api/models` מחזיר את המודלים שהמפתח שלך רשאי להשתמש בהם.

## API

| נתיב | שיטה | תיאור |
|---|---|---|
| `/` | GET | ממשק צ'אט |
| `/healthz` | GET/HEAD | בדיקת חיים |
| `/api/info` | GET | ספק פעיל, כלים, מצב |
| `/api/models` | GET | אילו מודלים המפתח רשאי להשתמש בהם (אבחון) |
| `/api/transcribe` | POST | שמע → טקסט (גוף בקשה = קובץ שמע גולמי) |
| `/api/stt` | GET | אילו מנועי תמלול זמינים |
| `/voice/webhook` | POST | כניסת שיחה מ-Twilio |
| `/voice/turn` | POST | כל תור בשיחה |
| `/voice/status` | GET | מצב שכבת הטלפון |
| `/api/chat` | POST | `{"message":"...","sessionId":"s1"}` |
| `/api/notes` | GET | הזיכרון הקבוע |
| `/api/history?sessionId=s1` | GET | היסטוריית שיחה |
| `/api/docs` | GET | תיעוד Swagger |

```bash
curl -X POST https://my-ai-agent-wpuz.onrender.com/api/chat \
  -H "content-type: application/json" \
  -H "X-API-Key: YOUR_KEY" \
  -d '{"message":"מי היה אלכסנדר פוסקין?","sessionId":"demo"}'
```

התשובה: `{"answer":"...","provider":"groq:llama-3.3-70b-versatile","steps":[...],"used_tools":true,"duration_ms":4200}`

## הרצה מקומית

```bash
pip install -r requirements.txt
export GROQ_API_KEY=...
uvicorn main:app --reload --port 8000
```

## מבנה

```
main.py    שרת FastAPI, נתיבים, אבטחה, ממשק הדפדפן
agent.py   לולאת הסוכן, חיבור לספקי המודל, זיכרון שיחה
tools.py   הכלים + מנוע החישובים (AST, ללא eval)
```

## ⚠️ מגבלות התוכנית החינמית

- **השרת נרדם אחרי 15 דקות** של חוסר פעילות. הבקשה הראשונה אחרי כך עלולה לקחת ~50 שניות.
- **המערכת קבצים זמנית** — הזיכרון (`data/`) נמחק בכל deploy. לשמירה קבועה צריך Render Disk (בתשלום) או מסד נתונים חיצוני.
- **750 שעות/חודש** של זמן מעבד.
- **אין SSH/Shell** — ניהול דרך ה-API או ה-Dashboard בלבד.
