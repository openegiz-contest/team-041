"""Minimal Ditto HTTP client on the standard library.

Ditto answers 503 or 408 for a few seconds at a time while its JVMs warm up,
so every call retries those (and connection errors) a bounded number of times.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


class DittoError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, body: str):
        super().__init__(f"{method} {path} -> {status}: {body[:300]}")
        self.status = status


class DittoClient:
    def __init__(self, base_url: str, user: str, password: str, attempts: int = 20, delay_s: float = 3.0):
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth = f"Basic {token}"
        self.attempts, self.delay_s = attempts, delay_s

    def request(self, method: str, path: str, body=None, headers: dict | None = None,
                accept: tuple[int, ...] = ()) -> tuple[int, object]:
        """Send one request; return (status, parsed JSON or None).

        Statuses listed in `accept` are returned to the caller instead of raised.
        """
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        all_headers = {"Authorization": self.auth, "Accept": "application/json"}
        if data is not None:
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        last = "no attempt made"
        for attempt in range(1, self.attempts + 1):
            req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=all_headers)
            try:
                with urllib.request.urlopen(req, timeout=70) as resp:
                    return resp.status, _parse(resp.read())
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", "replace")
                if exc.code in accept:
                    return exc.code, _parse(text.encode())
                if exc.code not in RETRY_STATUSES:
                    raise DittoError(exc.code, method, path, text) from None
                last = f"{exc.code} {text[:120]}"
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = str(exc)
            log.warning("%s %s failed (%s), retry %d of %d", method, path, last, attempt, self.attempts)
            time.sleep(self.delay_s)
        raise DittoError(0, method, path, f"gave up after {self.attempts} attempts: {last}")

    def create_thing(self, thing: dict) -> bool:
        """Create the thing unless it exists. Return True if it was created."""
        path = "/api/2/things/" + urllib.parse.quote(thing["thingId"])
        body = {k: v for k, v in thing.items() if k != "thingId"}
        status, _ = self.request("PUT", path, body, headers={"If-None-Match": "*"}, accept=(412,))
        return status != 412

    def put_attributes(self, thing_id: str, attributes: dict) -> None:
        self.request("PUT", f"/api/2/things/{urllib.parse.quote(thing_id)}/attributes", attributes)

    def get_thing(self, thing_id: str) -> dict | None:
        status, body = self.request("GET", "/api/2/things/" + urllib.parse.quote(thing_id), accept=(404,))
        return None if status == 404 else body

    def delete_thing(self, thing_id: str) -> None:
        self.request("DELETE", "/api/2/things/" + urllib.parse.quote(thing_id), accept=(404,))

    def get_things(self, thing_ids: list[str], chunk: int = 40) -> list[dict]:
        """The things with these IDs, complete with attributes and features,
        read from the things service itself (not from the search index, which
        lags behind by a few seconds)."""
        things: list[dict] = []
        for start in range(0, len(thing_ids), chunk):
            ids = ",".join(thing_ids[start:start + chunk])
            _, page = self.request("GET", "/api/2/things?" + urllib.parse.urlencode({"ids": ids}))
            things += page or []
        return things

    def send_message(self, thing_id: str, subject: str, body: dict) -> int:
        """Send a live message to the twin's inbox without waiting for an answer
        (docs/mine/ARCHITECTURE.md, section 5.2). Returns the HTTP status, 202 when accepted."""
        path = f"/api/2/things/{urllib.parse.quote(thing_id)}/inbox/messages/{urllib.parse.quote(subject)}?timeout=0"
        status, _ = self.request("POST", path, body, accept=(400, 404))
        return status

    def search_ids(self, namespace: str) -> list[str]:
        """IDs of every thing in the namespace, following the search cursor."""
        ids, cursor = [], None
        while True:
            option = "size(200)" + (f",cursor({cursor})" if cursor else "")
            query = urllib.parse.urlencode({"namespaces": namespace, "fields": "thingId", "option": option})
            _, page = self.request("GET", f"/api/2/search/things?{query}")
            ids += [item["thingId"] for item in page.get("items", [])]
            cursor = page.get("cursor")
            if not cursor:
                return ids


def _parse(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")
