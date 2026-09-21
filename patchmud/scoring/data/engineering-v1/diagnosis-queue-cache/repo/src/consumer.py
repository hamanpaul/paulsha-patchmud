class Consumer:
    def __init__(self, broker, cache, metrics, prefetch=4):
        self.broker = broker
        self.cache = cache
        self.metrics = metrics
        self.prefetch = prefetch

    def poll(self):
        messages = self.broker.fetch(self.prefetch)
        for message in messages:
            job = self.cache.decode(message)
            self.metrics.observe("queue_depth", self.broker.visible_count())
            self.metrics.observe("consumer_lag_ms", job.received_at - job.started_at)
            self.handle(job)

    def handle(self, job):
        job.run()
