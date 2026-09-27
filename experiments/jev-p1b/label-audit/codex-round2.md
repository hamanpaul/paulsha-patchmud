[
  {"id":"h-nc-ci-run-all-passed","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-ci-run-one-failed","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-owner-mismatch","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-pitr-disabled","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-changelog-known-issue","your_verdict":"insufficient","gold":"not_satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"high","issue":"The criterion ends at 'lists issue' without identifying an issue or section. The clause adds '#91 under Fixed,' which the criterion does not say. Read as 'lists an issue,' the evidence could even support satisfaction. The rationale is also truncated."},
  {"id":"h-nc-restore-never-observed","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"hv-nc-ci-passed-old-failure","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"hv-nc-ci-failure-muted","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"hv-nc-drill-claimed-passed","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""}
]

Recommend EXCLUDING:

- `h-nc-changelog-known-issue` — The incomplete criterion cannot support a stable verdict, and its clause introduces requirements absent from the criterion.
