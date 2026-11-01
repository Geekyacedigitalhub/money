"""连接自检的测试。

全部使用伪造的 HTTP 响应，因此不需要真实的 Upload-Post 账号，也不会发出任何
网络请求。重点覆盖三种现场最容易踩的坑：Key 写错、profile 名字对不上、
平台加过但没完成授权。
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from app.bot import doctor
from app.bot.models import ALL_PLATFORMS, Platform


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")


def _configure(monkeypatch, api_key="k", profile="mybrand", enabled=True):
    from app.config import config

    monkeypatch.setitem(config.app, "upload_post_api_key", api_key)
    monkeypatch.setitem(config.app, "upload_post_username", profile)
    monkeypatch.setitem(config.app, "upload_post_enabled", enabled)


def _fake_get(routes: dict[str, FakeResponse]):
    calls: list[str] = []

    def _get(url, **kwargs):
        calls.append(url)
        for suffix, response in routes.items():
            if url.endswith(suffix):
                return response
        raise AssertionError(f"unexpected request: {url}")

    return _get, calls


PROFILE_FULL = {
    "success": True,
    "limit": 3,
    "profiles": [
        {
            "username": "mybrand",
            "social_accounts": {
                "tiktok": {"handle": "@ace", "display_name": "Ace", "username": "open_id"},
                "instagram": {"handle": "@ace.ig", "display_name": "Ace IG", "username": "178"},
                "facebook": {"handle": "Ace Page", "display_name": "Ace Page", "username": "99"},
                "youtube": {"handle": "@aceyt", "display_name": "Ace YT", "username": "UC1"},
            },
        }
    ],
}


@pytest.fixture(autouse=True)
def _clear_config(monkeypatch):
    _configure(monkeypatch, api_key="", profile="", enabled=False)


def test_reports_missing_config_without_touching_the_network(monkeypatch):
    with mock.patch.object(doctor.requests, "get") as get:
        report = doctor.check_connection()
    get.assert_not_called()
    assert report.config_ready is False
    assert "upload_post_api_key" in report.config_missing
    assert "Not configured" in doctor.format_report(report, markdown=False)


def test_all_four_connected(monkeypatch):
    _configure(monkeypatch)
    get, _ = _fake_get(
        {
            "/api/uploadposts/me": FakeResponse(200, {"success": True}),
            "/api/uploadposts/users": FakeResponse(200, PROFILE_FULL),
        }
    )
    with mock.patch.object(doctor.requests, "get", side_effect=get):
        report = doctor.check_connection()

    assert report.api_key_valid is True
    assert report.profile_found is True
    assert report.connected_count == 4
    assert report.all_connected is True
    assert report.plan_limit == 3
    text = doctor.format_report(report, markdown=False)
    assert "All four platforms are connected" in text


def test_partially_connected_profile_flags_the_rest(monkeypatch):
    _configure(monkeypatch)
    partial = {
        "success": True,
        "profiles": [
            {
                "username": "mybrand",
                "social_accounts": {
                    "tiktok": {"handle": "@ace"},
                    "instagram": "",  # 加过但没完成授权
                },
            }
        ],
    }
    get, _ = _fake_get(
        {
            "/api/uploadposts/me": FakeResponse(200, {"success": True}),
            "/api/uploadposts/users": FakeResponse(200, partial),
        }
    )
    with mock.patch.object(doctor.requests, "get", side_effect=get):
        report = doctor.check_connection()

    assert report.connected_count == 1
    assert report.all_connected is False
    assert report.status_for(Platform.tiktok).connected is True
    assert report.status_for(Platform.instagram).connected is False
    assert report.status_for(Platform.instagram).note == "never connected"
    assert report.status_for(Platform.youtube).connected is False
    text = doctor.format_report(report, markdown=False)
    assert "Still to connect" in text
    assert "YouTube" in text and "Instagram" in text


def test_expired_login_is_flagged_as_reauth(monkeypatch):
    _configure(monkeypatch)
    expired = {
        "success": True,
        "profiles": [
            {
                "username": "mybrand",
                "social_accounts": {
                    "tiktok": {"handle": "@ace", "reauth_required": True},
                },
            }
        ],
    }
    get, _ = _fake_get(
        {
            "/api/uploadposts/me": FakeResponse(200, {"success": True}),
            "/api/uploadposts/users": FakeResponse(200, expired),
        }
    )
    with mock.patch.object(doctor.requests, "get", side_effect=get):
        report = doctor.check_connection()

    status = report.status_for(Platform.tiktok)
    assert status.connected is True
    assert status.needs_reauth is True
    assert "expired" in status.describe()
    assert report.connected_count == 0  # 过期的连接不算可用


def test_rejected_api_key_is_reported_clearly(monkeypatch):
    _configure(monkeypatch)
    get, _ = _fake_get({"/api/uploadposts/me": FakeResponse(401)})
    with mock.patch.object(doctor.requests, "get", side_effect=get):
        report = doctor.check_connection()

    assert report.api_key_valid is False
    assert "rejected" in report.api_error
    assert "API key rejected" in doctor.format_report(report, markdown=False)


def test_unknown_profile_lists_what_exists(monkeypatch):
    _configure(monkeypatch, profile="typo")
    get, _ = _fake_get(
        {
            "/api/uploadposts/me": FakeResponse(200, {"success": True}),
            "/api/uploadposts/users": FakeResponse(200, PROFILE_FULL),
        }
    )
    with mock.patch.object(doctor.requests, "get", side_effect=get):
        report = doctor.check_connection()

    assert report.profile_found is False
    assert report.available_profiles == ["mybrand"]
    text = doctor.format_report(report, markdown=False)
    assert "mybrand" in text


def test_profile_match_ignores_case(monkeypatch):
    _configure(monkeypatch, profile="MyBrand")
    get, _ = _fake_get(
        {
            "/api/uploadposts/me": FakeResponse(200, {"success": True}),
            "/api/uploadposts/users": FakeResponse(200, PROFILE_FULL),
        }
    )
    with mock.patch.object(doctor.requests, "get", side_effect=get):
        report = doctor.check_connection()

    assert report.profile_found is True
    assert report.profile_name == "mybrand"


def test_network_failure_does_not_raise(monkeypatch):
    import requests

    _configure(monkeypatch)
    with mock.patch.object(
        doctor.requests, "get", side_effect=requests.ConnectionError("boom")
    ):
        report = doctor.check_connection()

    assert report.api_key_valid is None
    assert "could not reach" in report.api_error
    # 网络故障必须报成网络故障，不能误报成"profile 不存在"
    text = doctor.format_report(report, markdown=False)
    assert "Could not reach" in text
    assert "Profile not found" not in text


def test_connect_command_offers_test_buttons_only_for_connected(monkeypatch):
    from app.bot.config import BotSettings
    from app.bot.core import BotCore
    from app.bot.models import ChatMessage
    from app.bot.publisher import SimulatedPublisher

    _configure(monkeypatch)
    partial = {
        "success": True,
        "profiles": [{"username": "mybrand", "social_accounts": {"tiktok": {"handle": "@a"}}}],
    }
    get, _ = _fake_get(
        {
            "/api/uploadposts/me": FakeResponse(200, {"success": True}),
            "/api/uploadposts/users": FakeResponse(200, partial),
        }
    )
    core = BotCore(BotSettings(demo_mode=True), publisher=SimulatedPublisher())
    with mock.patch.object(doctor.requests, "get", side_effect=get):
        replies = core.handle(ChatMessage(chat_id="c", text="/connect", user_id="u"))

    payloads = [button.data for row in replies[0].buttons for button in row]
    assert "test:tiktok" in payloads
    assert "test:instagram" not in payloads  # 没连上就不该给测试按钮


def test_extract_url_finds_post_links():
    from app.bot.publisher import extract_url

    assert extract_url({"results": {"tiktok": {"url": "https://t.co/x"}}}) == "https://t.co/x"
    assert extract_url({"post_url": "https://y.be/abc"}) == "https://y.be/abc"
    assert extract_url({"nothing": 1}) == ""
    assert extract_url("https://direct.example/p") == "https://direct.example/p"


def test_every_platform_has_a_doctor_row(monkeypatch):
    _configure(monkeypatch)
    get, _ = _fake_get(
        {
            "/api/uploadposts/me": FakeResponse(200, {"success": True}),
            "/api/uploadposts/users": FakeResponse(200, PROFILE_FULL),
        }
    )
    with mock.patch.object(doctor.requests, "get", side_effect=get):
        report = doctor.check_connection()
    assert [item.platform for item in report.platforms] == list(ALL_PLATFORMS)
