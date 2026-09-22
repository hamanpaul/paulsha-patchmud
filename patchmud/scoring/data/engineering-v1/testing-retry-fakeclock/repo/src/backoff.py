def delay(attempt):
    return 0.5 * (2 ** (attempt - 1))
