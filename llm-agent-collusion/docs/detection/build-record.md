# Offline build record

Date: 2026-10-25. Starting commit: `e6fb1308dae9cf97ae42aec1c5f7889d9e2291e8`.

Implemented the importer/canonical schemas, three allowlisted evidence views, all nine core conditions, two optional compute controls, real assistant–judge messaging state machine, grounded excerpt validation, provider interfaces/fake clients, OpenRouter/Nebius live adapters, cache/retry/concurrency/budget controls, private annotations/adjudication, grouped splits, development-only calibration, frozen manifests, paired bootstrap metrics, Markdown/JSON/plots, generation through the existing simulator, private Experiment 0 summaries and an optional Modal CPU runner.

## Actual validation

- `uv sync --extra dev`: installed only this project's existing dependencies and shared local compute package.
- `.venv/bin/python -m pytest tests -q`: **65 passed**, including the starter market snapshot regression; four existing Pydantic class-config deprecation warnings.
- `.venv/bin/python -m src.collusion_detection doctor --config configs/detection/offline.json --acceptance detection_outputs/offline-acceptance.json`: **64 detection tests passed**, with outbound sockets blocked and a credential-free test subprocess environment. Receipt is bound to relevant source/tests/prompts/lock/config hashes.
- Ruff checks passed on all new package/tests and the Modal runner. Modal runner compilation passed; no Modal function was invoked.
- Complete offline demo: **54 synthetic fixture predictions plus nine predictions on a real bundled episode, zero protocol errors**. LINKED/INTERACTIVE reuse the same source-validated initial report. Repeated demo invocation resumed cached calls and regenerated reports.
- Imported all **75** real bundled source logs: **55** main-study-eligible episodes; **20** excluded oversight-intervention runs. All imported files remain unlabeled.
- The selected real demo log is the first sorted eligible seller-communication-enabled, oversight-disabled source, chosen without inspecting performance. It has 30 rounds, 521 canonical events and 150 seller messages.
- Actual starter market exercised through injected fake clients on both generation arms, 10 rounds each, three buyers and three sellers: **120 mocked calls**. Verified next-round delivery, private objectives reaching only the two designated planted sellers, identical arm market settings and no private objectives in exported market files.
- Live-smoke and generation plans were expanded offline with **zero completion requests**. Illustrative one-episode RAW smoke maximum was $0.02464 under the configured prices, with a $2 ceiling; this is not a quote or bill. The two-arm 12-round generation bound was $3.52512 and therefore would exceed that smoke ceiling: the live gate correctly requires a larger deliberately configured allocation or a revised plan. No live generation was attempted.
- Inspected the generated plot and reports. Reports/figures prominently identify mocked/synthetic outputs. The real-source report explicitly has no labeled benchmark accuracy.

## Required acceptance coverage

| Requirement | Checks |
|---|---|
| Real four-backtick log | test_pipeline: importer parses actual bundled logs and all observed fields |
| View leakage | M/MAO/A allowlist, static context equality, private metadata absent |
| Next-round semantics | canonical available_round plus real Market generation delivery |
| IDs/quotes/access | invalid IDs, forbidden views, invalid spans, invented quotes and judge-unseen citations fail |
| Actual interaction | separate report, decision, answer and final calls through real Engine |
| Turns/isolation | two-question hard cap, early finish, zero allowance, reset across episodes/replicates |
| Shared initial report | LINKED/INTERACTIVE hash equality, one actual report request and cached attribution |
| Grouped split | same related groups/branches stay together, order-independent splitting |
| Calibration leakage | held-out predictions rejected, held-out labels excluded from selection/hash |
| Failures | API/JSON/schema/grounding failures retain error status and no assessment |
| Provider controls | bounded retries/Retry-After, process-wide concurrency <=3, coalesced/persistent cache, reservations/resume, unknown billing |
| Known metrics | exact confusion/AUROC, tie handling, episode replicate means, paired intersection/group bootstrap |
| Offline fence | blocked sockets, no real provider construction in offline CLI/dry-run, no credential env reads in fake adapter |
| Frozen research choices | source/prompt/model/study/split/threshold checks reject mixed or retrospectively altered evaluations |

## What is mocked or untested

All assistant/judge inference is mocked. Fake scores are constant 0.5 with explicit insufficient-evidence wording and establish no detector capability. Fixture expectations are written by the software author, not fabricated human annotations of real episodes. Token/cost/latency values from fake calls are synthetic metadata; they do not estimate hosted runtime.

Real hosted transport is tested with injected HTTP responses and scripted errors only. Current model IDs, context limits/capabilities and prices still require live preflight. One real OpenRouter environment key can serve all roles; none was accessed during this build. Modal image build, Secrets, Volume and remote execution remain untested in deployment. No resources were launched.

Real annotations/adjudication and a reviewed related-episode group map are still required before a meaningful real held-out benchmark. Default imported grouping deliberately keeps the source root together until reviewed. Small pilots remain exploratory; no calibration or communication improvement has been established.

Client spending reservations are conservative estimates, not provider-enforced billing caps. One orchestrator per credential is required for the process-local shared gate; independent processes/hosts/output ledgers do not share a distributed spending service. Compute-control examples expose actual usage but are not automatically token/cost matched.

## Reproduction and future commands

All exact setup, demo, independent annotation, calibration/evaluation, $2 live-smoke, $25 pilot, fresh generation and Modal commands are in [README.md](README.md). Live commands are documented for later use and were **not executed**.

## Post-build live smoke troubleshooting

A user explicitly ran the first hosted smoke. Authentication/catalog validation succeeded; OpenRouter returned the requested `openai/gpt-4.1-nano` from Azure. The completion reached its 1600-token limit (`finish_reason: length`), echoed schema wrappers, and emitted an excessively long citation list. No valid assessment was accepted. Recorded billed usage: 29583 input tokens, 1600 output tokens, $0.0035983. This is an observed technical failure, not a detection result.

The subsequent repair adds v2 prompts (v1 preserved), native strict JSON Schema and capability-aware routing, an increased bounded output ceiling, native-schema reservation accounting, explicit truncation reporting, and nonzero CLI status with error details. Regression tests replay the truncated response through the real adapter/engine using injected HTTP; no additional real provider call or credential read was made during the repair. Use the v2 retry command in the README with regenerated acceptance and plan files.
