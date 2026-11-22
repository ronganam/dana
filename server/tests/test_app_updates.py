"""Android app updates (core/app_updates.py): APKs from GitHub Releases (versions.json, SHA256SUMS) and uploads in one manifest, the newest versionCode winning.

Run: bin/test test_app_updates

A fake GitHub (an in-process aiohttp server, no network) serves the latest
release of ``acme/dana`` with its assets; the tests change that release
between checks.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("app_updates_test")
PUBLISH_TOKEN = "test-publish-token-0123456789abcdef"
os.environ["APP_PUBLISH_TOKEN"] = PUBLISH_TOKEN

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import app_updates

failures = 0


def check(name: str, cond: bool) -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    failures += 0 if cond else 1


def apk(label: str) -> bytes:
    """Bytes that pass for an APK (ZIP magic), distinct per label."""
    return b"PK\x03\x04" + label.encode() * 64


class FakeGitHub:
    """The latest release of acme/dana and its asset files; records each request path."""

    def __init__(self) -> None:
        self.files: Dict[str, bytes] = {}
        self.versions: Dict[str, Any] = {}
        self.tag = ""
        self.requests: List[str] = []
        self.base = ""
        self.omit: set = set()
        self.sums_override: Dict[str, str] = {}

    def release(self, version: str, codes: Dict[str, int]) -> None:
        """Publish release ``v<version>`` with one APK per artifact of ``codes``."""
        self.tag = f"v{version}"
        self.files, self.versions, self.omit, self.sums_override = {}, {}, set(), {}
        for artifact, code in codes.items():
            name = f"dana-{artifact}-{version}.apk"
            data = apk(f"{artifact}{code}")
            self.files[name] = data
            self.versions[artifact] = {"versionName": version, "versionCode": code, "file": name,
                                       "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    def sums(self) -> str:
        return "".join(f"{self.sums_override.get(n, hashlib.sha256(d).hexdigest())}  {n}\n"
                       for n, d in sorted(self.files.items()))

    def asset_names(self) -> List[str]:
        return [n for n in [*self.files, "versions.json", "SHA256SUMS"] if n not in self.omit]

    async def latest(self, request: web.Request) -> web.Response:
        self.requests.append(request.path)
        if request.match_info["repo"] != "acme/dana":
            return web.json_response({"message": "Not Found"}, status=404)
        assets = [{"name": n, "browser_download_url": f"{self.base}/download/{self.tag}/{n}"}
                  for n in self.asset_names()]
        return web.json_response({"tag_name": self.tag, "body": f"Changes in {self.tag}", "assets": assets})

    async def download(self, request: web.Request) -> web.Response:
        self.requests.append(request.path)
        name = request.match_info["name"]
        if name == "versions.json":
            return web.json_response(self.versions)
        if name == "SHA256SUMS":
            return web.Response(text=self.sums())
        if name in self.files:
            return web.Response(body=self.files[name])
        return web.Response(status=404)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/repos/{repo:.+}/releases/latest", self.latest)
        app.router.add_get("/download/{tag}/{name}", self.download)
        return app


def apk_downloads(gh: FakeGitHub) -> List[str]:
    return [p for p in gh.requests if p.endswith(".apk")]


def stored_apks() -> List[str]:
    return sorted(n for n in os.listdir(app_updates.APP_UPDATES_DIR) if n.endswith(".apk"))


async def upload(client: TestClient, artifact: str, name: str, code: int) -> aiohttp.ClientResponse:
    form = aiohttp.FormData()
    form.add_field("artifact", artifact)
    form.add_field("version_name", name)
    form.add_field("version_code", str(code))
    form.add_field("apk", apk(f"upload{artifact}{code}"), filename="app.apk")
    return await client.post("/api/app/upload", data=form, headers={"Authorization": f"Bearer {PUBLISH_TOKEN}"})


async def main() -> None:
    # Disabled: APP_RELEASES_REPO empty (testenv sets it) — nothing is read.
    check("APP_RELEASES_REPO empty -> no repository", app_updates.releases_repo() == "")
    check("disabled: sync installs nothing", await app_updates.sync_releases() == [])
    await asyncio.wait_for(app_updates.releases_loop(), timeout=2)
    check("disabled: the loop returns at once", True)
    os.environ.pop("APP_RELEASES_REPO")
    check("unset -> the project's repository", app_updates.releases_repo() == "ronganam/dana")
    os.environ["APP_RELEASES_REPO"] = ""

    gh = FakeGitHub()
    async with TestServer(gh.app()) as gh_server:
        gh.base = str(gh_server.make_url("")).rstrip("/")
        app_updates.GITHUB_API = gh.base
        site = web.Application()
        app_updates.setup(site)
        async with TestClient(TestServer(site)) as client:
            r = await client.get("/api/app/version")
            check("nothing offered yet -> 404", r.status == 404)

            # A release: phone and wear offered; the watch face / tablet / TV are not update artifacts.
            gh.release("10.0.0", {"phone": 60, "wear": 60, "watchface": 5, "tablet": 60, "tv": 60})
            installed = await app_updates.sync_releases("acme/dana")
            check("release: phone and wear installed", installed == ["phone", "wear"])
            r = await client.get("/api/app/version")
            manifest = await r.json()
            check("manifest: only phone and wear", sorted(manifest) == ["phone", "wear"])
            phone = manifest.get("phone", {})
            check("manifest: phone entry like an upload's",
                  phone.get("versionName") == "10.0.0" and phone.get("versionCode") == 60
                  and phone.get("file") == "phone-10.0.0.apk"
                  and phone.get("sha256") == hashlib.sha256(apk("phone60")).hexdigest()
                  and phone.get("size") == len(apk("phone60"))
                  and phone.get("release_notes") == "Changes in v10.0.0")
            r = await client.get("/api/app/download/phone-10.0.0.apk")
            check("download serves the release's phone APK", r.status == 200 and await r.read() == apk("phone60"))
            r = await client.get("/api/app/download/wear-10.0.0.apk")
            check("download serves the release's wear APK", r.status == 200 and await r.read() == apk("wear60"))
            check("no APK of other artifacts downloaded",
                  sorted(apk_downloads(gh)) == ["/download/v10.0.0/dana-phone-10.0.0.apk",
                                                 "/download/v10.0.0/dana-wear-10.0.0.apk"])

            # The same release again: nothing downloaded.
            gh.requests.clear()
            check("same release again -> nothing installed", await app_updates.sync_releases("acme/dana") == [])
            check("same release again -> no APK downloaded", apk_downloads(gh) == [])

            # An older release is ignored.
            gh.release("9.9.0", {"phone": 59, "wear": 59})
            gh.requests.clear()
            check("older release -> nothing installed", await app_updates.sync_releases("acme/dana") == [])
            check("older release -> no APK downloaded", apk_downloads(gh) == [])
            check("older release -> manifest unchanged", app_updates.offered_version_code("phone") == 60)

            # A checksum mismatch is refused: nothing replaced, no partial file left.
            gh.release("10.1.0", {"phone": 61, "wear": 61})
            gh.sums_override["dana-phone-10.1.0.apk"] = gh.versions["phone"]["sha256"]
            gh.files["dana-phone-10.1.0.apk"] = apk("tampered")
            refused = ""
            try:
                await app_updates.sync_releases("acme/dana")
            except app_updates.ReleaseError as e:
                refused = str(e)
            check("checksum mismatch -> refused", "checksum" in refused)
            check("checksum mismatch -> phone still 60", app_updates.offered_version_code("phone") == 60)
            check("checksum mismatch -> no tampered APK stored", stored_apks() == ["phone-10.0.0.apk", "wear-10.0.0.apk"])
            check("checksum mismatch -> no partial file left",
                  not [n for n in os.listdir(app_updates.APP_UPDATES_DIR) if n.endswith(".part")])

            # versions.json and SHA256SUMS disagree: refused before any download.
            gh.release("10.1.0", {"phone": 61})
            gh.versions["phone"]["sha256"] = "0" * 64
            gh.requests.clear()
            refused = ""
            try:
                await app_updates.sync_releases("acme/dana")
            except app_updates.ReleaseError as e:
                refused = str(e)
            check("versions.json sha256 differs from SHA256SUMS -> refused", "differs" in refused)
            check("... and nothing downloaded", apk_downloads(gh) == [])

            # A release without versions.json is not used.
            gh.release("10.1.0", {"phone": 61})
            gh.omit = {"versions.json"}
            refused = ""
            try:
                await app_updates.sync_releases("acme/dana")
            except app_updates.ReleaseError as e:
                refused = str(e)
            check("no versions.json -> refused", "versions.json" in refused)

            # A repository name that is not owner/name.
            refused = ""
            try:
                await app_updates.sync_releases("acme/../x/y")
            except app_updates.ReleaseError as e:
                refused = str(e)
            check("malformed APP_RELEASES_REPO -> refused", "owner/name" in refused)

            # Uploads and releases: the newest versionCode wins.
            r = await upload(client, "phone", "10.2.0-own", 70)
            check("newer upload -> published", r.status == 200)
            check("newer upload -> offered", app_updates.offered_version_code("phone") == 70)
            check("newer upload -> the release's phone APK pruned", "phone-10.0.0.apk" not in stored_apks())
            gh.release("10.1.0", {"phone": 61, "wear": 61})
            check("release older than the upload -> only wear installed",
                  await app_updates.sync_releases("acme/dana") == ["wear"])
            check("phone stays the upload", app_updates.offered_version_code("phone") == 70)
            r = await upload(client, "phone", "10.0.5", 65)
            body = await r.json()
            check("older upload -> 409", r.status == 409 and body.get("offered_version_code") == 70)
            r = await upload(client, "phone", "../evil", 80)
            check("upload with a path in version_name -> 400", r.status == 400)
            gh.release("11.0.0", {"phone": 80, "wear": 80})
            check("release newer than the upload -> installed",
                  await app_updates.sync_releases("acme/dana") == ["phone", "wear"])
            r = await client.get("/api/app/version")
            manifest = await r.json()
            check("manifest: the release's phone again",
                  manifest["phone"]["versionCode"] == 80 and manifest["phone"]["file"] == "phone-11.0.0.apk")
            check("only the latest APK of each artifact kept", stored_apks() == ["phone-11.0.0.apk", "wear-11.0.0.apk"])
            manifest_file = json.loads(Path(app_updates.MANIFEST_PATH).read_text())
            check("manifest file matches /api/app/version", manifest_file == manifest)


asyncio.run(main())
print(f"\n{'All app update checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
