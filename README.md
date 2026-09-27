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
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | |
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

## API

| נתיב | שיטה | תיאור |
|---|---|---|
| `/` | GET | ממשק צ'אט |
| `/healthz` | GET/HEAD | בדיקת חיים |
| `/api/info` | GET | ספק פעיל, כלים, מצב |
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
