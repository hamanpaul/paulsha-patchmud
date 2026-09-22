---
status: accepted
work_item: jev-model-scoring
task_type: feature
---

# JEV model scoring implementation plan

User-approved scope: new `paulsha-patchmud --target --harness codex --model gpt-5.6-luna --effort max` and optional independent `--base --harness agy --model gemini-3.8-flash --effort high`. Controlled PatchMUD tools, all final quality scores decided by JEV, 18 cases once, quality separate from costs. Prefer compatible historical base. Preserve old commands and frozen pilot-v1.

## Accepted design

- New `engineering-v1`: six categories (repair, diagnosis, scope, testing, recovery, audit), each with depths multi-constraint, cross-component, evolving-state. Limits respectively 8/16/24 author turns and 600/1200/1800 seconds. Each case has real public materials, rubric, and private reference/partial/wrong anchors. Cases include a no-change correct answer and insufficient-evidence answer. No private sessions as sources.
- Each case has four explicit 0..4 JEV Score questions: fulfillment, evidence, constraints, verification. Equal weights; case score=25*mean(questions), category=mean(three cases), total=mean(six categories). Confidence is reported, not a score multiplier. All objective outcomes are evidence, never additional arithmetic gates. Service/credential/isolation errors produce no score; budget exhaustion is a judgeable model outcome. Incomplete coverage has no full total.
- Pinned JEV `jev-1.13.0`, stdlib HTTP, typed response validation, bounded retry. Model/role/history blinded. Report input evidence references, not invented JEV prose. No runtime imports from session-health/Cortex/Hippo.
- Reuse Workspace/materialize_repo and IsolationRunner. CLI adapters return controlled actions; no native tools or ambient instructions. New runner supports final multiline reports and deterministic staged evidence. Persist full public transcript/tool results/diff. Hidden assets never go to model workspace, prompts or public report.
- Case loader uses packaged resources, independent of cwd. Public and private hashes contribute suite fingerprint. Public snapshot excludes hidden. Each case owns repository fixture. Candidate tests execute only through IsolationRunner. Objective test statuses remain passed/failed/error.
- Output root `~/.config/paulsha-patchmud`; immutable JSON run archives and public snapshots in `runs/<run-id>/`, locked atomic `models-score.md`. Complete history; per-case requirements/outcomes/scores/probabilities/confidence/evidence. Model/JEV time/native usage/cost provenance separate; Decimal for money. Never treat unknown as zero.
- Base cache matches complete suite hash/rubric/judge/protocol/engine content/harness version/model+effort/budget/repetitions. Date disclosed; unknown actual model snapshot remains unknown. `--refresh-base`, `--repeat N`, `--suite`, `--output-dir` allow explicit control. Internal `--case` selection marks partial/pilot and cannot populate full-suite cache.
- Pilot: repair multi, diagnosis cross, scope evolving, testing multi, recovery cross, audit evolving; two settings; inspect saturation/floor/judge mistakes before freeze. Freeze 18 and run full two profiles. Do not rig separation. Missing live credential is an explicit unmet live gate, not passing evidence.

## Worker interface contract (root owns this document)

Use plain JSON-compatible dictionaries as artifacts, type hints as appropriate. Numeric scores JSON numbers; monetary values decimal strings or null. No cross-worker edits without root coordination.

`cases.py`: `load_suite(name='engineering-v1') -> dict` with keys `id`, `version`, `suite_hash`, `rubric_version`, `cases` (list); `load_case(case_id, suite='engineering-v1') -> dict`; `public_case_snapshot(case: dict) -> dict`. Cases include `id`, `category` (six names above), `depth` (1..3), `title`, `prompt`, `requirements` (list[str]), `allowed_paths` (list[str]), `max_turns`, `wall_seconds`, `fixture_dir` (absolute str containing repo/ and hidden/), `rubric` (mapping four dimension IDs to {instructions, criteria:[5 strings]}), `stages` (list[{after_turn:int, message:str}]), `test_argv` (list[str], default python3 -m pytest -q), `case_hash`, and private `anchors` metadata. Public snapshot drops fixture_dir/private anchors/hidden content but contains public repo file contents. Package resources under `patchmud/scoring/data/engineering-v1/`.

