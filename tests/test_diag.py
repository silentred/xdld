from __future__ import annotations

from xdld.diag import describe_exception, hints, response_body, status_code


class TwitterException(Exception):
    """Same shape as ``twikit.errors.TwitterException`` (headers optional)."""

    def __init__(self, *args: object, headers: dict | None = None) -> None:
        super().__init__(*args)
        self.headers = headers


def _exc(message: str, headers: dict | None = None) -> TwitterException:
    return TwitterException(message, headers=headers)


def test_status_code_is_read_from_the_message():
    exc = _exc('status: 422, message: "{"errors":[]}"')
    assert status_code(exc) == 422


def test_status_code_falls_back_to_the_exception_type():
    class NotFound(TwitterException):
        pass

    assert status_code(NotFound("boom")) == 404


def test_response_body_is_unwrapped():
    exc = _exc('status: 403, message: "{"errors":[{"code":9}]}"')
    assert response_body(exc) == '{"errors":[{"code":9}]}'


def test_describe_exception_prints_status_body_and_headers():
    exc = _exc(
        'status: 429, message: "slow down"',
        headers={"x-rate-limit-reset": "1700000000", "cf-ray": "abc", "x-noise": "1"},
    )
    report = describe_exception(exc)
    assert "http status : 429" in report
    assert "slow down" in report
    assert "x-rate-limit-reset: 1700000000" in report
    assert "cf-ray: abc" in report
    assert "x-noise" not in report  # only the interesting headers


def test_describe_exception_keeps_the_cause():
    try:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            raise TwitterException("outer") from exc
    except TwitterException as exc:
        report = describe_exception(exc)
    assert "caused by   : ValueError: boom" in report


def test_hints_explain_the_common_status_codes():
    assert "403" in hints(_exc("status: 403, message: \"x\""))[0]
    assert "404" in hints(_exc("status: 404, message: \"x\""))[0]
    assert "422" in hints(_exc("status: 422, message: \"x\""))[0]
    assert "429" in hints(_exc("status: 429, message: \"x\""))[0]


def test_hints_call_out_a_null_user_id():
    exc = _exc(
        'status: 422, message: "{"errors":[{"code":"GRAPHQL_VALIDATION_FAILED",'
        '"message":"cannot be null","path":["variable","userId"]}]}"'
    )
    text = " ".join(hints(exc))
    assert "userId" in text
