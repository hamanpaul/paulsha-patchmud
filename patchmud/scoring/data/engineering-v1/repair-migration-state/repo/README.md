# Resumable migration fixture

Records migrate in batches and a journal cursor controls restart behavior.  An
incident can interrupt a worker after writing records but before checkpointing;
the final dry-run requirement adds a second non-mutating execution mode.