`runner.py`: `resolve_profile(harness, model, effort) -> dict` (requested/resolved/observed identities, harness version, execution mode, protocol version); `build_scoring_adapter(profile, *, timeout_s=600) -> ModelAdapter`; `execute_case(case, adapter, *, runner_factory=None, clock=time.monotonic, artifact_dir=None) -> dict` returns `case_id`, `status` (completed/budget_exhausted/protocol_failed/error), `end_reason`, `error` (null or sanitized message), `turns`, `wall_ms`, `transcript` (list of messages), `events` (tool outcomes with evidence IDs), `final_report`, `final_diff`, `test_results`, `usage` (raw per-call provider data). Use injectable factories for tests, never live model/API in unit tests. All non-error terminal statuses judgeable. No JEV or model identity in execution output supplied to judge.

`judge.py`: `JevJudge(model='jev-1.13.0', *, transport=None, timeout_s=30, max_attempts=3)`; `.evaluate(case, execution) -> dict` returns `status` (scored/error), `model`, `dimensions` (4 native responses), `score` (0..100 or null), `usage`, `wall_ms`, `error`, `evidence_refs`, `request_hash`. Public artifacts must not contain anchors/hidden. Expose `build_judge_request(case, execution, model=...)` for offline anchor testing. Auth from TYPESAFE_API_KEY only; fake transport tests work without auth.

`store.py`: `ScoreStore(root: Path)`; `.save_run(record: dict) -> Path` immutable run.json with content digest and `.find_baseline(fingerprint: str) -> dict|None`; `.render_report() -> Path` atomic locked Markdown over validated archived runs. Root supplies `record`: schema_version=1, run_id, created_at UTC ISO, role base/target, profile, fingerprint, suite {id,version,suite_hash,rubric_version,case_ids}, judge_model, repeat, status complete/partial/error, cases list [{case:public_snapshot, execution, judgment, repetition}], summary from aggregate, comparison(optional {base_run_id, reused, ...}). `.save_run` may emit per-case files but run.json is source of truth. `.find_baseline` only complete records with verified digest/full coverage; root validates expected identity. `.render_report` includes immutable histories and comparison. Do not expose absolute fixture paths or private data.

`aggregation.py`: `aggregate_results(case_results, expected_cases, repeat=1) -> dict` with `total` nullable, `categories`, `coverage` {expected,scored}, `complete`; expected_cases list of case dicts. Invalid/duplicate/missing case+repetition must not manufacture completeness. `compare_results(target, base) -> dict` for per-case/category/total deltas only like-for-like, includes IDs/reuse/date provided by root.

## Ownership and acceptance

- Luna judge/store worker: judge.py/store.py/aggregation.py and tests/scoring/test_judge.py,test_store.py,test_aggregation.py.
- Luna runner worker: runner.py, additive adapter changes if necessary, tests/scoring/test_runner.py and test_profiles.py. Do not edit legacy cli.py/protocol.py without coordination; new controlled parser can be local.
- Luna cases worker: cases.py, all data/engineering-v1 assets, tests/scoring/test_cases.py and fixture quality checks. Must deliver all 18 meaningful cases and executable evidence, not metadata shells.
- Root: CLI orchestration, pyproject packaging, docs/policy/OpenSpec, integration tests, live pilot/full runs, reviews, PR.
- Every worker first writes/executes meaningful RED, then implements/executes tests, reports exact results and unresolved limitations; no commits/push/PR by workers. Stop after bounded owned task and report changed paths.

## Completion gates

Full pytest and policy-preflight; installed wheel from outside checkout; namespace readiness; no hidden leakage; blinded scoring; budget/cancellation; base cache invalidation; report concurrent persistence; honest missing metadata. Root source/test cross-check and independent final review. Update canonical CLAUDE.md/README/CHANGELOG, preserve symlinks. Open PR after local gates; verify remote checks. User explicitly authorizes push/PR but not merge. Live results and pilot/freeze evidence remain necessary; report any true external blocker separately.
