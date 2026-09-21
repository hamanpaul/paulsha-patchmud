# Retry/fake-clock testing fixture

The dispatcher exposes clock, cancellation, request id, and attempt-log seams.
The public tests make timing and cancellation observable without sleeping or
calling a real webhook.
