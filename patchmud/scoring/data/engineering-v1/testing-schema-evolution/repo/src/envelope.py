def parse(raw):
    try:
        version = int(raw["version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("version marker") from exc
    if version not in {1, 2}:
        raise ValueError("version marker")
    return {"id": raw["id"], "version": version, "tenant": raw["tenant"], "payload": dict(raw["payload"]), **raw.get("extra", {})}
