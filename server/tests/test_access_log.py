"""The access log keeps changes, errors and slow requests only (core/debug_api.py QuietAccessLog, installed by main.py).

Lines are made by aiohttp's own AccessLogger (its default format, and one with ``%Tf``) for mocked requests;
routine successful GETs are dropped, everything else is kept.

Run: bin/test test_access_log
"""

import logging
import sys
from typing import List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("access_log_test")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import make_mocked_request  # noqa: E402
from aiohttp.web_log import AccessLogger  # noqa: E402

from core.debug_api import QuietAccessLog  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: object, detail: object = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def logged(fmt: str, method: str, path: str, status: int, seconds: float) -> bool:
    logger = logging.getLogger(f"access-test-{len(results)}")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    capture = Capture()
    logger.addHandler(capture)
    logger.addFilter(QuietAccessLog())
    AccessLogger(logger, fmt).log(make_mocked_request(method, path), web.Response(status=status), seconds)
    return len(capture.lines) == 1


DEFAULT = AccessLogger.LOG_FORMAT
TIMED = '%a %t "%r" %s %b %Tf "%{Referer}i" "%{User-Agent}i"'

for fmt, name in ((DEFAULT, "default format"), (TIMED, "with %Tf")):
    check(f"{name}: a routine GET 200 is dropped", not logged(fmt, "GET", "/api/phone/events?after=3", 200, 0.02))
    check(f"{name}: a GET 304 is dropped", not logged(fmt, "GET", "/static/app.js", 304, 0.01))
    check(f"{name}: POST is kept", logged(fmt, "POST", "/api/signals", 200, 0.02))
    check(f"{name}: DELETE is kept", logged(fmt, "DELETE", "/api/sessions/x", 204, 0.02))
    check(f"{name}: a GET 404 is kept", logged(fmt, "GET", "/nope", 404, 0.01))
    check(f"{name}: a GET 429 is kept", logged(fmt, "GET", "/api/phone/events", 429, 0.01))
    check(f"{name}: a GET 500 is kept", logged(fmt, "GET", "/api/now", 500, 0.01))
    check(f"{name}: a slow GET (3 s) is kept", logged(fmt, "GET", "/api/now", 200, 3.0))
check("with %Tf: a GET of 1.2 s is kept (exact time)", logged(TIMED, "GET", "/api/now", 200, 1.2))
check("with %Tf: a GET of 0.9 s is dropped", not logged(TIMED, "GET", "/api/now", 200, 0.9))

plain = logging.makeLogRecord({"name": "aiohttp.access", "msg": "something else"})
check("a record that is not an access line is kept", QuietAccessLog().filter(plain))

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
