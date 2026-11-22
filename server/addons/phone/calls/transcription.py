"""Transcribing a call recording (``core.llm.transcribe``, audio inline), as
the current user (the call's owner: usage rows are theirs)."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from typing import List, Optional, Tuple

from core import budget, llm

logger = logging.getLogger(__name__)

# Inline audio is capped at ~20 MB per request (base64 adds a third), and a
# long call is too much to transcribe in one reply anyway: anything bigger or
# longer is cut into small mono Opus chunks transcribed separately.
TRANSCRIBE_DIRECT_MAX_BYTES = int(os.getenv("CALL_TRANSCRIBE_DIRECT_MAX_BYTES", str(12 * 1024 * 1024)))
TRANSCRIBE_DIRECT_MAX_SECONDS = int(os.getenv("CALL_TRANSCRIBE_DIRECT_MAX_SECONDS", "720"))
TRANSCRIBE_CHUNK_SECONDS = int(os.getenv("CALL_TRANSCRIBE_CHUNK_SECONDS", "600"))
TRANSCRIBE_TIMEOUT = float(os.getenv("CALL_TRANSCRIBE_TIMEOUT", "240"))
TRANSCRIBE_CONCURRENCY = 3


def err_text(e: BaseException) -> str:
    """An exception as text; timeouts can stringify to ''."""
    return str(e) or type(e).__name__


def _clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def audio_duration_seconds(path: str) -> int:
    """Duration of an audio file via mutagen (0 when unreadable)."""
    try:
        import mutagen
        f = mutagen.File(path)
        if f is not None and getattr(f, "info", None) is not None:
            return int(round(float(f.info.length or 0)))
    except Exception as e:
        logger.warning(f"Could not read duration of {path}: {e}")
    return 0


async def _transcribe_once(data: bytes, mime: str, note: str = "", ref: str = "") -> str:
    last: Optional[BaseException] = None
    for attempt in range(3):
        try:
            return await llm.transcribe(data, mime, timeout=TRANSCRIBE_TIMEOUT, note=note,
                                        purpose="call_transcript", source="phone", ref=ref)
        except Exception as e:
            last = e
            if not llm.retryable(e):
                break  # bad request / too large: retrying won't help
            logger.warning(f"Transcription attempt {attempt + 1} failed: {err_text(e)}")
            await asyncio.sleep(3 * (attempt + 1))
    raise last  # type: ignore[misc]


async def _split_audio(path: str, out_dir: str, chunk_seconds: int) -> List[str]:
    """Cut ``path`` into ``chunk_seconds`` mono 16 kHz Opus chunks (≈ 1.8 MB / 10 min)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k",
        "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1",
        os.path.join(out_dir, "part%03d.ogg"),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[-300:]}")
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".ogg"))


async def transcribe_call_audio(path: str, mime: str, duration: int = 0, ref: str = "") -> str:
    """Transcribe a call recording of any length. Raises when nothing could be transcribed.

    Raises ``llm.TranscriptionUnavailable`` / ``budget.Paused`` as they are.

    Short recordings are sent as they are. Long ones are cut into
    ``TRANSCRIBE_CHUNK_SECONDS`` chunks, transcribed concurrently and joined;
    a chunk that keeps failing becomes a ``[mm:ss–mm:ss could not be
    transcribed]`` line, so one bad stretch doesn't lose the whole call.
    """
    size = os.path.getsize(path)
    if size <= TRANSCRIBE_DIRECT_MAX_BYTES and duration <= TRANSCRIBE_DIRECT_MAX_SECONDS:
        data = await asyncio.to_thread(lambda: open(path, "rb").read())
        return await _transcribe_once(data, mime, ref=ref)

    out_dir = tempfile.mkdtemp(prefix="call-chunks-")
    try:
        parts = await _split_audio(path, out_dir, TRANSCRIBE_CHUNK_SECONDS)
        if not parts:
            raise RuntimeError("ffmpeg produced no audio")
        n = len(parts)
        sem = asyncio.Semaphore(TRANSCRIBE_CONCURRENCY)

        async def one(i: int, part: str) -> Tuple[str, Optional[str]]:
            data = await asyncio.to_thread(lambda: open(part, "rb").read())
            note = (f"This is part {i + 1} of {n} of one long phone call; it may start or end mid-sentence. "
                    "Transcribe only this part.")
            async with sem:
                try:
                    return (await _transcribe_once(data, "audio/ogg", note, ref)).strip(), None
                except (llm.TranscriptionUnavailable, budget.Paused):
                    raise  # no part can be transcribed now: the call says why
                except Exception as e:
                    start = i * TRANSCRIBE_CHUNK_SECONDS
                    end = start + TRANSCRIBE_CHUNK_SECONDS if i < n - 1 else max(duration, start)
                    logger.error(f"Transcribing part {i + 1}/{n} of {os.path.basename(path)} failed: {err_text(e)}")
                    return f"[{_clock(start)}–{_clock(end)} could not be transcribed]", err_text(e)

        results = await asyncio.gather(*(one(i, p) for i, p in enumerate(parts)))
        failures = [err for _, err in results if err]
        if len(failures) == n:
            raise RuntimeError(f"all {n} parts failed: {failures[0]}")
        logger.info(f"Transcribed {os.path.basename(path)} in {n} parts ({len(failures)} failed)")
        return "\n\n".join(text for text, _ in results if text)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
