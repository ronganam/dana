"""Saved images (chat uploads, Telegram photos, images a WhatsApp tool fetched).

Each file in ``paths.IMAGES_DIR`` belongs to the user it was saved for (table
``uploaded_images``, migration ``m0011_whatsapp_per_user``). It is served at
``/api/images/{name}`` and turned into a data URI for the model only for that
user; for anyone else (or a file with no owner) it does not exist.
"""

import base64
import logging
import uuid
from pathlib import Path
from typing import Optional

from core import user_rows
from core.db import get_db_connection
from core.paths import IMAGES_DIR
from core.users import User

logger = logging.getLogger(__name__)

URL_PREFIX = "/api/images/"

MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def ensure_upload_dir() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def save_image(image_bytes: bytes, mime_type: str, user: User) -> str:
    """Store an image for ``user``; returns its URL (``/api/images/<name>``)."""
    ensure_upload_dir()
    ext = MIME_TO_EXT.get(mime_type, ".jpg")
    filename = f"{uuid.uuid4().hex}{ext}"
    (IMAGES_DIR / filename).write_bytes(image_bytes)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO uploaded_images (name, user_id, created_at) VALUES (?, ?, ?)",
                     (filename, user.id, user_rows.now_iso()))
        conn.commit()
    logger.info(f"Saved image {filename} ({len(image_bytes)} bytes, {mime_type})")
    return f"{URL_PREFIX}{filename}"


def get_image_path(filename: str, user: Optional[User]) -> Optional[Path]:
    """The file of the image ``filename`` (a name or its URL) when it belongs
    to ``user``; None otherwise (someone else's, no owner, a path, missing)."""
    if user is None:
        return None
    name = filename.split("?")[0].split("#")[0]
    name = name.rsplit("/", 1)[-1]
    if not name or name.startswith("."):
        return None
    with get_db_connection() as conn:
        owned = conn.execute("SELECT 1 FROM uploaded_images WHERE name = ? AND user_id = ?",
                             (name, user.id)).fetchone()
    if owned is None:
        return None
    path = IMAGES_DIR / name
    return path if path.is_file() else None


def image_url_to_data_uri(url: str, user: Optional[User]) -> str:
    """``url`` as a data URI for the model ("" when it is not ``user``'s image)."""
    if url.startswith("data:"):
        return url
    path = get_image_path(url, user)
    if not path:
        return ""
    ext = path.suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"
