# Configuration reload fixture

The service has a file layer, an environment layer, and a derived endpoint
cache.  A reload is expected to validate the whole candidate before replacing
the last known-good state.  The public tests exercise explicit false/zero,
range errors, and rollback together.
