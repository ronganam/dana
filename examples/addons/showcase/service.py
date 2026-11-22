"""The plant service the ``showcase`` tools ask for care tips: ``GET <base>/tips?plant=<name>``.

The base address is ``https://api.plants.example`` (declared in the manifest's
``touches``), or ``SHOWCASE_API_URL`` (declared in its ``needs``), which the
tests point at a fake on this machine.
"""

import os

from aiohttp import ClientError, ClientSession, ClientTimeout

from core.addons import Connection

# --- reference: http ---
BASE_URL = "https://api.plants.example"


class ServiceError(RuntimeError):
    """The plant service refused or did not answer; the message says so in plain words."""


async def tip(conn: Connection, plant: str) -> str:
    """The service's care tip for ``plant``, asked with the user's key."""
    base = os.getenv("SHOWCASE_API_URL", BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {conn.credentials.get('api_key') or ''}"}
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as session, \
                session.get(f"{base}/tips", params={"plant": plant}, headers=headers) as r:
            if r.status == 401:
                raise ServiceError("The plant service refused the API key.")
            r.raise_for_status()
            return str((await r.json())["tip"])
    except ClientError as e:
        raise ServiceError(f"The plant service did not answer ({type(e).__name__}).") from e
# --- end ---
