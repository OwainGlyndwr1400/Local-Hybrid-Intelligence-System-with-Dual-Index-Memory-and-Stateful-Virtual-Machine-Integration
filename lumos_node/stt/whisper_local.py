"""Local Whisper STT via faster-whisper. Replaces browser/cloud STT for push-to-talk.

faster-whisper uses CTranslate2 internally for fast CPU inference. Models are
auto-downloaded from HuggingFace (Systran/faster-whisper-*) on first use and
cached under ~/.cache/lumos_whisper/.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..log import get_logger

log = get_logger(__name__)


_model_instance: Any = None
_model_key: tuple[str, str] | None = None


def _cache_dir() -> Path:
    p = Path.home() / ".cache" / "lumos_whisper"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model() -> Any:
    global _model_instance, _model_key
    settings = get_settings()
    key = (settings.whisper_model_size, settings.whisper_compute_type)
    if _model_instance is None or _model_key != key:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper not installed. Run: uv pip install faster-whisper"
            ) from e
        log.info(
            "whisper.loading",
            model=key[0],
            compute_type=key[1],
            cache=str(_cache_dir()),
        )
        _model_instance = WhisperModel(
            key[0],
            device="cpu",
            compute_type=key[1],
            download_root=str(_cache_dir()),
        )
        _model_key = key
        log.info("whisper.loaded")
    return _model_instance


def transcribe(audio_bytes: bytes, *, language: str | None = "en") -> dict[str, Any]:
    """Transcribe raw audio bytes (webm/wav/mp3/etc) to text.

    Writes the blob to a temp file so faster-whisper's internal av/ffmpeg
    decoder can handle any common container format. Returns a dict with
    `text`, `language`, `duration`, and `segments` keys.
    """
    if not audio_bytes:
        return {"text": "", "language": "en", "duration": 0.0, "segments": []}

    model = _get_model()

    # faster-whisper accepts a path; write to temp file. The suffix is a hint;
    # the underlying decoder sniffs the format regardless.
    suffix = ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        segments_iter, info = model.transcribe(
            tmp_path,
            language=language if language else None,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        segments: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for s in segments_iter:
            seg_text = s.text or ""
            text_parts.append(seg_text)
            segments.append(
                {
                    "start": float(s.start),
                    "end": float(s.end),
                    "text": seg_text,
                }
            )
        text = "".join(text_parts).strip()
        log.info(
            "whisper.transcribed",
            chars=len(text),
            duration=info.duration,
            language=info.language,
        )
        return {
            "text": text,
            "language": info.language,
            "language_probability": float(info.language_probability or 0.0),
            "duration": float(info.duration or 0.0),
            "segments": segments,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def prewarm() -> dict[str, Any]:
    if not is_available():
        return {"available": False, "reason": "faster-whisper not installed"}
    _ = _get_model()
    settings = get_settings()
    return {
        "available": True,
        "model": settings.whisper_model_size,
        "compute_type": settings.whisper_compute_type,
        "cache_dir": str(_cache_dir()),
    }
