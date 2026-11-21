"""Media of knowledge entries: a link saved into a project is downloaded
(yt-dlp), transcribed and summarised in the background, and the result is
stored on the entry; the files live in ``paths.MEDIA_DIR`` and are served at
``/media/<file>`` only to someone who can see the entry (:func:`file_for`).

Processing runs as the user who owns the entry (the owner of its project), so
the model calls it makes are attributed to them (docs/usage.md). The download
is restricted (core/outbound.py) unless both the user who last set the link
(``entries.url_set_by``) and the one who asked for it have ``manage_server``.
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from core import llm, outbound, paths, users
from core.db import get_db_connection
from core.users import User
from addons.knowledge.values import clean_str, loads_json

logger = logging.getLogger(__name__)

MEDIA_DIR = str(paths.MEDIA_DIR)
# The URL prefix stored in entries.media_path.
URL_PREFIX = "/media/"

# Background tasks still running (a reference keeps each one alive).
_tasks: Set["asyncio.Task[None]"] = set()

_SUMMARY_SCHEMA = {"type": "object", "properties": {
    "summary": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["summary", "tags"]}

async def download_and_process_media(url: str, restricted: bool) -> dict:
    """
    Downloads media from a URL using yt-dlp, extracts metadata, 
    and attempts to transcribe the audio if present.
    Returns a dict with: {'media_path': str, 'transcript': str, 'title': str, 'author': str}

    ``restricted`` (a link someone without ``manage_server`` chose or asked
    for; core/outbound.py): the URL must be on the public internet and
    yt-dlp runs with site extractors only (no generic extractor: it never
    fetches an arbitrary page or redirect target). Unrestricted, the download
    is as it always was.
    """
    if restricted:
        try:
            await outbound.check_public_url(url)
        except outbound.Refused as e:
            logger.warning(f"Link download refused: {e}")
            return {"error": "Download refused", "details": str(e)}
    try:
        # Create a unique filename base
        file_id = str(uuid.uuid4())
        output_template = os.path.join(MEDIA_DIR, f"{file_id}.%(ext)s")
        
        # yt-dlp command to download video + audio and extract info as JSON
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--max-filesize", "50M", # Prevent massive downloads
            "-S", "res:720,ext:mp4:m4a", # Prefer mp4 video, m4a audio
            "--no-check-certificates",
            "--write-comments",
            "--print-json",
            "--extractor-args", "tiktok:api_hostname=api16-normal-c-useast1a.tiktokv.com;app_info=7355728856979392262",
            "-o", output_template,
        ]
        if restricted:
            cmd += ["--use-extractors", "default,-generic"]
        cmd += ["--", url]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            err_msg = stderr.decode('utf-8').strip()
            logger.error(f"yt-dlp failed for {url} with exit code {process.returncode}:\nSTDERR:\n{err_msg}")
            return {"error": "Download failed", "details": err_msg}
            
        # Parse JSON info from yt-dlp stdout
        info = {}
        for line in stdout.decode().strip().split('\n'):
            try:
                info = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
                
        ext = info.get("ext", "mp4")
        media_path = os.path.join(MEDIA_DIR, f"{file_id}.{ext}")
        relative_path = f"{URL_PREFIX}{file_id}.{ext}"
        
        title = info.get("title", "")
        author = info.get("uploader", info.get("channel", ""))
        
        # Extract top comments if available
        raw_comments = info.get("comments") or []
        comments = []
        for c in raw_comments[:10]:
            if isinstance(c, dict) and c.get("text"):
                c_author = c.get("author") or "Anonymous"
                comments.append({"author": c_author, "text": c.get("text")})
        
        summary = ""
        tags = []
        transcript = ""
        
        # Try to transcribe & summarize if it's a video/audio file
        if os.path.exists(media_path):
            try:
                # We extract audio to a temporary wav/mp3 to transcribe
                audio_tmp = os.path.join(MEDIA_DIR, f"{file_id}_audio.mp3")
                ffmpeg_cmd = [
                    "ffmpeg", "-y", "-i", media_path, 
                    "-vn", "-acodec", "libmp3lame", "-q:a", "5", 
                    audio_tmp
                ]
                ff_process = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await ff_process.communicate()
                
                if ff_process.returncode == 0 and os.path.exists(audio_tmp):
                    with open(audio_tmp, "rb") as f:
                        audio_bytes = f.read()
                    
                    transcript = await llm.transcribe(audio_bytes, "audio/mp3", purpose="media_summary", source="app")
                    os.remove(audio_tmp)
                    
                    # Generate AI summary and tags from transcript / title
                    if transcript.strip() or title.strip():
                        prompt = (
                            "Analyze this media title and transcript. Return JSON with two fields:\n"
                            "1. 'summary': A concise 1-2 sentence summary of what this video/media is about (in the same language as the transcript).\n"
                            "2. 'tags': 2-5 short relevant category/topic labels in the same language "
                            "(e.g. ['דולומיטים', 'קרוואן', 'איטליה', 'טיולים'] or ['Food', 'Rome', 'Pasta']).\n\n"
                            f"Title: {title}\nTranscript: {transcript}"
                        )
                        try:
                            parsed = await llm.complete_json(prompt, _SUMMARY_SCHEMA, job="chat", purpose="media_summary", source="app")
                            summary = parsed.get("summary", "")
                            tags = parsed.get("tags", [])
                            if not isinstance(tags, list):
                                tags = [str(tags)]
                        except Exception as se:
                            logger.warning(f"Failed to generate AI summary/tags for {url}: {se}")
            except Exception as e:
                logger.error(f"Failed to transcribe media {media_path}: {e}")
                
        return {
            "media_path": relative_path,
            "transcript": transcript,
            "summary": summary,
            "tags": tags,
            "title": title,
            "author": author,
            "comments": comments
        }
    except Exception as e:
        logger.error(f"Exception in download_and_process_media for {url}: {e}")
        return {"error": str(e)}


def apply_media_result(entry_id: str, result: Dict[str, Any]) -> None:
    """Store the output of the media/download pipeline on an entry."""
    with get_db_connection() as conn:
        # scoped: the media pipeline's own entry, processed as its owner (process_knowledge_media)
        row = conn.execute("SELECT title, tags FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return

        media_tags = result.get("tags") or []
        media_tags = media_tags if isinstance(media_tags, list) else []
        merged_tags = loads_json(row["tags"], []) + [str(t) for t in media_tags]
        merged_tags = list(dict.fromkeys(merged_tags))  # de-dupe, preserve order

        conn.execute(
            # scoped: the media pipeline's own entry, processed as its owner (process_knowledge_media)
            """
            UPDATE entries
            SET media_path = ?, transcript = ?, summary = ?, tags = ?, comments = ?,
                source_author = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                result.get("media_path"),
                result.get("transcript"),
                result.get("summary"),
                json.dumps(merged_tags),
                json.dumps(result.get("comments") or []),
                result.get("author"),
                entry_id,
            ),
        )
        # Only adopt the real media title when the entry has none yet.
        if clean_str(row["title"]) == "" and result.get("title"):
            # scoped: the media pipeline's own entry, processed as its owner (process_knowledge_media)
            conn.execute("UPDATE entries SET title = ? WHERE id = ?", (result["title"], entry_id))
        conn.commit()


