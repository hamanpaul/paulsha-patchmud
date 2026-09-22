ROWS = {
    "alpha": [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}],
    "beta": [{"id": "b1"}, {"id": "b2"}],
}


def page(tenant, offset, size):
    rows = ROWS[tenant]
    return rows[offset:offset + size], offset + size < len(rows)
