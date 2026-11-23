"""The phone's app icons (addons/phone/app_icons.py): upload, list, fetch, per user.
Run: bin/test test_app_icons

Two users with the ``phone`` addon (Ann, Bob) and one without (Carl), on the
real handlers. Checked: a PNG uploaded by the phone is listed with the first 8
hex chars of its sha256 and served back byte for byte (image/png, private
cache, ETag = the hash, 304 on If-None-Match), a replaced icon gets its new
hash; each user's icons are their own both ways (another user's package
answers 404 and is not listed); no addon → 403 ``not_connected`` on every
route; bad package names (``../x``, ``..``, no dot, too long) → 400
``bad_package`` and nothing written outside the folder; not a PNG, not
square, too small or big → 400 ``not_png``; over 64 KB → 413 ``too_large``;
the 501st icon → 409 ``too_many`` (replacing one still works); removing a
user removes their folder.
"""

import asyncio
import hashlib
import os
import struct
import sys
import zlib
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("app_icons_test")
os.environ["API_TOKEN"] = "app-icons-test-env-token"  # a test value: the first admin's token (a headless install)
from cryptography.fernet import Fernet  # noqa: E402
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, devices, paths, users  # noqa: E402

db.migrate()
users.bootstrap()

from core.auth import setup_auth  # noqa: E402
from core.addons import store  # noqa: E402
from addons.phone import api as phone_api, app_icons  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ANN = users.create("Ann")
BOB = users.create("Bob")
CARL = users.create("Carl")
for _u in (ANN, BOB):
    store.connect(_u, "phone")
store.connect(CARL, "tasks")
TOKENS = {u.id: devices.create(u, "phone")[0] for u in (ANN, BOB, CARL)}


def auth(user, **extra: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}", **extra}


