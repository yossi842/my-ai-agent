"""
כלי אינטרנט לסוכן: חיפוש, קריאת דפים, וגישה לשירת הדפדפן.

עיקרון מנחה - הדפדפן הוא כלי משלים, לא כלי ראשי:

  מצוא מידע   -> web_search / fetch_page   (ללא דפדפן, מהיר, ללא חסימות)
  קורא דף      -> fetch_page                (ללא דפדפן כשאפשר)
  מתחבר/לוחץ   -> browser_*                 (רק כשצריך אינטראקציה)

הכלל נובע ממדידה: אתרים עם הגנת בוטים (DuckDuckGo, חלק מהמסחרים) חוסמים
כתובת-IP של data center ומציגים CAPTCHA. לכן עדיף תמיד לנסות קודם
את שכבת ה-HTTP, ורק אם היא לא מספיקה - לעבור לדפדפן.
"""
from __future__ import annotations

import html
import json
import os
import re
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request as URLRequest, urlopen

BROWSER_URL = os.getenv("BROWSER_URL", "").strip().rstrip("/")
BROWSER_TOKEN = os.getenv("BROWSER_TOKEN", "").strip()
# השירת הדפדפן זמינה, אך Render Free יכול להירדם - ההפעלה הראשונה
# אחרי שינה אורכת כ-50 שניות. לכן פסק ראשון ארוך והבאים קצרים.
BROWSER_TIMEOUT_COLD = int(os.getenv("BROWSER_TIMEOUT_COLD", "80") or 80)
BROWSER_TIMEOUT_WARM = int(os.getenv("BROWSER_TIMEOUT_WARM", "45") or 45)
MAX_FETCH_CHARS = int(os.getenv("MAX_FETCH_CHARS", "8000") or 8000)

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

_warm = False


def _get(url: str, timeout: int = 25, headers: Optional[dict] = None) -> str:
    req = URLRequest(url, headers={"User-Agent": _UA, "Accept-Language": "he,en;q=0.8", **(headers or {})})
    with urlopen(req, timeout=timeout) as resp:
        charset = "utf-8"
        try:
            charset = resp.headers.get_content_charset() or "utf-8"
        except Exception:
            pass
        return resp.read().decode(charset, errors="replace")


def _clean_text(doc: str) -> str:
    doc = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", doc)
    doc = re.sub(r"(?s)<!--.*?-->", " ", doc)
    doc = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section)>", "\n", doc)
    doc = re.sub(r"<[^>]+>", " ", doc)
    doc = html.unescape(doc)
    doc = re.sub(r"[ \t\r\f\v]+", " ", doc)
    doc = re.sub(r"\n\s*\n+", "\n\n", doc)
    return doc.strip()


