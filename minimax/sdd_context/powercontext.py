"""PowerContext HTTP client.

Only the explicit-memory endpoints are used: the SDD context module writes one
Memory entry per task state, and reads it back on the next run. No generation
model is required for that path.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from sdd_context.config import PowerContextConfig


class PowerContextError(RuntimeError):
    """The PowerContext server is unreachable or rejected the request."""


class PowerContextClient:
    def __init__(self, config: PowerContextConfig) -> None:
        self.config = config
        self._scope_id: str | None = config.scope_id
        # The PowerContext HTTP transport mis-routes a second request that reuses
        # a keep-alive connection, so every call closes its connection.
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout,
            headers={"Connection": "close"},
        )

    def close(self) -> None:
        self._client.close()

    # -- discovery ------------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            response = self._client.get("/health/ready")
        except httpx.HTTPError as exc:  # pragma: no cover - transport failure
            raise PowerContextError(f"PowerContext is not reachable: {exc}") from exc
        if response.status_code >= 400:
            raise PowerContextError(f"PowerContext is not ready: HTTP {response.status_code}")
        return {"status": response.status_code, "body": _json(response)}

    def scope_id(self) -> str:
        if self._scope_id:
            return self._scope_id
        response = self._request("GET", "/v1/scopes/default")
        self._scope_id = str(response["scope_id"])
        return self._scope_id

    # -- memory ---------------------------------------------------------
    def remember(self, kind: str, text: str, reason: str | None = None) -> dict[str, Any]:
        payload = {"scope_id": self.scope_id(), "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason[:512]
        return self._request("POST", "/v1/memory/remember", payload)

    def list_memories(self) -> list[dict[str, Any]]:
        payload = {"scope_id": self.scope_id()}
        response = self._request("POST", "/v1/memory/entries/list", payload)
        return list(response.get("entries") or [])

    # -- plumbing -------------------------------------------------------
    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, json=payload)
        except httpx.HTTPError as exc:  # pragma: no cover - transport failure
            raise PowerContextError(
                f"PowerContext request failed ({method} {path}): {exc}"
            ) from exc
        if response.status_code >= 400:
            detail = response.text[:400]
            raise PowerContextError(
                f"PowerContext rejected {method} {path}: HTTP {response.status_code} {detail}"
            )
        return _json(response)


def _json(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        return json.loads(response.content.decode("utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise PowerContextError(f"PowerContext returned non-JSON body: {exc}") from exc
