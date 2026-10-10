"""Thin asynchronous HTTP client for the verified vLex API surfaces."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from vlex_mcp.config import Settings

Service = Literal["research", "vincent", "public"]
Sleep = Callable[[float], Awaitable[None]]


class VLexAPIError(ToolError):
    """An HTTP or application-level error returned while calling vLex."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        method: str | None = None,
        url: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.method = method
        self.url = url
        prefix = f"vLex API error ({status_code})" if status_code is not None else "vLex API error"
        super().__init__(f"{prefix}: {message}")


class VLexClient:
    """Call vLex with subscription-key authentication, retries, and pagination."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=settings.timeout_seconds)

    async def __aenter__(self) -> VLexClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the internally created HTTP client, if any."""
        if self._owns_http_client:
            await self._http.aclose()

    def _base_url(self, service: Service) -> str:
        return {
            "research": self.settings.research_base_url,
            "vincent": self.settings.vincent_base_url,
            "public": self.settings.public_base_url,
        }[service].rstrip("/")

    @staticmethod
    def _vendor_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return response.text.strip() or response.reason_phrase

        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                nested = error.get("message") or error.get("detail")
                if nested:
                    return str(nested)
            elif error:
                return str(error)
            for key in ("message", "detail", "title"):
                if body.get(key):
                    return str(body[key])
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = self.settings.backoff_base_seconds * (2**attempt)
            return min(max(0.0, delay), self.settings.max_backoff_seconds)
        return min(
            self.settings.backoff_base_seconds * (2**attempt),
            self.settings.max_backoff_seconds,
        )

    async def request(
        self,
        service: Service,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Make an authenticated request and retry documented transient statuses."""
        url = f"{self._base_url(service)}/{path.lstrip('/')}"
        request_headers = {
            "Ocp-Apim-Subscription-Key": self.settings.api_key,
            "User-Agent": "vlex-mcp/0.1.0",
            **(headers or {}),
        }

        for attempt in range(self.settings.max_retries + 1):
            response = await self._http.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=request_headers,
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self.settings.max_retries:
                await self._sleep(self._retry_delay(response, attempt))
                continue
            if response.is_error:
                raise VLexAPIError(
                    self._vendor_message(response),
                    status_code=response.status_code,
                    method=method,
                    url=url,
                )
            return response

        raise AssertionError("retry loop exited unexpectedly")  # pragma: no cover

    async def request_json(
        self,
        service: Service,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Return a successful JSON response without changing its vendor fields."""
        response = await self.request(
            service,
            method,
            path,
            params=params,
            json_body=json_body,
            headers=headers,
        )
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise VLexAPIError(
                "Expected a JSON response but vLex returned invalid JSON.",
                status_code=response.status_code,
                method=method,
                url=str(response.request.url),
            ) from exc

    async def request_structured(
        self,
        service: Service,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Normalize JSON or SSE responses into a JSON object for MCP tools."""
        response = await self.request(
            service,
            method,
            path,
            params=params,
            json_body=json_body,
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type == "text/event-stream":
            events: list[Any] = []
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    events.append({"done": True})
                    continue
                try:
                    events.append(json.loads(data))
                except json.JSONDecodeError:
                    events.append({"data": data})
            return {"content_type": content_type, "events": events}

        if response.status_code == 204 or not response.content:
            return {}
        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"content_type": content_type or "text/plain", "content": response.text}
        if isinstance(body, dict):
            return body
        return {"data": body}

    async def paginate(
        self,
        service: Service,
        path: str,
        *,
        limit: int,
        page: int = 1,
        max_per_page: int = 100,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Collect a bounded number of items from a one-based vLex list endpoint."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if page < 1:
            raise ValueError("page must be at least 1")

        items: list[Any] = []
        current_page = page
        last_meta: dict[str, Any] = {}
        base_params = dict(params or {})

        while len(items) < limit:
            per_page = min(max_per_page, limit - len(items))
            response = await self.request_json(
                service,
                "GET",
                path,
                params={**base_params, "page": current_page, "per_page": per_page},
            )
            if not isinstance(response, dict) or not isinstance(response.get("data"), list):
                raise VLexAPIError("Paginated response did not contain the expected data list.")

            page_items = response["data"]
            items.extend(page_items[: limit - len(items)])
            meta = response.get("meta")
            last_meta = dict(meta) if isinstance(meta, dict) else {}

            total_pages = last_meta.get("total_pages")
            if not page_items:
                break
            if isinstance(total_pages, int) and current_page >= total_pages:
                break
            if total_pages is None and len(page_items) < per_page:
                break
            current_page += 1

        return {
            "data": items,
            "meta": {
                **last_meta,
                "start_page": page,
                "returned": len(items),
                "limit": limit,
            },
        }
