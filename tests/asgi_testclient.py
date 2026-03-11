"""
Compatibility test client for environments where Starlette's TestClient hangs.

The project test suite is largely synchronous even when individual tests are
marked async, so this wrapper exposes a blocking interface while dispatching
requests through httpx's ASGI transport.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx


class ASGITestClient:
    """A minimal FastAPI/Starlette TestClient-compatible wrapper."""

    __test__ = False

    def __init__(
        self,
        app: Any,
        *,
        base_url: str = "http://localhost",
        raise_server_exceptions: bool = True,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        follow_redirects: bool = True,
        **_: Any,
    ) -> None:
        self.app = app
        self.base_url = base_url
        self.raise_server_exceptions = raise_server_exceptions
        self.follow_redirects = follow_redirects
        self.headers = httpx.Headers(headers or {})
        self.cookies = httpx.Cookies(cookies or {})
        self._closed = False

    def __enter__(self) -> "ASGITestClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._closed:
            raise RuntimeError("TestClient is closed")

        if "allow_redirects" in kwargs and "follow_redirects" not in kwargs:
            kwargs["follow_redirects"] = kwargs.pop("allow_redirects")

        response = self._run_in_thread(self._request_async, method, url, **kwargs)
        self.cookies.update(response.cookies)
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("OPTIONS", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("HEAD", url, **kwargs)

    async def _request_async(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        transport = httpx.ASGITransport(
            app=self.app,
            raise_app_exceptions=self.raise_server_exceptions,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url=self.base_url,
            headers=self.headers,
            cookies=self.cookies,
            follow_redirects=self.follow_redirects,
        ) as client:
            return await client.request(method, url, **kwargs)

    def _run_in_thread(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(func(*args, **kwargs))

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(func(*args, **kwargs))
            except BaseException as exc:  # pragma: no cover - passthrough
                error["exc"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()

        if "exc" in error:
            raise error["exc"]

        return result["value"]
