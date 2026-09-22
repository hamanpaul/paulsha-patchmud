DEFAULT_ATTEMPTS = 3


def should_retry(attempt, error):
    return error in {"timeout", "connection_reset"} and attempt < DEFAULT_ATTEMPTS


def delivery_confirmed(response):
    return response.status == 202 and response.headers.get("X-Delivery-Id") is not None
