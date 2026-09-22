import json


def text(event):
    return f"{event['id']}: {event['payload']}"


def as_json(event):
    # Bug: key order follows incidental construction and can include a prefix.
    return json.dumps(event, sort_keys=True)