def png(width: int, height: int = 0, rgb: Tuple[int, int, int] = (37, 211, 102)) -> bytes:
    """A real solid-colour PNG."""
    height = height or width

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (app_icons.PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def sha8(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


async def put(client, user, package: str, data: bytes):
    return await client.put(f"/api/phone/app-icons/{package}", data=data,
                            headers=auth(user, **{"Content-Type": "image/png"}))


async def listing(client, user) -> Dict[str, str]:
    r = await client.get("/api/phone/app-icons", headers=auth(user))
    assert r.status == 200, r.status
    return (await r.json())["icons"]


async def test_flow(client) -> None:
    check("nothing stored yet → empty list", await listing(client, ANN) == {})
    wa = png(96)
    r = await put(client, ANN, "com.whatsapp", wa)
    check("PUT a 96 px PNG → 204", r.status == 204, r.status)
    check("listed with its sha256's first 8 hex", await listing(client, ANN) == {"com.whatsapp": sha8(wa)})
    stored = paths.APP_ICONS_DIR / ANN.id / "com.whatsapp.png"
    check("stored as <data dir>/app-icons/<user id>/<package>.png", stored.read_bytes() == wa)

    r = await client.get("/api/phone/app-icons/com.whatsapp.png", headers=auth(ANN))
    body = await r.read()
    check("GET → the same bytes", r.status == 200 and body == wa, r.status)
    check("GET → image/png, private cache, ETag the hash",
          r.headers.get("Content-Type") == "image/png"
          and r.headers.get("Cache-Control") == "private, max-age=86400"
          and r.headers.get("ETag") == f'"{sha8(wa)}"', dict(r.headers))
    r = await client.get("/api/phone/app-icons/com.whatsapp.png",
                         headers=auth(ANN, **{"If-None-Match": f'"{sha8(wa)}"'}))
    check("If-None-Match with the hash → 304, no body", r.status == 304 and await r.read() == b"", r.status)
    r = await client.get("/api/phone/app-icons/com.whatsapp.png", headers=auth(ANN, **{"If-None-Match": '"00000000"'}))
    check("If-None-Match with another hash → 200", r.status == 200, r.status)
    r = await client.get("/api/phone/app-icons/com.example.none.png", headers=auth(ANN))
    check("no such icon → 404", r.status == 404 and (await r.json())["error"] == "not_found", r.status)

    newer = png(96, rgb=(10, 20, 30))
    await put(client, ANN, "com.whatsapp", newer)
    check("a replaced icon is listed with its new hash", await listing(client, ANN) == {"com.whatsapp": sha8(newer)})
    r = await client.get("/api/phone/app-icons/com.whatsapp.png",
                         headers=auth(ANN, **{"If-None-Match": f'"{sha8(wa)}"'}))
    check("the old hash no longer matches → 200 with the new bytes", r.status == 200 and await r.read() == newer)
    for side in (48, 192):
        r = await put(client, ANN, f"com.example.s{side}", png(side))
        check(f"{side} px is accepted", r.status == 204, r.status)


async def test_refusals(client) -> None:
    before = sorted(p.relative_to(paths.DATA_DIR) for p in paths.DATA_DIR.rglob("*") if p.is_file())
    for package in ("..%2Fx", "..", "whatsapp", "com..x", "1com.x", "com.x-y", "a." + "b" * 200):
        r = await put(client, ANN, package, png(96))
        code = (await r.json()).get("error") if r.status == 400 else None
        check(f"package {package[:20]!r} → 400 bad_package (or no route)",
              (r.status, code) == (400, "bad_package") or r.status == 404, (r.status, code))
    after = sorted(p.relative_to(paths.DATA_DIR) for p in paths.DATA_DIR.rglob("*") if p.is_file())
    check("a refused package writes no file anywhere", before == after, set(after) - set(before))
    r = await client.get("/api/phone/app-icons/..%2Fx.png", headers=auth(ANN))
    check("GET of a bad package → 404", r.status == 404, r.status)

    for label, data in (("not a PNG", b"GIF89a" + b"\x00" * 100), ("not square", png(96, 64)),
                        ("too small", png(32)), ("too big", png(256)), ("empty", b"")):
        r = await put(client, ANN, "com.example.bad", data)
        code = (await r.json()).get("error")
        check(f"{label} → 400 not_png", (r.status, code) == (400, "not_png"), (r.status, code))
    big = png(96)
    big = big + b"\x00" * (app_icons.MAX_BYTES + 1 - len(big))
    r = await put(client, ANN, "com.example.big", big)
    check("over 64 KB → 413 too_large", r.status == 413 and (await r.json())["error"] == "too_large", r.status)
    check("refused uploads are not listed", not any(p.startswith("com.example.b") for p in await listing(client, ANN)))


async def test_users(client) -> None:
    bank = png(64, rgb=(200, 0, 0))
    await put(client, BOB, "com.example.bank", bank)
    check("Bob sees only his icons", await listing(client, BOB) == {"com.example.bank": sha8(bank)})
    check("Ann does not see Bob's", "com.example.bank" not in await listing(client, ANN))
    r = await client.get("/api/phone/app-icons/com.example.bank.png", headers=auth(ANN))
    check("Bob's package fetched by Ann → 404", r.status == 404, r.status)
    r = await client.get("/api/phone/app-icons/com.whatsapp.png", headers=auth(BOB))
    check("Ann's package fetched by Bob → 404", r.status == 404, r.status)
    own = png(80, rgb=(1, 2, 3))
    await put(client, BOB, "com.whatsapp", own)
    r = await client.get("/api/phone/app-icons/com.whatsapp.png", headers=auth(ANN))
    check("the same package under two users stays two icons", await r.read() != own)

    for label, call in (("list", lambda: client.get("/api/phone/app-icons", headers=auth(CARL))),
                        ("put", lambda: put(client, CARL, "com.whatsapp", png(96))),
                        ("get", lambda: client.get("/api/phone/app-icons/com.whatsapp.png", headers=auth(CARL)))):
        r = await call()
        body = await r.json()
        check(f"no phone addon: {label} → 403 not_connected", r.status == 403 and body.get("error") == "not_connected",
              (r.status, body))
    check("Carl got no folder", not (paths.APP_ICONS_DIR / CARL.id).exists())


async def test_limit(client) -> None:
    folder = paths.APP_ICONS_DIR / BOB.id
    data = png(48)
    have = len(list(folder.glob("*.png")))
    for i in range(app_icons.MAX_ICONS - have):
        (folder / f"com.example.app{i}.png").write_bytes(data)
    check("500 icons listed", len(await listing(client, BOB)) == app_icons.MAX_ICONS)
    r = await put(client, BOB, "com.example.one_more", png(96))
    check("the 501st icon → 409 too_many", r.status == 409 and (await r.json())["error"] == "too_many", r.status)
    r = await put(client, BOB, "com.example.bank", png(96))
    check("replacing one at the limit → 204", r.status == 204, r.status)
    r = await put(client, ANN, "com.example.one_more", png(96))
    check("the limit is per user: Ann can still add", r.status == 204, r.status)


def test_remove_user() -> None:
    check("Bob has a folder", (paths.APP_ICONS_DIR / BOB.id).is_dir())
    users.remove(BOB.id)
    check("removing Bob removes his folder", not (paths.APP_ICONS_DIR / BOB.id).exists())
    check("Ann's icons stay", (paths.APP_ICONS_DIR / ANN.id / "com.whatsapp.png").is_file())


async def main() -> None:
    app = web.Application()
    setup_auth(app)
    phone_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        await test_flow(client)
        await test_refusals(client)
        await test_users(client)
        await test_limit(client)
    test_remove_user()


asyncio.run(main())
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
