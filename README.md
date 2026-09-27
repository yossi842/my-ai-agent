# my-ai-agent — הסוכן שלך על Render

סוכן AI בעברית, מבוסס Python + FastAPI, עם ממשק צ'אט מובנה.
מוכן להעלאה ל-Render לפי ההגדרות שלך.

## קבצים
- `main.py` — השרת + הסוכן + הממשק (נקודת כניסה: `main:app`)
- `requirements.txt` — תלויות
- `render.yaml` — הגדרת Blueprint ל-Render
- `.gitignore`

## הרצה מקומית
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
פתח: http://localhost:8000

## העלאה ל-Render (לפי הצעדים שלך)

1. העלה את כל הקבצים לריפו ב-GitHub (ענף `main`).
2. כנס ל-dashboard.render.com → כפתור New + → Web Service.
3. חבר את הריפו: Connect a repository → Connect.
4. מלא בדיוק:
   - Name: `my-ai-agent`
   - Language: `Python 3`
   - Branch: `main`
   - Region: `Frankfurt (EU Central)`
   - Root Directory: ריק
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Instance Type: `Free ($0/month)`.
6. לחץ `Create Web Service`.
7. אחרי 1-2 דקות הסטטוס יהיה Live ירוק ותקבל כתובת כמו `https://my-ai-agent.onrender.com`.

## API
- `GET /` — ממשק הצ'אט
- `GET /healthz` — בדיקת חיים
- `GET /api/info` — מידע
- `POST /api/chat` — `{"message":"היי","history":[]}` → `{"reply":"...","timestamp":"..."}`

## שדרוג עם מודל חכם
ב-Render → Environment → Add Env Var:
- `OPENAI_API_KEY` = המפתח שלך
- `OPENAI_MODEL` = gpt-4o-mini (אופציונלי)

עשה Manual Deploy → Redeploy, והסוכן יענה דרך OpenAI אוטומטית.
