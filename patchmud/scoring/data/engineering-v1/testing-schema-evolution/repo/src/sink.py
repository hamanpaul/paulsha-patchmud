def accept(event, tenant):
    if event["tenant"] != tenant:
        raise ValueError("tenant mismatch")
    return {"id": event["id"], "version": event["version"], "payload": event["payload"]}
