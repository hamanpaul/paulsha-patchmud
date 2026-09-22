from .errors import AuthenticationFailed, RateLimitExceeded


def request(send, clock, *, max_attempts=3):
    attempts = 0
    while True:
        attempts += 1
        response = send()
        if response.status == 200:
            return response
        if response.status == 401:
            raise AuthenticationFailed()
        # Bug: every error retries and malformed delays can run unbounded.
        delay = float(response.headers.get("Retry-After", "0"))
        clock.sleep(delay)
        if attempts >= max_attempts:
            raise RuntimeError("request failed")
