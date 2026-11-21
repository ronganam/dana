"""Network addresses of a user's services (docs/access.md, "Security rules").

An integration's address field (Home Assistant ``host``, WAHA ``url``,
Frigate ``url``: ``Field(type=ADDRESS)``) holds a base URL that the server
sends requests to. Two rules keep a user from pointing those requests
somewhere else:

- :func:`normalize`: a stored address is ``http`` / ``https``, has a host, no
  user name or password, no query and no fragment (a base ending in ``?x=``
  would turn every path appended to it into a query string). It is stored
  normalised: scheme and host lower case, the default port dropped, no
  trailing slash.
- :func:`join`: every request URL is the base plus a path, built so that the
  result keeps the base's scheme, host, port and path prefix; a path that
  holds a query, a fragment, ``//`` or a ``..`` segment is refused. Variable
  parts of a path (ids) go through :func:`segment`.

:func:`origin` is what the "allowed origins" rule compares (scheme + host +
port).
"""

import ipaddress
import re
from typing import Optional
from urllib.parse import quote, urlsplit, urlunsplit

SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": 80, "https": 443}
# Characters a path may hold as is (everything else in a variable part is percent-encoded by segment()).
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)*$")


def _host(raw: Optional[str]) -> str:
    """The host of a URL as text, lower case; IPv6 in brackets. Raises ValueError when it is not one."""
    host = (raw or "").strip().lower()
    if not host:
        raise ValueError("The address has no host.")
    try:
        ip = ipaddress.ip_address(host)
        return f"[{ip}]" if ip.version == 6 else str(ip)
    except ValueError:
        pass
    if not _HOST_RE.match(host):
        raise ValueError(f"{host!r} is not a host name.")
    return host


def normalize(value: str) -> str:
    """``value`` as a stored address: ``scheme://host[:port][/path]``.

    Raises:
        ValueError: not http(s), no host, a user name / password, a query, a
            fragment, a bad port or characters a path may not hold.
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("The address is empty.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as e:
        raise ValueError(f"Not a valid address ({e}).") from None
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        raise ValueError("The address must start with http:// or https://.")
    if "@" in parts.netloc:
        raise ValueError("The address may not contain a user name or password.")
    if parts.query or text.rstrip("/").endswith("?") or "?" in text:
        raise ValueError("The address may not contain a query (?…).")
    if parts.fragment or "#" in text:
        raise ValueError("The address may not contain a fragment (#…).")
    host = _host(parts.hostname)
    path = parts.path.rstrip("/")
    if path and (not _PATH_RE.match(path) or "//" in path or any(s in (".", "..") for s in path.split("/"))):
        raise ValueError("The address's path holds characters or segments it may not.")
    netloc = host if port is None or port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    return urlunsplit((scheme, netloc, path, "", ""))


def origin(value: str) -> str:
    """Scheme + host + port of an address (``https://ha.example:8443``); default ports dropped.

    Raises:
        ValueError: see :func:`normalize`.
    """
    parts = urlsplit(normalize(value))
    return f"{parts.scheme}://{parts.netloc}"


def segment(value: object) -> str:
    """One variable path segment (an id), percent-encoded so it cannot add a
    segment, a query or a fragment."""
    return quote(str(value), safe="@:+,=!*'()~-._")


def join(base: str, path: str) -> str:
    """The URL of ``path`` (``/api/states``) on the service at ``base``.

    Raises:
        ValueError: ``base`` is not a valid address (a value stored before
            these rules: a query, userinfo, …) or ``path`` is not a plain
            absolute path (a query, a fragment, ``//``, ``..``).
    """
    normal = normalize(base)
    if not path.startswith("/") or path.startswith("//") or not _PATH_RE.match(path) \
            or any(s in (".", "..") for s in path.split("/")):
        raise ValueError(f"Not a plain path: {path!r}")
    url = normal + path
    a, b = urlsplit(normal), urlsplit(url)
    if (a.scheme, a.netloc) != (b.scheme, b.netloc) or b.query or b.fragment:
        raise ValueError(f"{path!r} would leave {origin(normal)}")
    return url
