import json

from xdld.cookies import CookieError, load_cookies, missing_auth, parse_cookie_text


def test_parse_flat_json():
    assert parse_cookie_text('{"auth_token": "a", "ct0": "b"}') == {
        "auth_token": "a",
        "ct0": "b",
    }


def test_parse_browser_export_list():
    payload = json.dumps(
        [
            {"name": "auth_token", "value": "a"},
            {"name": "ct0", "value": "b"},
            {"name": "guest_id", "value": "c"},
        ]
    )
    assert parse_cookie_text(payload)["auth_token"] == "a"


def test_parse_netscape_cookies_txt():
    text = "\n".join(
        [
            "# Netscape HTTP Cookie File",
            ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\ta",
            ".x.com\tTRUE\t/\tTRUE\t0\tct0\tb",
        ]
    )
    assert parse_cookie_text(text) == {"auth_token": "a", "ct0": "b"}


def test_parse_raw_string():
    assert parse_cookie_text("auth_token=a; ct0=b") == {"auth_token": "a", "ct0": "b"}


def test_load_missing_file(tmp_path):
    try:
        load_cookies(tmp_path / "nope.json")
    except CookieError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected CookieError")


def test_missing_auth():
    assert missing_auth({"auth_token": "a"}) == ["ct0"]
    assert missing_auth({"auth_token": "a", "ct0": "b"}) == []
