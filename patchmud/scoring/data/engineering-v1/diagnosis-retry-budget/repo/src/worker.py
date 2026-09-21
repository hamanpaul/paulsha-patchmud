from .retry import delivery_confirmed, should_retry


def deliver(client, message, log):
    attempts = 0
    while True:
        attempts += 1
        response = client.send(message)
        log.append({"attempt": attempts, "status": response.status, "id": response.headers.get("X-Delivery-Id")})
        if delivery_confirmed(response):
            return attempts
        if not should_retry(attempts, response.error):
            raise RuntimeError("delivery failed")