# ============================================================ 1) web_search
def _ddg(query: str) -> List[dict]:
    """חיפוש ב-DuckDuckGo דרך ה-HTML endpoint. ללא מפתח, ללא שירת עזר."""
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    doc = _get(url, timeout=25)
    results: List[dict] = []
    # קישורי תוצאות מגיעים כהפניה מוסתרת: //duckduckgo.com/l/?uddg=<urlencoded>
    for m in re.finditer(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', doc, re.I | re.S):
        href, title = m.group(1), _clean_text(m.group(2))
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/l/" in href:
            qs = parse_qs(urlparse(href).query)
            if "uddg" in qs:
                href = qs["uddg"][0]
        if href.startswith("http") and title:
            results.append({"title": title[:160], "url": href})
        if len(results) >= 10:
            break
    if not results:
        # נפילה חזרה למנוע lite
        doc2 = _get("https://lite.duckduckgo.com/lite/?q=" + quote_plus(query), timeout=20)
        for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>', doc2, re.I | re.S):
            results.append({"title": _clean_text(m.group(2))[:160], "url": m.group(1)})
            if len(results) >= 10:
                break
    return results


def _wikipedia_search(query: str, limit: int = 5) -> List[dict]:
    api = ("https://he.wikipedia.org/w/api.php?action=query&list=search&format=json"
           f"&srlimit={limit}&srnamespace=0&srsearch={quote_plus(query)}")
    data = json.loads(_get(api, timeout=20))
    return [{"title": h.get("title"),
             "url": "https://he.wikipedia.org/wiki/" + quote_plus(h.get("title", "").replace(" ", "_")),
             "snippet": re.sub(r"<[^>]+>", "", h.get("snippet", ""))}
            for h in data.get("query", {}).get("search", [])]


def _mojeek(query: str) -> List[dict]:
    """Mojeek - מנוע חיפוש עצמאי וקטן, נאנה לבוטים (רישיון ציבורי לשימוש).

    נוסף כיוצא ממדידה: DuckDuckGo חוסם כתובות-IP של data center
    (כפי שנראה גם בדפדפן - CAPTCHA). Mojeek מאפשר גישה תכנותית.
    """
    doc = _get("https://www.mojeek.com/search?q=" + quote_plus(query), timeout=20)
    results: List[dict] = []
    for m in re.finditer(r'<a[^>]+class="ob"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', doc, re.I | re.S):
        href, title = m.group(1), _clean_text(m.group(2))
        if href.startswith("http") and title:
            results.append({"title": title[:160], "url": href})
        if len(results) >= 10:
            break
    if not results:
        for m in re.finditer(r'<h2><a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', doc, re.I | re.S):
            results.append({"title": _clean_text(m.group(2))[:160], "url": m.group(1)})
            if len(results) >= 10:
                break
    return results


def _post_json(url: str, payload: dict, timeout: int = 25, headers: Optional[dict] = None) -> dict:
    req = URLRequest(url, data=json.dumps(payload).encode(), method="POST",
                     headers={"Content-Type": "application/json", "User-Agent": _UA, **(headers or {})})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _tavily(query: str, limit: int) -> List[dict]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return []
    data = _post_json("https://api.tavily.com/search", {
        "api_key": key, "query": query, "max_results": limit,
        "search_depth": "basic", "include_answer": True,
    }, timeout=30)
    out = [{"title": r.get("title", "")[:160], "url": r.get("url", ""),
            "snippet": (r.get("content") or "")[:300]} for r in data.get("results", [])]
    if data.get("answer") and out:
        out.insert(0, {"title": "READY_ANSWER", "url": "", "snippet": str(data["answer"])[:400]})
    return out


def _serper(query: str, limit: int) -> List[dict]:
    key = os.getenv("SERPER_API_KEY", "").strip()
    if not key:
        return []
    data = _post_json("https://google.serper.dev/search", {"q": query, "num": limit},
                      timeout=25, headers={"X-API-KEY": key})
    out = [{"title": (r.get("title") or "")[:160], "url": r.get("link", ""),
            "snippet": (r.get("snippet") or "")[:300]}
           for r in data.get("organic", [])]
    box = data.get("answerBox") or {}
    snippet = box.get("answer") or box.get("snippet") or ""
    if snippet:
        out.insert(0, {"title": "READY_ANSWER", "url": box.get("link", ""), "snippet": str(snippet)[:400]})
    return out


def _t_web_search(query: str = "", limit: int = 8) -> Dict[str, Any]:
    """חפש באינטרנט. מוחזר רשימת תוצאות עם כותרת, כתובת ותקציר."""
    query = (query or "").strip()
    if not query:
        return {"error": "חסר query"}
    limit = max(1, min(int(limit or 8), 15))
    errors = []

    # סדר העדפה: Mojeek (לא חוסם) -> DuckDuckGo -> Wikipedia
    engines = (
        ("tavily", lambda q: _tavily(q, limit)),
        ("serper", lambda q: _serper(q, limit)),
        ("mojeek", _mojeek),
        ("duckduckgo", _ddg),
        ("wikipedia", lambda q: _wikipedia_search(q, limit)),
    )
    for name, fn in engines:
        try:
            res = fn(query)
            if res:
                return {"query": query, "engine": name, "count": len(res), "results": res[:limit]}
            errors.append(f"{name}: אין תוצאות")
        except HTTPError as e:
            errors.append(f"{name}: HTTP {e.code}")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
    return {"query": query, "error": "החיפוש נכשל", "detail": errors,
            "hint": "אפשר לנסות ניסוח אחר, או לפתוח ישירות כתובת עם fetch_page"}


# ============================================================ 2) fetch_page
def _t_fetch_page(url: str = "", maxChars: int = MAX_FETCH_CHARS) -> Dict[str, Any]:
    """הורד דף וחלץ ממנו טקסט קריא. מהיר יותר מדפדפן, ועובד ברוב האתרים."""
    url = (url or "").strip()
    if not url:
        return {"error": "חסר url"}
    if not url.startswith(("http://", "https://")):
        return {"error": "רק http/https מותרים"}
    try:
        raw = _get(url, timeout=25)
    except HTTPError as e:
        return {"url": url, "error": f"HTTP {e.code}",
                "hint": "אם זה אתר עם JS כבד, נסה את הכלי browser_open"}
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {str(e)[:150]}"}

    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    text = _clean_text(raw)
    return {
        "url": url,
        "title": html.unescape(title_m.group(1)).strip()[:200] if title_m else "",
        "chars": len(text),
        "text": text[:maxChars],
        "truncated": len(text) > maxChars,
        "method": "http",
    }


# ============================================================ 3) דפדפן
def _browser(path: str, payload: Optional[dict] = None, session: str = "default") -> Dict[str, Any]:
    global _warm
    if not BROWSER_URL or not BROWSER_TOKEN:
        return {"error": "שירת הדפדפן לא מוגדרת",
                "hint": "הוסף BROWSER_URL ו-BROWSER_TOKEN, או השתמש ב-fetch_page"}

    timeout = BROWSER_TIMEOUT_WARM if _warm else BROWSER_TIMEOUT_COLD
    sep = "&" if "?" in path else "?"
    url = f"{BROWSER_URL}{path}{sep}session={quote_plus(session)}"
    data = json.dumps(payload or {}).encode()
    req = URLRequest(url, data=data if payload is not None else None, headers={
        "Content-Type": "application/json",
        "X-Browser-Token": BROWSER_TOKEN,
        "User-Agent": _UA,
    }, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode())
        _warm = True
        return out
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:250]
        except Exception:
            pass
        _warm = False
        hints = {
            401: "הטוקן של הדפדפן שגוי",
            409: "לא פתוח דף - קרא קודם browser_open",
            429: "יותר מדי פעולות בדפדפן",
        }
        return {"error": f"browser HTTP {e.code}", "detail": body,
                "hint": hints.get(e.code, "נסה fetch_page במקום")}
    except URLError as e:
        _warm = False
        return {"error": f"הדפדפן לא זמין: {e.reason}",
                "hint": "ייתכן שהשרת רדם. השתמש ב-fetch_page לבינתיים"}
    except Exception as e:
        _warm = False
        return {"error": f"{type(e).__name__}: {str(e)[:200]}",
                "hint": "נסה fetch_page במקום"}


