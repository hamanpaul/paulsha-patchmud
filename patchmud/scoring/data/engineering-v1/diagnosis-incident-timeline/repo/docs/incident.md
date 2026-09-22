# Incident evidence

At the initial snapshot, a fast path regression and a gateway retry loop are
both plausible.  The gateway and application events agree on request x1, but
the downstream trace is sampled only for successful requests.  The missing
span is therefore a coverage limitation.  The run may gain more timestamps,
but it must not treat the absent span as proof that payments was never called.
