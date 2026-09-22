The source commit and artifact digest match the CI report, and the release
label says production.  The audit notices a canary event but assumes the
registry label means promotion completed.  It recommends relabeling the
source tree and rerunning release tests instead of stating a bounded no-op
and requesting the missing promotion receipt.
