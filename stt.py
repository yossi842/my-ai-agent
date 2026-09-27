"""
תמלול דיבור לטקסט (Speech-to-Text).

שני מקורות אפשריים, לפי מה שמוגדר בשרת:
  1. Groq  -> whisper-large-v3      (https://api.groq.com/openai/v1/audio/transcriptions)
  2. Google -> gemini-3.5-transcribe

הערה על עיצוב: הדפדפן שולח את קובץ השמע הגולמי כגוף בקשה (Content-Type: audio/*)
ולא כ-multipart. כך השרת לא צריך parser של multipart — רק לקרוא bytes.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest, urlopen

MAX_AUDIO_BYTES = 8 * 1024 * 1024  # 8MB
ALLOWED_TYPES = (
    "audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg",
    "audio/wav", "audio/x-wav", "audio/flac", "audio/aac", "video/webm",
)


def stt_backends() -> list[dict]:
    """רשימת מנועי תמלול זמינים, לפי סדר עדיפות."""
    out: list[dict] = []
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        out.append({
            "id": "groq-whisper",
            "url": "https://api.groq.com/openai/v1/audio/transcriptions",
            "api_key": groq_key,
            "model": (os.getenv("STT_MODEL", "whisper-large-v3").strip() or "whisper-large-v3"),
        })
    gem = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if gem:
        out.append({
            "id": "gemini-transcribe",
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/audio/transcriptions",
            "api_key": gem,
            "model": "gemini-3.5-transcribe",
        })
    return out


def _build_multipart(fields: dict, files: list[dict]) -> Tuple[bytes, str]:
    """בונה גוף multipart/form-data ידנית (בלי תלויות חיצוניות)."""
    boundary = f"----agent{uuid.uuid4().hex}"
    sep = f"--{boundary}\r\n".encode()
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(sep)
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(f"{value}\r\n".encode())
    for part in files:
        parts.append(sep)
        parts.append(
            f'Content-Disposition: form-data; name="{part["name"]}"; '
            f'filename="{part["filename"]}"\r\n'.encode()
        )
        parts.append(f'Content-Type: {part["content_type"]}\r\n\r\n'.encode())
        parts.append(part["content"])
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe(audio: bytes, content_type: str, language: str = "he") -> dict:
    """ממיר שמע לטקסט. מחזיר dict עם text, provider, error."""
    if not audio:
        return {"error": "לא נשלח קובץ שמע"}
    if len(audio) > MAX_AUDIO_BYTES:
        return {"error": f"קובץ השמע גדול מדי ({len(audio)//1024}KB, מקסימום 8MB)"}
    if not content_type.lower().startswith(("audio/", "video/")):
        return {"error": f"סוג קובץ לא נתמך: {content_type}"}

    backends = stt_backends()
    if not backends:
        return {"error": "אין מנוע תמלול מוגדר. צריך GROQ_API_KEY או GEMINI_API_KEY."}

    ext = {
        "audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg",
        "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav",
        "audio/x-wav": "wav", "audio/flac": "flac", "audio/aac": "aac",
    }.get(content_type.split(";")[0].strip().lower(), "webm")

    errors: list[str] = []
    for backend in backends:
        try:
            body, ctype = _build_multipart(
                fields={"model": backend["model"], "language": language, "response_format": "json"},
                files=[{
                    "name": "file",
                    "filename": f"speech.{ext}",
                    "content_type": content_type,
                    "content": audio,
                }],
            )
            req = URLRequest(
                backend["url"],
                data=body,
                headers={
                    "Authorization": f"Bearer {backend['api_key']}",
                    "Content-Type": ctype,
                    "Accept": "application/json",
                    "User-Agent": "my-ai-agent/3.0 (Hebrew AI agent)",
                },
            )
            with urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode())
            text = (data.get("text") or "").strip()
            if not text:
                errors.append(f"{backend['id']}: השרת החזיר טקסט ריק")
                continue
            return {
                "text": text,
                "provider": backend["id"],
                "model": backend["model"],
                "language": language,
            }
        except HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:250]
            except Exception:
                pass
            errors.append(f"{backend['id']}: HTTP {e.code} {detail}")
        except URLError as e:
            errors.append(f"{backend['id']}: {e.reason}")
        except Exception as e:
            errors.append(f"{backend['id']}: {type(e).__name__}: {e}")

    return {"error": "כל מנועי התמלול נכשלו. " + " | ".join(errors)[:400]}
