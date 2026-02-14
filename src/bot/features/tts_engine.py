"""Text-to-Speech engine abstraction with local model support.

Supported backends:
  - kokoro: Local 82M param model, Apache 2.0, sub-300ms (default)
  - openai: OpenAI TTS API (requires API key, used as fallback)
"""

import asyncio
import io
import tempfile
from typing import Optional

import structlog

logger = structlog.get_logger()

# Lazy-loaded Kokoro pipeline
_kokoro_pipeline = None
_kokoro_lock = asyncio.Lock()


async def _get_kokoro():
    """Lazy-load the Kokoro TTS pipeline."""
    global _kokoro_pipeline
    if _kokoro_pipeline is not None:
        return _kokoro_pipeline

    async with _kokoro_lock:
        if _kokoro_pipeline is not None:
            return _kokoro_pipeline

        try:
            from kokoro import KPipeline

            logger.info("Loading Kokoro TTS model")
            loop = asyncio.get_event_loop()
            _kokoro_pipeline = await loop.run_in_executor(
                None,
                lambda: KPipeline(lang_code="a"),  # American English
            )
            logger.info("Kokoro TTS model loaded")
            return _kokoro_pipeline

        except ImportError:
            logger.error(
                "kokoro not installed. Install with: pip install kokoro soundfile"
            )
            raise


async def synthesize_speech(
    text: str,
    engine: str = "kokoro",
    voice: str = "af_heart",
    openai_api_key: Optional[str] = None,
) -> bytes:
    """Convert text to speech audio.

    Args:
        text: Text to synthesize.
        engine: TTS engine to use ("kokoro" or "openai").
        voice: Voice name (engine-specific).
        openai_api_key: Required if engine is "openai".

    Returns:
        Audio data as bytes (OGG/Opus format for Telegram voice notes).
    """
    if engine == "kokoro":
        return await _synthesize_kokoro(text, voice)
    elif engine == "openai":
        return await _synthesize_openai(text, voice, openai_api_key)
    else:
        raise ValueError(f"Unknown TTS engine: {engine}")


async def _synthesize_kokoro(text: str, voice: str = "af_heart") -> bytes:
    """Synthesize speech using local Kokoro model."""
    import soundfile as sf

    pipeline = await _get_kokoro()

    loop = asyncio.get_event_loop()

    def _generate():
        # Kokoro returns a generator of (graphemes, phonemes, audio) tuples
        audio_segments = []
        for _gs, _ps, audio in pipeline(text, voice=voice, speed=1.0):
            audio_segments.append(audio)

        if not audio_segments:
            raise RuntimeError("Kokoro generated no audio segments")

        # Concatenate all audio segments
        import numpy as np
        full_audio = np.concatenate(audio_segments)

        # Write to OGG/Opus format (Telegram voice note format)
        buf = io.BytesIO()
        sf.write(buf, full_audio, 24000, format="OGG", subtype="OPUS")
        buf.seek(0)
        return buf.read()

    audio_bytes = await loop.run_in_executor(None, _generate)

    logger.info(
        "Kokoro TTS synthesized",
        text_length=len(text),
        audio_bytes=len(audio_bytes),
        voice=voice,
    )
    return audio_bytes


async def _synthesize_openai(
    text: str, voice: str = "alloy", api_key: Optional[str] = None
) -> bytes:
    """Synthesize speech using OpenAI TTS API."""
    if not api_key:
        raise ValueError("OpenAI API key required for OpenAI TTS engine")

    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError("openai package required. Install with: pip install openai")

    client = AsyncOpenAI(api_key=api_key)

    response = await client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="opus",
    )

    audio_bytes = response.content

    logger.info(
        "OpenAI TTS synthesized",
        text_length=len(text),
        audio_bytes=len(audio_bytes),
        voice=voice,
    )
    return audio_bytes
