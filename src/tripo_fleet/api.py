"""Minimal client for the Tripo v3 REST API (files, multiview generation, tasks, balance).

Endpoints and error handling follow developers.tripo3d.com (read 2026-10-18); the host is
openapi.tripo3d.ai for keys from the international platform (see config.DEFAULT_API_BASE):
POST /files (multipart field `file`), POST /generation/multiview-to-model,
GET /tasks/{task_id}, GET /account/balance. Errors carry code/message/suggestion/request_id.
"""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any, Callable

import requests

RETRYABLE_HTTP = {429, 500, 502, 503, 504}
TERMINAL_STATES = {"success", "failed", "cancelled", "banned", "expired"}


class TripoError(RuntimeError):
    def __init__(self, http_status: int, code: int | None, message: str,
                 suggestion: str = "", request_id: str = ""):
        self.http_status = http_status
        self.code = code
        self.suggestion = suggestion
        self.request_id = request_id
        detail = f"HTTP {http_status}, code {code}: {message}"
        if suggestion:
            detail += f" ({suggestion})"
        if request_id:
            detail += f" [request_id {request_id}]"
        super().__init__(detail)


class TripoClient:
    def __init__(self, api_key: str, base_url: str, session: requests.Session | None = None,
                 max_retries: int = 5, sleep: Callable[[float], None] = time.sleep):
        self._base = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"
        self._max_retries = max_retries
        self._sleep = sleep

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = f"{self._base}{path}"
        for attempt in range(self._max_retries + 1):
            response = self._session.request(method, url, timeout=120, **kwargs)
            # Generation creation has no idempotency key: retrying can spend twice.
            can_retry = method.upper() == "GET" or path == "/files"
            if can_retry and response.status_code in RETRYABLE_HTTP and attempt < self._max_retries:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                self._sleep(delay)
                # Rewind file handles before resending a multipart upload
                for handle in (kwargs.get("files") or {}).values():
                    if hasattr(handle[1], "seek"):
                        handle[1].seek(0)
                continue
            try:
                body = response.json()
            except ValueError:
                raise TripoError(response.status_code, None, response.text[:300]) from None
            if response.status_code >= 400 or body.get("code", 0) != 0:
                raise TripoError(response.status_code, body.get("code"), body.get("message", ""),
                                 body.get("suggestion", ""), body.get("request_id", ""))
            return body.get("data", {})
        raise AssertionError("unreachable")

    def balance(self) -> dict:
        return self._request("GET", "/account/balance")

    def upload(self, path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            data = self._request("POST", "/files", files={"file": (path.name, handle, mime)})
        return data["file_token"]

    def create_multiview(self, payload: dict) -> str:
        data = self._request("POST", "/generation/multiview-to-model", json=payload)
        return data["task_id"]

    def get_task(self, task_id: str) -> dict:
        return self._request("GET", f"/tasks/{task_id}")

    def wait(self, task_id: str, interval: float = 2.0, timeout: float = 1200.0,
             on_progress: Callable[[dict], None] | None = None) -> dict:
        start = time.monotonic()
        last = None
        while True:
            task = self.get_task(task_id)
            marker = (task.get("status"), task.get("progress"))
            if on_progress and marker != last:
                on_progress(task)
                last = marker
            if task.get("status") in TERMINAL_STATES:
                return task
            if time.monotonic() - start > timeout:
                raise TimeoutError(f"{task_id} still {task.get('status')} after {timeout:.0f}s")
            self._sleep(interval)


def download(url: str, dest: Path) -> int:
    """Download a signed output URL (they expire after about 5 minutes). No auth header."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    size = 0
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
                size += len(chunk)
    tmp.replace(dest)
    return size
