"""
כלי הסוכן (agent tools).
כל כלי מוגדר כמילולון OpenAI function-calling, עם handler שמחזיר dict.
"""
from __future__ import annotations

import ast
import html
import json
import math
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from urllib.parse import quote_plus, urlparse
from urllib.request import Request as URLRequest, urlopen

ISRAEL_TZ = timezone(timedelta(hours=3))
UA = "my-ai-agent/3.0 (+render)"
MAX_TOOL_BYTES = 6000
_MEM_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(ISRAEL_TZ)


def _http(url: str, timeout: int = 20, headers: Dict[str, str] | None = None) -> str:
    req = URLRequest(url, headers={"User-Agent": UA, **(headers or {})})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_TOOL_BYTES * 4)
    charset = "utf-8"
    try:
        charset = resp.headers.get_content_charset() or "utf-8"
    except Exception:
        pass
    return raw.decode(charset, errors="replace")


def _html_to_text(doc: str) -> str:
    doc = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", doc)
    doc = re.sub(r"(?s)<!--.*?-->", " ", doc)
    doc = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", doc)
    doc = re.sub(r"<[^>]+>", " ", doc)
    doc = html.unescape(doc)
    doc = re.sub(r"[ \t\r\f\v]+", " ", doc)
    doc = re.sub(r"\n\s*\n+", "\n\n", doc)
    return doc.strip()


# ---------------------------------------------------------------- calculator
_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "pow": pow, "log": math.log,
    "log10": math.log10, "exp": math.exp, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "degrees": math.degrees, "radians": math.radians,
    "factorial": math.factorial, "gcd": math.gcd, "hypot": math.hypot,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}
_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


class CalcError(ValueError):
    pass


