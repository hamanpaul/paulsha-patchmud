def normalize(event, state):
    normalized = dict(event)
    normalized["version"] = 2
    normalized["tenant"] = event["tenant"]
    # Bug: an older replay can overwrite a newer event id.
    state[event["id"]] = normalized
    return normalized
