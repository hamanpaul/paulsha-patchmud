def parse_event(raw):
    return {"id": raw["id"], "payload": dict(raw["payload"]), "source": raw.get("source")}
