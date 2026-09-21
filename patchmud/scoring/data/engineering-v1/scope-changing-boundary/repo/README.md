# Evolving export scope fixture

The handler owns export pagination over tenant-authorized query results.  The
later requirements make cursors resumable and tenant-bound while keeping old
tokens valid during a compatibility window.
