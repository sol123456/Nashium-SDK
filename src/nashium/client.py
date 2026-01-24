from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NashiumClientConfig:
    base_url: str
    bearer_token: str | None = None
    timeout_seconds: float = 30.0


class NashiumClient:
    def __init__(self, config: NashiumClientConfig):
        self._config = config

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = self._config.base_url.rstrip("/") + "/" + path.lstrip("/")
        data = None
        headers = {"Accept": "application/json"}

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if self._config.bearer_token:
            headers["Authorization"] = f"Bearer {self._config.bearer_token}"

        req = urllib.request.Request(url=url, data=data, method=method.upper(), headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                body = resp.read()
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read()
            text = body.decode("utf-8", errors="replace") if body else ""
            raise RuntimeError(f"HTTP {e.code} {e.reason}: {text}") from e
