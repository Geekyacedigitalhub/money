"""Direct publishing: auth routing, error translation and the publisher contract.

No network here. Every HTTP call is faked, because the point of these tests is
that a wrong token or an exhausted quota produces a sentence a person can act on
— not a stack trace.
"""

from __future__ import annotations

import json

import pytest

from app.bot import direct
from app.bot.direct import facebook, tokens, youtube
from app.bot.models import Caption, Platform


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text or json.dumps(self._payload)
        self.content = b"x"

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def isolated_tokens(tmp_path, monkeypatch):
    store = tmp_path / "tokens.json"
    monkeypatch.setattr(tokens, "tokens_path", lambda: str(store))
    return store


def test_youtube_authorize_url_asks_for_offline_access():
    url = youtube.authorize_url("client-abc")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert youtube.SCOPE.replace("/", "%2F").replace(":", "%3A") in url


def test_youtube_upload_translates_quota_exhaustion(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    monkeypatch.setattr(youtube, "access_token", lambda *a, **k: "token")
    monkeypatch.setattr(
        youtube.requests,
        "post",
        lambda *a, **k: FakeResponse(
            403,
            {
                "error": {
                    "message": "The request cannot be completed because you have "
                    "exceeded your quota.",
                    "errors": [{"reason": "quotaExceeded"}],
                }
            },
        ),
    )

    result = youtube.publish(str(video), Caption(title="t"))
    assert result.ok is False
    assert "6 uploads/day" in result.detail


def test_youtube_upload_returns_a_clickable_url(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    monkeypatch.setattr(youtube, "access_token", lambda *a, **k: "token")
    monkeypatch.setattr(
        youtube.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"id": "vid123"}, headers={"Location": "https://up"}),
    )
    monkeypatch.setattr(
        youtube.requests, "put", lambda *a, **k: FakeResponse(200, {"id": "vid123"})
    )

    result = youtube.publish(str(video), Caption(title="hello"))
    assert result.ok is True
    assert result.url == "https://youtu.be/vid123"


def test_youtube_reports_a_dead_refresh_token(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    def explode(*a, **k):
        raise RuntimeError("YouTube is not connected — run: cli connect youtube")

    monkeypatch.setattr(youtube, "access_token", explode)
    result = youtube.publish(str(video), Caption(title="t"))
    assert result.ok is False
    assert "connect youtube" in result.detail


def test_facebook_page_token_is_verified_before_it_is_saved(monkeypatch):
    monkeypatch.setattr(
        facebook.requests,
        "get",
        lambda *a, **k: FakeResponse(400, {"error": {"message": "Invalid OAuth token", "code": 190}}),
    )

    with pytest.raises(RuntimeError) as raised:
        facebook.connect("123456789", "bad-token")
    assert "invalid or expired" in str(raised.value)
    assert tokens.get("facebook") == {}


def test_facebook_connect_stores_the_page_name(monkeypatch):
    monkeypatch.setattr(
        facebook.requests, "get", lambda *a, **k: FakeResponse(200, {"name": "Naija Hustle"})
    )

    record = facebook.connect("123456789", "good-token")
    assert record["page_name"] == "Naija Hustle"
    ok, reason = facebook.ready()
    assert ok, reason


def test_facebook_rejects_a_page_id_that_is_not_digits():
    with pytest.raises(RuntimeError) as raised:
        facebook.connect("my-page", "token")
    assert "all digits" in str(raised.value)


def test_direct_publisher_skips_an_unconnected_platform():
    class Captions(dict):
        pass

    settings = type(
        "S",
        (),
        {"delivery": "direct", "youtube_privacy_status": "public", "youtube_made_for_kids": False},
    )()

    results = direct.DirectPublisher().publish(
        "missing.mp4", Captions(), [Platform.tiktok], settings
    )
    assert len(results) == 1
    assert results[0].success is False
    assert "does not cover this platform" in results[0].detail


def test_delivery_accepts_direct():
    from app.bot.config import _as_delivery

    assert _as_delivery("direct") == "direct"
    assert _as_delivery("free") == "direct"
    assert _as_delivery("nonsense") == "upload_post"
    assert _as_delivery("manual") == "manual"


def test_google_credentials_land_in_the_direct_section(tmp_path, monkeypatch):
    """youtube_client_id 写进 [bot] 就永远读不到——它属于 [direct]。"""
    from app.bot import cli
    from app.config import config as runtime_config

    target = tmp_path / "config.toml"
    target.write_text('[app]\n\n[bot]\n\n[direct]\nyoutube_client_id = ""\n')
    monkeypatch.setattr(cli, "_config_path", lambda: target)
    for section in ("app", "bot", "direct"):
        monkeypatch.setattr(runtime_config, section, {}, raising=False)

    cli.set_config_values(["youtube_client_id=abc.apps.googleusercontent.com"])

    text = target.read_text()
    direct_part = text.split("[direct]")[1]
    assert 'youtube_client_id = "abc.apps.googleusercontent.com"' in direct_part
