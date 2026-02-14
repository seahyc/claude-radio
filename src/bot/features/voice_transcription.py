"""Voice transcription using local faster-whisper model."""

import asyncio
import io
import tempfile
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger()

# Lazy-loaded model instance
_whisper_model = None
_model_lock = asyncio.Lock()


async def _get_model(model_size: str = "base"):
    """Lazy-load the Whisper model."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    async with _model_lock:
        if _whisper_model is not None:
            return _whisper_model

        try:
            from faster_whisper import WhisperModel

            logger.info("Loading Whisper model", model_size=model_size)
            # Run model loading in thread pool (it's CPU-bound)
            loop = asyncio.get_event_loop()
            _whisper_model = await loop.run_in_executor(
                None,
                lambda: WhisperModel(model_size, device="cpu", compute_type="int8"),
            )
            logger.info("Whisper model loaded", model_size=model_size)
            return _whisper_model

        except ImportError:
            logger.error(
                "faster-whisper not installed. "
                "Install with: pip install faster-whisper"
            )
            raise
        except Exception as e:
            logger.error("Failed to load Whisper model", error=str(e))
            raise


async def transcribe_audio(
    audio_data: bytes,
    model_size: str = "base",
    language: Optional[str] = None,
) -> str:
    """Transcribe audio data to text using local Whisper model.

    Args:
        audio_data: Raw audio bytes (ogg/opus from Telegram, or any ffmpeg-supported format)
        model_size: Whisper model size (tiny, base, small, medium, large-v3)
        language: Optional language code (e.g., "en"). Auto-detect if None.

    Returns:
        Transcribed text string.
    """
    model = await _get_model(model_size)

    # Write to temp file (faster-whisper needs a file path or file-like object)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
        tmp.write(audio_data)
        tmp.flush()

        # Run transcription in thread pool (CPU-bound)
        loop = asyncio.get_event_loop()

        def _transcribe():
            kwargs = {"beam_size": 5}
            if language:
                kwargs["language"] = language
            segments, info = model.transcribe(tmp.name, **kwargs)
            # Collect all segments
            return " ".join(seg.text.strip() for seg in segments)

        text = await loop.run_in_executor(None, _transcribe)

    logger.info(
        "Audio transcribed",
        text_length=len(text),
        text_preview=text[:100],
    )
    return text
