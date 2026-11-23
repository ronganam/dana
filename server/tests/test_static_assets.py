"""The web pages load every script from the server itself: vendored files of exact versions, no CDN.

A script from a CDN runs with the page's session, so an unpinned one
(``lucide@latest``) is code nobody reviewed; a pinned one still breaks offline.
Checks every ``<script src>`` of ``static/*.html`` and the service worker's
precache list (``static/sw.js``), and that each vendored library names its
exact version (``THIRD_PARTY.md`` lists them). The app's fonts are self-hosted too
(``static/vendor/fonts``: declared in ``styles.css``, precached), no font CDN.
Run: bin/test test_static_assets
"""

import re

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("static_assets_test")

STATIC = testenv.SERVER / "static"
VENDORED = {"lucide.min.js": r"lucide v\d+\.\d+\.\d+", "marked.min.js": r"marked v\d+\.\d+\.\d+",
            "purify.min.js": r"DOMPurify \d+\.\d+\.\d+"}

failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


sw = (STATIC / "sw.js").read_text(encoding="utf-8")
remote = []
for page in sorted(STATIC.glob("*.html")):
    for src in re.findall(r"<script[^>]*\ssrc=[\"']([^\"']+)", page.read_text(encoding="utf-8")):
        if not src.startswith("/"):
            remote.append(f"{page.name}: {src}")
check("pages: every <script src> is the server's own (no CDN)", not remote, remote)

font_cdn = [p.name for p in sorted(STATIC.glob("*.html")) if "fonts.googleapis" in p.read_text(encoding="utf-8")
            and p.name != "calendar-tv.html"]
check("pages: the app's fonts are self-hosted (static/vendor/fonts), no font CDN", not font_cdn, font_cdn)
for font in ("inter-latin", "dm-mono-regular", "instrument-serif-regular", "instrument-serif-italic"):
    check(f"vendor/fonts/{font}.woff2: present, declared in styles.css and precached",
          (STATIC / "vendor" / "fonts" / f"{font}.woff2").is_file()
          and f"/static/vendor/fonts/{font}.woff2" in (STATIC / "styles.css").read_text(encoding="utf-8")
          and f"/static/vendor/fonts/{font}.woff2" in sw)

cached_scripts = [u for u in re.findall(r"\"(https?://[^\"]+)\"", sw) if u.endswith(".js") or "lucide" in u]
check("service worker: precaches no remote script", not cached_scripts, cached_scripts)

for name, version in VENDORED.items():
    path = STATIC / "vendor" / name
    head = path.read_text(encoding="utf-8")[:400] if path.exists() else ""
    check(f"vendor/{name}: present, with its exact version in the header", bool(re.search(version, head)), head[:120])
    check(f"vendor/{name}: loaded by index.html and precached", f"/static/vendor/{name}" in sw
          and f"/static/vendor/{name}" in (STATIC / "index.html").read_text(encoding="utf-8"))

print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
