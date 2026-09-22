from src.cancel import Cancellation
from src.dispatcher import dispatch


class FakeClock:
    def __init__(self):
        self.events = []

    def sleep(self, seconds):
        self.events.append(seconds)


def test_exact_backoff_and_success_has_no_extra_sleep():
    clock = FakeClock()
    assert dispatch(lambda request_id: "ok", clock, Cancellation(), "r1") == "ok"
    assert clock.events == []

    clock = FakeClock()
    results = iter(["transient", "transient", "ok"])
    assert dispatch(lambda request_id: next(results), clock, Cancellation(), "r2") == "ok"
    assert clock.events == [0.5, 1.0]


def test_cancellation_between_attempts_preserves_id_and_log():
    clock = FakeClock()
    cancellation = Cancellation()
    log = []

    def send(request_id):
        cancellation.cancelled = True
        return "transient"

    assert dispatch(send, clock, cancellation, "r3", log=log) == "cancelled"
    assert clock.events == []
    assert log == [{"request_id": "r3", "attempt": 1, "status": "transient"}]


def test_permanent_error_does_not_retry():
    clock = FakeClock()
    assert dispatch(lambda request_id: "permanent", clock, Cancellation(), "r4") == "permanent"
    assert clock.events == []
