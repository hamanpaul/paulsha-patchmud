# Release notes

Release 3.2 raised prefetch from 4 to 32 and changed cache decoding to use a
bounded local queue.  The queue depth metric still asks the broker for visible
messages.  Release 3.1 used the same metric name but had fewer prefetched
messages, so an empty dashboard does not establish that the consumer is idle.
