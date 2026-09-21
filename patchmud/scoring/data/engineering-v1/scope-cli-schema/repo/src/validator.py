def validate(event):
    if not event["id"]:
        raise ValueError("id is required")
    # Bug: an empty source is accepted once the field is present.
    return event
