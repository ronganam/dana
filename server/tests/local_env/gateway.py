"""TCP forwarder of the local test environment (docs/local-testing.md).

The app and the stubs sit on an internal Docker network with no route to the
internet; this is the one container also on a normal network, publishing
ports on 127.0.0.1 and forwarding each to a service on the internal network.

Usage: python gateway.py LISTEN_PORT=HOST:PORT [LISTEN_PORT=HOST:PORT ...]
"""

import asyncio
import sys
from typing import List, Tuple


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _handler(host: str, port: int):
    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        try:
            up_r, up_w = await asyncio.open_connection(host, port)
        except OSError:
            client_w.close()
            return
        await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))
    return handle


async def main(routes: List[Tuple[int, str, int]]) -> None:
    servers = [await asyncio.start_server(_handler(host, port), "0.0.0.0", listen) for listen, host, port in routes]
    for listen, host, port in routes:
        print(f"gateway: :{listen} -> {host}:{port}", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    parsed = []
    for arg in sys.argv[1:]:
        listen, target = arg.split("=", 1)
        host, port = target.rsplit(":", 1)
        parsed.append((int(listen), host, int(port)))
    asyncio.run(main(parsed))