def _t_browser_open(url: str = "", **_: Any) -> Dict[str, Any]:
    """פותח כתובת בדפדפן אמיתי. משתמש בזה רק כשצריך אינטראקציה (התחברות, טופס, קליק)."""
    res = _browser("/b/open", {"url": url})
    if "error" in res:
        return res
    return {"url": res.get("url"), "title": res.get("title"), "via": "browser",
            "memory_mb": (res.get("memory") or {}).get("total_pss_mb")}


def _t_browser_read(maxChars: int = MAX_FETCH_CHARS, **_: Any) -> Dict[str, Any]:
    """קורא את תוכן הדף שפתוח כרגע בדפדפן."""
    res = _browser("/b/read", {"maxChars": int(maxChars)})
    if "error" in res:
        return res
    return {"url": res.get("url"), "title": res.get("title"),
            "text": res.get("text", ""), "truncated": res.get("truncated", False)}


def _t_browser_links(maxLinks: int = 40, **_: Any) -> Dict[str, Any]:
    """מחזיר את הקישורים בדף שפתוח - שימושי לניווט."""
    res = _browser("/b/links", {"maxChars": int(maxLinks) * 120})
    if "error" in res:
        return res
    return {"url": res.get("url"), "count": res.get("count", 0),
            "links": (res.get("links") or [])[:int(maxLinks)]}


