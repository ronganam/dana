"""The server's public address: what a phone, a QR code or Google's sign-in
should use to reach it.

``PUBLIC_URL`` (e.g. ``https://dana.example.com``) when set; otherwise the
address a request came to, as the nearest proxy reports it
(``X-Forwarded-Proto`` / ``X-Forwarded-Host``, first value) or as the request
itself shows it.
"""

import os
from typing import Optional

from aiohttp import web


def configured() -> str:
    """``PUBLIC_URL`` without a trailing slash ("" when unset)."""
    return (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")


def _first(value: Optional[str]) -> str:
    return (value or "").split(",")[0].strip()


def base_url(request: web.Request) -> str:
    """The public base URL (no trailing slash) for links given in answer to ``request``."""
    base = configured()
    if base:
        return base
    scheme = _first(request.headers.get("X-Forwarded-Proto")) or request.scheme
    host = _first(request.headers.get("X-Forwarded-Host")) or request.host
    return f"{scheme}://{host}"
