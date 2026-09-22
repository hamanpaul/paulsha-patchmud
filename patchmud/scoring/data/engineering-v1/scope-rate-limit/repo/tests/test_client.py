import pytest

from src.client import request
from src.errors import AuthenticationFailed, RateLimitExceeded


class Response:
    def __init__(self, status, retry_after="0"):
        self.status = status
        self.headers = {"Retry-After": retry_after}


class FakeClock:
    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def test_only_429_retries_and_honors_delay():
    responses = iter([Response(429, "1.5"), Response(200)])
    clock = FakeClock()
    assert request(lambda: next(responses), clock).status == 200
    assert clock.sleeps == [1.5]


def test_exhaustion_is_typed_and_malformed_delay_is_bounded():
    clock = FakeClock()
    with pytest.raises(RateLimitExceeded):
        request(lambda: Response(429, "nope"), clock, max_attempts=2)
    assert len(clock.sleeps) == 2
    with pytest.raises(AuthenticationFailed):
        request(lambda: Response(401), FakeClock())
