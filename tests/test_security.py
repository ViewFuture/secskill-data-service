"""SSRF / URL 安全校验测试（禁止真实出网）。"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app import MAX_FEED_BYTES, fetch_json_feed, validate_public_https_url


def test_reject_http_url():
    with pytest.raises(ValueError, match="HTTPS"):
        validate_public_https_url("http://example.com/jobs.json")


def test_reject_localhost_hostname():
    with pytest.raises(ValueError, match="localhost"):
        validate_public_https_url("https://localhost/jobs.json")


def test_reject_loopback_ip():
    with pytest.raises(ValueError, match="public"):
        validate_public_https_url("https://127.0.0.1/jobs.json")


def test_reject_192_168_private_ip():
    with pytest.raises(ValueError, match="public"):
        validate_public_https_url("https://192.168.1.10/jobs.json")


def test_reject_10_x_private_ip():
    with pytest.raises(ValueError, match="public"):
        validate_public_https_url("https://10.0.0.8/jobs.json")


@pytest.mark.parametrize(
    "ip",
    [
        "172.16.0.1",
        "172.20.5.5",
        "172.31.255.255",
    ],
)
def test_reject_172_16_to_172_31_private_ips(ip: str):
    with pytest.raises(ValueError, match="public"):
        validate_public_https_url(f"https://{ip}/jobs.json")


def test_reject_url_with_userinfo():
    with pytest.raises(ValueError, match="userinfo"):
        validate_public_https_url("https://user:pass@example.org/feed.json")


def test_allow_mocked_public_https_domain_without_network():
    """允许公网域名：mock DNS，禁止真实访问互联网。"""
    fake_addrinfo = [
        (2, 1, 6, "", ("8.8.8.8", 0)),  # 模拟公网解析结果，不发起真实 DNS
    ]

    with patch("app.socket.getaddrinfo", return_value=fake_addrinfo) as mocked_dns:
        result = validate_public_https_url("https://jobs.example.org/public/feed.json")

    assert result == "https://jobs.example.org/public/feed.json"
    mocked_dns.assert_called()


def _client_with_transport(transport: httpx.MockTransport):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        kwargs["follow_redirects"] = False
        return real_client(*args, **kwargs)

    return factory


@pytest.mark.asyncio
async def test_fetch_rejects_redirect_to_private_ip_without_network():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "jobs.example.org":
            return httpx.Response(
                302,
                headers={"Location": "https://127.0.0.1/internal"},
            )
        return httpx.Response(200, json={"jobs": []})

    transport = httpx.MockTransport(handler)
    with patch("app.httpx.AsyncClient", side_effect=_client_with_transport(transport)):
        with patch(
            "app.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("8.8.8.8", 0))],
        ):
            with pytest.raises(ValueError, match="public|localhost"):
                await fetch_json_feed("https://jobs.example.org/feed.json")


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_feed_without_network():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{" + b"x" * (MAX_FEED_BYTES + 10),
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)
    with patch("app.httpx.AsyncClient", side_effect=_client_with_transport(transport)):
        with patch(
            "app.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("8.8.8.8", 0))],
        ):
            with pytest.raises(ValueError, match="too large"):
                await fetch_json_feed("https://jobs.example.org/feed.json")
