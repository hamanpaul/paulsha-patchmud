from .auth import can_read
from .cursor import decode, encode
from .query import page


def export(principal, tenant, token=None, size=2):
    if not can_read(principal, tenant):
        raise PermissionError("forbidden")
    offset = decode(token) if token else 0
    rows, more = page(tenant, offset, size)
    return {"records": rows, "next": encode(offset + len(rows), tenant) if more else None}
