def encode(offset, tenant=None):
    # Initial implementation exposes an offset with no tenant binding.
    return str(offset)


def decode(token):
    return int(token)
