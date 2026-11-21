"""Fetches the server makes because a user asked for them (docs/access.md,
"Security rules").

A user without ``manage_server`` may make the server download only from the
public internet: :func:`check_public_url` refuses any scheme but http(s) and
any host that is, or resolves to, a loopback, private, link-local, carrier-
grade NAT, reserved or otherwise non-global address — which covers the pod
network, Kubernetes services, the node, cloud metadata (169.254.169.254) and
the home LAN. A name that does not resolve is refused too.

What this can and cannot enforce: it checks the URL before the download
starts. The downloader (yt-dlp, a subprocess) resolves the name again and
follows redirects itself, which cannot be checked from here; for such a user
it therefore runs with site extractors only (``--use-extractors
default,-generic``, addons/knowledge/media.py), which fetch only from
their own sites' APIs and CDNs, never an arbitrary URL a page or redirect
names.
"""

import asyncio
import ipaddress
import socket
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

from core.users import User

SCHEMES = ("http", "https")


class Refused(ValueError):
    """The URL is not one this user may make the server fetch."""


def trusted(user: User) -> bool:
    """A user whose fetches are not restricted (``manage_server``)."""
    return user.level.allows("manage_server")


def _public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _resolve(host: str, port: int) -> List[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _split(url: str) -> Tuple[str, int, Optional[str]]:
    """``(host, port, literal address or None)`` of a URL a user may make the
    server fetch, after the checks that need no lookup.

    Raises:
        Refused: not http(s), no host, a literal address or a name that is not public.
    """
    try:
        parts = urlsplit(str(url or "").strip())
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise Refused("That link is not a valid URL.") from None
    if parts.scheme.lower() not in SCHEMES:
        raise Refused("Only http and https links can be downloaded.")
    host = (parts.hostname or "").strip()
    if not host:
        raise Refused("That link has no host.")
    try:
        literal: Optional[str] = str(ipaddress.ip_address(host))
    except ValueError:
        literal = None
    if literal is not None:
        if not _public(ipaddress.ip_address(literal.split("%")[0])):
            raise Refused(f"{host} is not a public internet address.")
    elif host.lower().rstrip(".") == "localhost" or host.lower().rstrip(".").endswith(".localhost"):
        raise Refused(f"{host} is not a public internet address.")
    return host, port, literal


def check_url_form(url: str) -> None:
    """The checks of :func:`check_public_url` that need no network: for a
    link at the moment a user without ``manage_server`` sets it (the full
    check runs again before any download).

    Raises:
        Refused: why.
    """
    _split(url)


async def check_public_url(url: str) -> None:
    """Refuse ``url`` unless it is http(s) on a host whose every address is public.

    Raises:
        Refused: why (no secret, no resolved address).
    """
    host, port, literal = _split(url)
    if literal is not None:
        return
    try:
        addresses = await _resolve(host, port)
    except (OSError, UnicodeError):
        raise Refused(f"{host} could not be resolved.") from None
    if not addresses or not all(_public(ipaddress.ip_address(a.split("%")[0])) for a in addresses):
        raise Refused(f"{host} is not a public internet address.")
