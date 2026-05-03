"""
Async JSON-RPC v2 client for JASP over HTTP POST.

Configuration:
    JASP_RPC_URL  — environment variable, defaults to "http://127.0.0.1:48164/rpc"
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


class JASPClientError(Exception):
    """Raised when JASP returns a JSON-RPC error or the HTTP request fails."""

    def __init__(self, message: str, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class JASPClient:
    """Async HTTP client for JASP's JSON-RPC v2 endpoint."""

    def __init__(self, url: str | None = None, timeout: float = 60.0):
        """
        Args:
            url: JASP RPC endpoint URL.  Defaults to JASP_RPC_URL env var or
                 "http://127.0.0.1:48164/rpc".
            timeout: HTTP request timeout in seconds.
        """
        self.url = (
            url or os.environ.get("JASP_RPC_URL", "http://127.0.0.1:48164/rpc")
        ).rstrip("/")
        self.timeout = timeout
        self._id = 0
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "JASPClient not initialised — use 'async with' context manager"
            )
        return self._client

    async def __aenter__(self) -> "JASPClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Make a JSON-RPC v2 call to JASP.

        Args:
            method: The RPC method name, e.g. "analysis.create".
            params: Keyword parameters for the method.

        Returns:
            The 'result' field from the JSON-RPC response.

        Raises:
            JASPClientError: On JSON-RPC error or HTTP failure.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._next_id(),
        }

        try:
            resp = await self.client.post(self.url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise JASPClientError(
                f"HTTP error calling {method}: {exc}",
                code=getattr(exc, "status_code", None),
            ) from exc

        try:
            body = resp.json()
        except ValueError as exc:
            raise JASPClientError(
                f"Invalid JSON response from JASP for {method}: {resp.text[:500]}"
            ) from exc

        if "error" in body:
            err = body["error"]
            raise JASPClientError(
                f"JASP RPC error on {method}: {err.get('message', str(err))}",
                code=err.get("code"),
                data=err.get("data"),
            )

        return body.get("result")