def _eval_node(node: ast.AST, depth: int = 0) -> Any:
    if depth > 25:
        raise CalcError("ביטוי מורכב מדי")
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, depth)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("ערך לא חוקי")
        return node.value
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BINOPS):
            raise CalcError("פעולה לא נתמכת")
        left, right = _eval_node(node.left, depth + 1), _eval_node(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and (abs(left) > 1e6 or abs(right) > 1000):
            raise CalcError("חזקה גדולה מדי")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise CalcError("חלוקה באפס")
        return {
            ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
            ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
            ast.Pow: lambda a, b: a ** b,
        }[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand, depth + 1)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return val
        raise CalcError("אופרטור לא נתמך")
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_NAMES:
            return _ALLOWED_NAMES[node.id]
        raise CalcError(f"שם לא מוכר: {node.id}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS or node.keywords:
            raise CalcError("פונקציה לא מורשית")
        args = [_eval_node(a, depth + 1) for a in node.args]
        if node.func.id == "factorial" and (len(args) != 1 or not 0 <= int(args[0]) <= 170):
            raise CalcError("factorial חייב להיות בין 0 ל-170")
        return _ALLOWED_FUNCS[node.func.id](*args)
    raise CalcError("מבנה ביטוי לא נתמך")


def _normalize_expr(text: str) -> str:
    expr = text.strip()
    m = re.search(r"(?:חשב|חשבי|החשב|calculate|calc|compute)\s*[:\-–]?\s*(.+)$", expr, re.IGNORECASE)
    if m:
        expr = m.group(1)
    expr = expr.replace("×", "*").replace("✕", "*").replace("÷", "/")
    expr = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", expr)      # 5 - 3 -> 5-3
    expr = re.sub(r"(?<=\d)\s*\+\s*(?=\d)", "+", expr)
    expr = re.sub(r"(?<=\d)\s*\*\s*(?=\d)", "*", expr)
    expr = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", expr)
    expr = re.sub(r"(?<=\d)\s+-\s+(?=\d)", "-", expr)
    expr = re.sub(r"\^\s*2\b", "**2", expr)
    expr = expr.replace("√", "sqrt").replace("×", "*")
    return expr.strip().rstrip("=?. ")


def _try_calc(text: str) -> str | None:
    expr = _normalize_expr(text)
    if not expr or not re.search(r"\d", expr):
        return None
    if not re.fullmatch(r"[0-9+\-*/%().,!a-zA-Z_\s]*", expr):
        return None
    if "!" in expr and "factorial" not in expr and not re.fullmatch(r"[\d\s()!+\-*/%.*]+", expr):
        return None
    try:
        tree = ast.parse(expr, mode="eval")
        value = _eval_node(tree)
    except CalcError as e:
        return f"לא הצלחתי לחשב: {e}"
    except Exception:
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return "התוצאה אינה סופית"
        if value.is_integer() and abs(value) < 1e15:
            value = int(value)
        else:
            value = round(value, 10)
    if isinstance(value, int) and abs(value) > 10 ** 30:
        return "התוצאה גדולה מדי"
    return f"{expr} = {value}"


# ------------------------------------------------------------------- memory
def _notes_path() -> str:
    return os.path.join(os.getcwd(), "data", "notes.json")


def _load_notes() -> Dict[str, str]:
    with _MEM_LOCK:
        try:
            with open(_notes_path(), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}


def _save_notes(notes: Dict[str, str]) -> None:
    with _MEM_LOCK:
        os.makedirs(os.path.dirname(_notes_path()), exist_ok=True)
        tmp = _notes_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(notes, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _notes_path())


# ----------------------------------------------------------------- registry
def _t_now(**_: Any) -> Dict[str, Any]:
    n = _now()
    return {
        "iso": n.isoformat(),
        "date": n.strftime("%d/%m/%Y"),
        "time": n.strftime("%H:%M"),
        "weekday": n.strftime("%A"),
        "timezone": "Asia/Jerusalem (UTC+3)",
    }


def _t_calculator(expression: str = "", **_: Any) -> Dict[str, Any]:
    expr = _normalize_expr(expression or "")
    try:
        value = _eval_node(ast.parse(expr, mode="eval"))
    except Exception as e:
        return {"error": str(e) or "ביטוי לא חוקי"}
    if isinstance(value, float):
        value = int(value) if value.is_integer() and abs(value) < 1e15 else round(value, 10)
    return {"expression": expr, "result": value}


def _t_calculate_from_text(text: str = "", **_: Any) -> Dict[str, Any]:
    out = _try_calc(text or "")
    return {"text": text, "result": out, "solved": out is not None}


def _t_wikipedia_search(query: str = "", limit: int = 5, **_: Any) -> Dict[str, Any]:
    if not query.strip():
        return {"error": "חסר query"}
    limit = max(1, min(int(limit or 5), 10))
    api = (
        "https://he.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit="
        f"{limit}&srsearch={quote_plus(query)}"
    )
    try:
        data = json.loads(_http(api))
    except Exception as e:
        return {"error": f"חיפוש נכשל: {e}"}
    hits = [
        {"title": h.get("title"), "snippet": re.sub(r"<[^>]+>", "", h.get("snippet", ""))}
        for h in data.get("query", {}).get("search", [])
    ]
    return {"query": query, "results": hits, "count": len(hits)}


def _t_wikipedia_page(title: str = "", **_: Any) -> Dict[str, Any]:
    if not title.strip():
        return {"error": "חסר title"}
    api = (
        "https://he.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=1"
        f"&explaintext=1&redirects=1&format=json&titles={quote_plus(title)}"
    )
    try:
        data = json.loads(_http(api))
    except Exception as e:
        return {"error": f"שליפה נכשלה: {e}"}
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        return {"title": page.get("title"), "extract": (page.get("extract") or "")[:4000]}
    return {"error": "הערך לא נמצא"}


def _t_read_url(url: str = "", **_: Any) -> Dict[str, Any]:
    try:
        parsed = urlparse(url)
    except Exception:
        return {"error": "URL לא חוקי"}
    if parsed.scheme not in ("http", "https"):
        return {"error": "רק http/https מותרים"}
    try:
        raw = _http(url, timeout=20)
    except Exception as e:
        return {"error": f"ההורדה נכשלה: {e}"}
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    return {
        "url": url,
        "title": html.unescape(title_match.group(1)).strip()[:200] if title_match else "",
        "text": _html_to_text(raw)[:MAX_TOOL_BYTES],
    }


def _t_remember(key: str = "", value: str = "", **_: Any) -> Dict[str, Any]:
    if not key.strip():
        return {"error": "חסר key"}
    notes = _load_notes()
    notes[key.strip()[:100]] = str(value)[:2000]
    if len(notes) > 200:
        notes = dict(list(notes.items())[-200:])
    _save_notes(notes)
    return {"stored": key.strip()[:100], "total_notes": len(notes)}


def _t_recall(key: str = "", **_: Any) -> Dict[str, Any]:
    notes = _load_notes()
    if key.strip():
        return {"key": key.strip(), "value": notes.get(key.strip())}
    return {"notes": notes}


def _t_forget(key: str = "", **_: Any) -> Dict[str, Any]:
    notes = _load_notes()
    removed = notes.pop(key.strip(), None)
    _save_notes(notes)
    return {"removed": key.strip(), "was_present": removed is not None}


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "current_time", "description": "התאריך והשעה הנוכחיים בישראל.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "calculate", "description": "חשב ביטוי מתמטי. תומך בפעולות, ^ לחזקה, ופונקציות כגון sqrt, sin, log, factorial.",
        "parameters": {"type": "object", "properties": {
            "expression": {"type": "string", "description": "למשל (2+3)*4^2 או sqrt(144)"}}, "required": ["expression"]}}},
    {"type": "function", "function": {
        "name": "wikipedia_search", "description": "חפש בוויקיפדיה העברית וקבל רשימת ערכים רלוונטיים.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer", "description": "1-10, ברירת מחדל 5"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "wikipedia_page", "description": "קבל את פתיחת הערך מוויקיפדיה העברית כטקסט.",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "read_url", "description": "הורד דף אינטרנט וחלץ ממנו טקסט קריא.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "remember", "description": "שמור עובדה או העדפה לזיכרון לטווח ארוך שנשמר בין הפעלות.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {
        "name": "recall", "description": "קרא מהזיכרון. בלי key מחזיר את כל העובדות השמורות.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "forget", "description": "מחק עובדה מהזיכרון.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
]

HANDLERS = {
    "current_time": _t_now,
    "calculate": _t_calculator,
    "wikipedia_search": _t_wikipedia_search,
    "wikipedia_page": _t_wikipedia_page,
    "read_url": _t_read_url,
    "remember": _t_remember,
    "recall": _t_recall,
    "forget": _t_forget,
}


def run_tool(name: str, args: dict) -> dict:
    handler = HANDLERS.get(name)
    if not handler:
        return {"error": f"כלי לא מוכר: {name}"}
    try:
        return handler(**(args or {}))
    except TypeError as e:
        return {"error": f"ארגומנטים שגויים: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def quick_reply(user_text: str) -> str | None:
    """תשובה מהירה מקומית — חוסך קריאה למודל ונותן מרגש מיידי."""
    text = (user_text or "").strip()
    if not text:
        return "כתוב לי משהו ואשמח לעזור."
    low = text.lower().strip(" ?!.־-")
    if low in ("מה השעה", "מה השעה עכשיו", "מה השעה כרגע", "what time"):
        n = _now()
        return f"השעה עכשיו בישראל: {n.strftime('%H:%M')} ({n.strftime('%d/%m/%Y')})"
    if low in ("מה התאריך", "מה התאריך היום", "what date", "איזה יום"):
        n = _now()
        return f"היום {n.strftime('%A')}, {n.strftime('%d/%m/%Y')}."
    calc = _try_calc(text)
    if calc:
        return calc
    return None