def _t_browser_click(selector: str = "", **_: Any) -> Dict[str, Any]:
    """לוחץ על אלמנט לפי CSS selector."""
    res = _browser("/b/click", {"selector": selector})
    if "error" in res:
        return res
    return {"clicked": res.get("clicked"), "text": res.get("text"),
            "url": res.get("url"), "title": res.get("title")}


def _t_browser_fill(selector: str = "", text: str = "", **_: Any) -> Dict[str, Any]:
    """ממלא שדה טקסט לפי CSS selector."""
    res = _browser("/b/fill", {"selector": selector, "text": text})
    if "error" in res:
        return res
    return {"filled": res.get("filled"), "value": res.get("value")}


def _t_browser_back(**_: Any) -> Dict[str, Any]:
    """חוזר לדף הקודם."""
    res = _browser("/b/back", {})
    if "error" in res:
        return res
    return {"url": res.get("url"), "title": res.get("title")}


def _t_browser_close(**_: Any) -> Dict[str, Any]:
    """סוגר את הדפדפן ומשחרר זיכרון."""
    global _warm
    res = _browser("/b/session", {})
    _warm = False
    return {"closed": True, "note": "הזיכרון שוחרר", "result": res.get("memory")}


# ============================================================ רישום
WEB_HANDLERS = {
    "web_search": _t_web_search,
    "fetch_page": _t_fetch_page,
    "browser_open": _t_browser_open,
    "browser_read": _t_browser_read,
    "browser_links": _t_browser_links,
    "browser_click": _t_browser_click,
    "browser_fill": _t_browser_fill,
    "browser_back": _t_browser_back,
    "browser_close": _t_browser_close,
}

WEB_SCHEMAS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "חפש באינטרנט וקבל רשימת תוצאות (כותרת, כתובת, תקציר). קודם בכל משימת מציאת מידע - זה הכלי הזול והמהיר ביותר. ללא מפתח.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "מילות החיפוש"},
            "limit": {"type": "integer", "description": "1-15, ברירת מחדל 8"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_page",
        "description": "הורד כתובת וחלץ ממנה טקסט קריא. מהיר וללא דפדפן. אם הדף דינמי והטקסט חסר - עבור ל-browser_open.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "maxChars": {"type": "integer", "description": "עד כמה תווים, ברירת מחדל 8000"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "browser_open",
        "description": "פותח אתר בדפדפן אמיתי. להשתמש רק כשצריך אינטראקציה: התחבנות, מילוי טופס, לחיצה. לא להתחבר ל-search או fetch_page.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "browser_read",
        "description": "קורא את טקסט הדף שפתוח בדפדפן.",
        "parameters": {"type": "object", "properties": {
            "maxChars": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "browser_links",
        "description": "מחזיר את הקישורים שנמצאו בדף שפתוח בדפדפן.",
        "parameters": {"type": "object", "properties": {
            "maxLinks": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "browser_click",
        "description": "לוחץ על אלמנט בדף שפתוח, לפי CSS selector (למשל 'button.submit' או '#search_form_input').",
        "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]}}},
    {"type": "function", "function": {
        "name": "browser_fill",
        "description": "ממלא שדה טקסט בדף שפתוח, לפי CSS selector.",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string"}, "text": {"type": "string"}}, "required": ["selector", "text"]}}},
    {"type": "function", "function": {
        "name": "browser_back",
        "description": "חוזר לדף הקודם בדפדפן.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "browser_close",
        "description": "סוגר את הדפדפן ומשחרר זיכרון. כדאי בסיום משימת גלישה.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]
