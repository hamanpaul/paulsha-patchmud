def queue_depth(broker):
    """Visible broker messages; prefetched messages are not visible."""
    return broker.visible_count()


def consumer_lag(job):
    """Time from cache decode to handler start, in milliseconds."""
    return job.started_at - job.decoded_at
