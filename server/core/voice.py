import logging
import struct

from core import llm

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = (
    "You summarize a transcribed WhatsApp voice message.\n"
    "- Reply in the same language as the transcript (Hebrew -> Hebrew, English -> English; "
    "for mixed speech use the dominant language, keeping names/terms as spoken).\n"
    "- Be concise: 1-3 sentences, or up to 4 short bullet lines if the message covers several points.\n"
    "- Keep concrete details people need: requests, tasks, dates/times, amounts, addresses, names.\n"
    "- Add nothing that is not in the transcript. No preamble, no headings, no commentary.\n"
    "- If the transcript is empty or only noise, reply with an empty string."
)


def create_wav_header(sample_rate: int, bits_per_sample: int, channels: int, data_size: int) -> bytes:
    """Creates a standard RIFF WAV header for raw PCM data."""
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,
        b'WAVE',
        b'fmt ',
        16, # PCM format chunk size
        1,  # PCM format code
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size
    )
    return header

async def summarize_text(text: str) -> str:
    """Summarize a transcript with the chat model (``core.llm``).

    Returns an empty string when there is nothing to summarize. Raises on API
    failure so callers can tell the user instead of silently showing nothing.
    """
    text = (text or "").strip()
    if not text:
        return ""

    return await llm.complete([
        {"role": "system", "content": _SUMMARY_PROMPT},
        {"role": "user", "content": text},
    ], purpose="voice_summary")
