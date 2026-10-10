"""Offline tests for vLex HTTP behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from vlex_mcp.client import VLexAPIError, VLexClient
from vlex_mcp.config import Settings


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_uses_header_authentication(http_client: httpx.AsyncClient) -> None:
    route = respx.get("https://api.icbg.io/api/v2/workflows").mock(
        return_value=httpx.Response(200, json={"data": [{"name": "legal-research"}]})
    )
    client = VLexClient(Settings(api_key="header-secret"), http_client=http_client)

    response = await client.request_json("public", "GET", "/api/v2/workflows")

    assert response["data"][0]["name"] == "legal-research"
    assert route.calls.last.request.headers["Ocp-Apim-Subscription-Key"] == "header-secret"
    assert "subscription-key" not in route.calls.last.request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure_surfaces_vendor_message(http_client: httpx.AsyncClient) -> None:
    respx.get("https://api.icbg.io/api/v2/workflows").mock(
        return_value=httpx.Response(401, json={"detail": "The subscription key is invalid"})
    )
    client = VLexClient(Settings(api_key="wrong"), http_client=http_client)

    with pytest.raises(VLexAPIError, match="The subscription key is invalid") as exc_info:
        await client.request_json("public", "GET", "/api/v2/workflows")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
@respx.mock
async def test_429_is_retried_with_retry_after(http_client: httpx.AsyncClient) -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    route = respx.get("https://api.icbg.io/api/v2/workflows").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, json={"detail": "slow down"}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    client = VLexClient(
        Settings(api_key="test", max_retries=2, max_backoff_seconds=5),
        http_client=http_client,
        sleep=record_sleep,
    )

    assert await client.request_json("public", "GET", "/api/v2/workflows") == {"data": []}
    assert route.call_count == 2
    assert delays == [2.0]


@pytest.mark.asyncio
@respx.mock
async def test_5xx_is_retried_with_exponential_backoff(http_client: httpx.AsyncClient) -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    route = respx.get("https://api.icbg.io/research/v1/jurisdictions").mock(
        side_effect=[
            httpx.Response(503, text="temporarily unavailable"),
            httpx.Response(200, json={"jurisdictions": []}),
        ]
    )
    client = VLexClient(
        Settings(api_key="test", max_retries=1, backoff_base_seconds=0.25),
        http_client=http_client,
        sleep=record_sleep,
    )

    await client.request_json("research", "GET", "/jurisdictions")

    assert route.call_count == 2
    assert delays == [0.25]


@pytest.mark.asyncio
@respx.mock
async def test_paginate_follows_meta_and_honors_limit(http_client: httpx.AsyncClient) -> None:
    route = respx.get("https://api.icbg.io/api/v2/conversations/c1/messages").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [{"id": "m1"}, {"id": "m2"}],
                    "meta": {"page": 1, "per_page": 3, "total": 3, "total_pages": 2},
                },
            ),
            httpx.Response(
                200,
                json={
                    "data": [{"id": "m3"}],
                    "meta": {"page": 2, "per_page": 1, "total": 3, "total_pages": 2},
                },
            ),
        ]
    )
    client = VLexClient(Settings(api_key="test"), http_client=http_client)

    result = await client.paginate(
        "public",
        "/api/v2/conversations/c1/messages",
        limit=3,
        params={"order": "asc"},
    )

    assert [item["id"] for item in result["data"]] == ["m1", "m2", "m3"]
    assert result["meta"]["returned"] == 3
    assert route.call_count == 2
    assert route.calls[0].request.url.params["per_page"] == "3"
    assert route.calls[1].request.url.params["page"] == "2"
    assert route.calls[1].request.url.params["per_page"] == "1"


@pytest.mark.asyncio
@respx.mock
async def test_exhausted_retry_surfaces_last_vendor_error(http_client: httpx.AsyncClient) -> None:
    async def no_sleep(_: float) -> None:
        return None

    route = respx.get("https://api.icbg.io/api/v2/workflows").mock(
        side_effect=[
            httpx.Response(500, json={"message": "first failure"}),
            httpx.Response(503, json={"message": "final vendor failure"}),
        ]
    )
    client = VLexClient(
        Settings(api_key="test", max_retries=1),
        http_client=http_client,
        sleep=no_sleep,
    )

    with pytest.raises(VLexAPIError, match="final vendor failure"):
        await client.request_json("public", "GET", "/api/v2/workflows")

    assert route.call_count == 2
