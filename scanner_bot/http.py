"""Minimal JSON-over-HTTPS helper built on the standard library."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    def __init__(self, status: int | None, message: str, body: Any = None):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.body = body


def post_json(url: str, payload: dict, headers: dict | None = None, timeout: float = 30.0) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw
        raise HttpError(e.code, str(body)[:500], body) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HttpError(None, f"request to {url.split('/bot')[0]} failed: {e}") from e
