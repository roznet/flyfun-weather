"""Tests for api/client_info: iOS vs web classification from the User-Agent."""

from __future__ import annotations

import pytest

from weatherbrief.api.client_info import USER_AGENT_MAX_LEN, classify_user_agent


@pytest.mark.parametrize("agent, expected", [
    # iOS app: URLSession's default agent.
    ("flyfun-weather/15 CFNetwork/3826.500.131 Darwin/25.0.0", "ios"),
    # Safari on an iPhone is a web user, not an app user.
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1", "web"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36", "web"),
    ("python-httpx/0.28.1", "other"),
    ("curl/8.7.1", "other"),
    ("", None),
    (None, None),
])
def test_classify_user_agent(agent, expected):
    assert classify_user_agent(agent) == expected


def test_request_client_truncates_agent():
    from starlette.requests import Request

    long_agent = "Mozilla/5.0 " + "x" * 1000
    request = Request({
        "type": "http",
        "headers": [(b"user-agent", long_agent.encode())],
    })
    from weatherbrief.api.client_info import request_client

    client, agent = request_client(request)
    assert client == "web"
    assert len(agent) == USER_AGENT_MAX_LEN
