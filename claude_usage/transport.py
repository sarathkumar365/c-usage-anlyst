"""HTTPS calls to the backend (enroll, ingest) and reachability probes."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from claude_usage.constants import APP_VERSION


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"Request failed with HTTP {status}: {detail}")
        self.status = status


def https_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None

def require_https(url: str):
    # Bearer tokens and the enrollment secret must never travel in clear text.
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        raise RuntimeError(f"Refusing to send credentials to a non-HTTPS URL: {parsed.scheme}://{parsed.hostname}")

def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    require_https(url)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"claude-usage-agent/{APP_VERSION}",
            **(headers or {}),
        },
    )
    kwargs = {"timeout": timeout}
    context = https_context()
    if context is not None:
        kwargs["context"] = context
    try:
        with urllib.request.urlopen(req, **kwargs) as response:
            text = response.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(text) if text else {}
            except json.JSONDecodeError:
                parsed = {"raw": text}
            return {"status": response.status, "response": parsed}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HttpStatusError(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc.reason}") from exc

def probe_url(url: str, method: str = "GET", timeout: int = 10) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", f"claude-usage-agent/{APP_VERSION}")
        kwargs = {"timeout": timeout}
        context = https_context()
        if context is not None:
            kwargs["context"] = context
        with urllib.request.urlopen(req, **kwargs) as response:
            return 200 <= response.status < 500, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return exc.code < 500, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)