def owner_of_entry(entry_id: str) -> Optional[User]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT user_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return users.get(row["user_id"]) if row else None


def _link_of_entry(entry_id: str) -> Optional[Tuple[Optional[str], Optional[User]]]:
    """``(url, who last set it)`` of an entry (None: no such entry; the user
    None: unknown, e.g. removed since)."""
    with get_db_connection() as conn:
        # scoped: the pipeline reads its entry's link before acting as the entry's owner
        row = conn.execute("SELECT url, url_set_by FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    return row["url"], users.get(row["url_set_by"]) if row["url_set_by"] else None


async def process_knowledge_media(entry_id: str, url: str, requester: User) -> None:
    """Download media for a URL and enrich the entry, as the entry's owner.

    The download is restricted (core/outbound.py) whenever the user who last
    set the entry's URL (``url_set_by``; unknown counts as restricted) or
    ``requester`` (who saved or retried it) lacks ``manage_server``: whoever
    presses Retry, a link a member chose is never fetched unrestricted. A URL
    that is no longer the entry's is not processed (a newer change replaced it)."""
    owner = owner_of_entry(entry_id)
    link = _link_of_entry(entry_id)
    if owner is None or link is None:
        logger.warning("Media processing skipped: entry %s is gone or has no owner", entry_id)
        return
    stored_url, setter = link
    if (stored_url or "") != (url or ""):
        logger.info("Media processing skipped: the URL of entry %s changed since it was requested", entry_id)
        return
    restricted = not outbound.trusted(requester) or setter is None or not outbound.trusted(setter)
    with users.acting_as(owner):
        try:
            result = await download_and_process_media(url, restricted)
            if "error" in result:
                logger.error(
                    "Background media processing failed for %s (URL: %s): %s",
                    entry_id, url, result.get("details", result["error"]),
                )
                return
            apply_media_result(entry_id, result)
        except Exception as e:
            logger.error("Error in process_knowledge_media for %s: %s", entry_id, e, exc_info=True)


def start_processing(entry_id: str, url: str, requester: User) -> "asyncio.Task[None]":
    """Start :func:`process_knowledge_media` in the background (``requester``:
    who saved or retried the link)."""
    task = asyncio.create_task(process_knowledge_media(entry_id, url, requester))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def file_for(visible_where: str, args: list, name: str) -> Optional[Path]:
    """The stored media file ``name`` when an entry matching ``visible_where``
    (a condition on ``entries``, no alias, with its ``args``) points at it;
    None for anything else (a path, a file no visible entry has)."""
    if not name or name != os.path.basename(name) or name.startswith("."):
        return None
    with get_db_connection() as conn:
        # scoped: visible_where is the caller's entries.visible_sql clause
        row = conn.execute(f"SELECT 1 FROM entries WHERE media_path = ? AND {visible_where} LIMIT 1",
                           (URL_PREFIX + name, *args)).fetchone()
    if row is None:
        return None
    path = Path(MEDIA_DIR) / name
    return path if path.is_file() else None
