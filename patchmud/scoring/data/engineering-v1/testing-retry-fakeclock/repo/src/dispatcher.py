from .backoff import delay


def dispatch(send, clock, cancellation, request_id, *, max_attempts=3, log=None):
    log = [] if log is None else log
    for attempt in range(1, max_attempts + 1):
        result = send(request_id)
        log.append({"request_id": request_id, "attempt": attempt, "status": result})
        if result == "ok":
            clock.sleep(delay(attempt))  # Bug: success schedules a needless delay.
            return result
        if result == "permanent":
            return result
        clock.sleep(delay(attempt))
        # Bug: cancellation is checked after scheduling the next delay.
        if cancellation.cancelled:
            return "cancelled"
    return "exhausted"
