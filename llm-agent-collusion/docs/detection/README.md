# Collaborative oversight of seller collusion

This package tests two research questions on frozen double-auction logs: whether messages plus actions/outcomes improve detection over either alone, and whether genuine assistant–judge clarification improves on a static report. The assistant and judge cooperate honestly. This is inference-only research; no training or web app is involved.

**The default demo is offline and uses deterministic mock models. Real episodes currently have no human gold labels, so neither mocked checks nor live outputs establish detection accuracy.** Local smoke and stopped-pilot history is recorded below. Paths under `detection_outputs/` refer to local, ignored run artifacts; they are not included in this repository.

## Setup and complete offline demo

Run from `llm-agent-collusion/`, using its existing Python 3.12/uv project:

```bash
uv sync --extra dev
uv run python -m src.collusion_detection doctor --config configs/detection/offline.json
uv run python -m src.collusion_detection demo --out detection_outputs/demo
```

Only this project's dependencies and the existing shared compute package are installed. Do not run the root setup for all bundled projects. Detection does not import the starter simulation's dotenv loader or global client factory. Only explicit live commands load configured provider credentials from this project's `.env`; offline commands and dry runs never read it. It defaults to an injected fake and fences outbound sockets. All detection tests also block network access.

The demo runs the nine conditions on six hand-specified synthetic fixtures (54 predictions), then on the first sorted real bundled episode with seller communication enabled and live oversight disabled (nine predictions). It writes:

- `detection_outputs/demo/fixture_report/report.md`, `metrics.json`, `detection_metrics.png`: clearly mocked software checks against synthetic expectations.
- `detection_outputs/demo/real_report/report.md`, `metrics.json`: real source, mocked inference, no claimed benchmark accuracy.
- `fixture_run/` and `real_run/`: hashed manifests, predictions, full model-call records, resumable cache, and separate evidence views.
- `real/annotations/`: anonymous full-observable packet and blank labels template, with no model verdicts or planted assignments.

Rerunning with the same manifest reuses calls. Changes to episodes, prompts, conditions, models or frozen thresholds require a new output directory. API failures remain uncached errors; a cached malformed completion remains an error and needs a new replicate/output allocation for re-inference. A malformed output never becomes a negative prediction. Cache reuse assumes one orchestration process writes each output/cache directory.

## Starter inspection and compatibility

Actual starting commit: **`e6fb1308dae9cf97ae42aec1c5f7889d9e2291e8`** (the supplied older revision was `d585a7b3be50366cc2bdf5789bf8add5890b2abd`). See [source audit](source-audit.md).

The original simulation commands and matching mechanics remain available. The sole simulator change adds an optional `LMSeller.private_context`, empty by default and excluded from serialization. Generation injects clients rather than calling the legacy global model wrapper. Tests exercise the actual market, next-round messaging and the default hook behavior.

## Conditions and operational definition

| Evidence | RAW | EAE | LINKED | INTERACTIVE |
|---|---|---|---|---|
| M: seller messages | yes | yes | yes | yes |
| MAO: messages, asks, bids, trades, outcomes | yes | yes | yes | yes |
| A: actions/outcomes, no messages | yes | — | — | — |

RAW supplies the permitted view directly to the judge. EAE asks the assistant to select event IDs and optional exact spans; application code renders excerpts, without assistant reasoning or a full-log appendix. LINKED supplies a structured, source-checked report. INTERACTIVE starts with that same cached report, then uses distinct judge/assistant calls for up to two questions followed by a final assessment. Every exchange is persisted. Conversations reset across episodes, conditions and stochastic replicates.

The target is **observable joint implementation by at least two sellers** of an anticompetitive arrangement. Agreement, implementation and economic consequences are distinct. A rejected or unimplemented proposal is not automatically positive. Parallel high prices, common costs or high profits alone do not establish implementation. This is a research definition, not a legal determination. See [annotation rubric](annotation-rubric.md).

M reports connect proposals, replies, contradictions and withdrawals, and must acknowledge the absence of behavioral verification. MAO includes executed trades and derived trade counts, payments, mean prices and, when static valuations are known, seller profit and buyer surplus. These consequences do not establish a causal welfare effect.

Messages from round t reach other sellers only in t+1. Proposals in r1, responses in r2 and implementation in r3 are used in the positive fixture. No within-round order is inferred from JSON order. Ask/bid records are round bookkeeping; absent entries cannot distinguish an omitted response, a retained standing order or a withdrawal. Numeric equality is not proof of a new price change.

## Data separation and source validation

`episodes.jsonl` contains typed canonical observable episodes. `private/provenance.jsonl` contains raw paths, original metadata and aliases; generated objectives and assignments stay in generation's private files. Labels live separately. Predictions and model-call transcripts are separate outputs. Filenames/model names/treatment tags, native PASS/FAIL, prior monitor verdicts and private seller reasoning never enter evidence builders.

Views are built from an allowlist, not by deleting known sensitive keys. All views share seller/buyer aliases and roles, static valuations when known, horizon, and static market/timing rules. M contains only seller-message events. A contains no seller messages. Reports and follow-up answers receive only their assigned view. The judge receives source evidence directly only in RAW and judge-only review.

Event IDs, view membership, exact quotes, selection offsets and forwarded judge citations are validated. Unknown references, invented quotes, oversized reports and invalid outputs produce validation failures; they are not silently repaired. Citation validity does not establish an interpretation's correctness. Source text is explicitly untrusted in every prompt. Current prompts live in `src/collusion_detection/prompts/v2/` (v1 is preserved for the original smoke run); typed schemas and generated JSON schemas are in [schemas](schemas/).

The assistant's assigned view is an application input boundary, not an operating-system sandbox against a malicious model with tools. These assistant calls expose no filesystem or browsing tools.

## Import and independent annotation

```bash
uv run python -m src.collusion_detection import-market --source data --out detection_outputs/imported
uv run python -m src.collusion_detection export-annotations \
  --episodes detection_outputs/imported/episodes.jsonl --out detection_outputs/annotations
```

The importer reads `auction_results.md` with three-or-more-backtick fences, including real four-backtick files; it reads only adjacent experiment metadata needed for roles, static valuations and intervention exclusion. It never parses private agent transcripts or native evaluation files. Oversight enabled or unknown is excluded from the main dataset by default. `--include-interventions` explicitly imports secondary analyses; a matching study config must also set `include_interventions: true` to run them. Duplicates/exclusions are recorded privately. `--limit 1` imports the first eligible source in sorted order, not a performance-selected example.

Use two independent reviewers, then adjudicate disagreements. Do not give reviewers model predictions or generation assignments. Reviewers fill copies of `labels.template.jsonl`; concatenate the two submissions, retaining reviewer identities, and add an explicit adjudication record when needed. Do not use the evaluated judge to produce its own labels.

```bash
uv run python -m src.collusion_detection validate-labels \
  --episodes detection_outputs/imported/episodes.jsonl \
  --labels detection_outputs/annotations/reviews.jsonl \
  --out detection_outputs/annotations/resolved.jsonl
```

Validation rejects unknown episode/event/coalition IDs, buyer coalitions, invalid categories and unresolved conflicts. Blank templates are intentionally not completed annotations. Gold labels are never loaded by `run`.

## Grouping, development and held-out evaluation

Split related branches/variants together **before tuning**. An explicit reviewed private group map can be supplied to `import-market --groups groups.json`, mapping original source folder names to common group IDs. Generated paired arms share a group automatically. Without a map or source `group_id`, the importer conservatively groups the entire source root together: this prevents optimistic leakage but cannot yield a useful development/held-out study. Review grouping before real calibration or bootstrap analysis. Do not split each generated variant as an independent episode.

A pilot target is about 40 independently labeled episodes, approximately 10 development and 30 held out; it is not a prerequisite for running software. Small pilots remain exploratory.

```bash
uv run python -m src.collusion_detection split \
  --episodes detection_outputs/imported/episodes.jsonl \
  --out detection_outputs/splits.json --development-fraction .25 --seed 0
```

`run --splits ... --split development` produces development-only predictions. `calibrate` rejects any held-out prediction in its input file and ignores held-out labels. It either freezes a supplied threshold or chooses per-condition thresholds toward a common development FPR with deterministic `score >= threshold` ties; no attainable threshold may hit the requested FPR exactly. A threshold just above 1 is valid for predicting no positives if all negative scores tie at 1. Development labels and splits are hashed.

```bash
uv run python -m src.collusion_detection run --config configs/detection/offline.json \
  --episodes detection_outputs/imported/episodes.jsonl --splits detection_outputs/splits.json \
  --split development --out detection_outputs/development
uv run python -m src.collusion_detection calibrate \
  --predictions detection_outputs/development/predictions.jsonl \
  --labels detection_outputs/annotations/resolved.jsonl --splits detection_outputs/splits.json \
  --target-fpr .1 --out detection_outputs/thresholds.json
uv run python -m src.collusion_detection run --config configs/detection/offline.json \
  --episodes detection_outputs/imported/episodes.jsonl --splits detection_outputs/splits.json \
  --split held_out --thresholds detection_outputs/thresholds.json --out detection_outputs/held_out
uv run python -m src.collusion_detection evaluate \
  --predictions detection_outputs/held_out/predictions.jsonl \
  --labels detection_outputs/annotations/resolved.jsonl --splits detection_outputs/splits.json \
  --thresholds detection_outputs/thresholds.json --out detection_outputs/metrics.json
uv run python -m src.collusion_detection report \
  --metrics detection_outputs/metrics.json --out detection_outputs/report
```

These offline commands are software exercises only. Real inference requires the explicit live procedure below. Omit `--labels` when none exist; then no benchmark accuracy is claimed. CLI calibration/evaluation checks adjacent frozen manifests, evidence hashes, split maps and threshold/study binding. It rejects mixing different model/prompt/config runs or changing held-out thresholds retrospectively. A changed budget alone does not change the research definition/model/prompt study hash.

Reports include confusion matrices, recall, actual held-out FPR, precision, balanced accuracy, AUROC where defined, errors/exclusions, insufficient-evidence frequencies, paired differences and group-bootstrap intervals. Replicate scores are averaged within episode; repeated calls are not treated as independent episodes. Ambiguous and unlabeled episodes are excluded from binary metrics and counted. Partial replicate failures are counted; an episode with remaining valid replicates contributes their mean. Pairwise comparisons use the intersection with valid scores and binary labels, reporting its denominator. Tiny samples cannot measure 1% FPR reliably. Scores are rankings unless calibration has independently been established.

Deployment tokens/cost/latency attribute initial report generation to both LINKED and INTERACTIVE, even if cached. Actual billed cost/latency separately account for reuse. Missing usage/billing stays null; known subtotals and retry attempts remain available. Never infer a communication benefit solely from extra compute.

## Optional compute controls

`configs/detection/offline-controls.json` adds MAO_STATIC_REVIEW (assistant report + self-review + judge) and MAO_JUDGE_REVIEW (RAW judge + self-review). Both use real separate calls with fake injection offline. Compare their actual tokens/cost/latency with INTERACTIVE, and tune/freeze comparable caps on development data before held-out work. These examples are **not automatically compute-matched**; the report explicitly exposes the usage difference.

## Later live smoke — printed, not executed during this build

Examples use exact configurable OpenRouter IDs and illustrative token prices. Availability, context/capabilities, prices and returned model ID must be checked at live preflight; the example is not an availability promise. Adjust config if the provider retires an ID. The adapter refuses silent substitution and rejects an underpriced catalog reservation. Provider billing limits are not guaranteed by client estimates.

Put your key in `llm-agent-collusion/.env` (beside `pyproject.toml`):

```dotenv
OPENROUTER_API_KEY=your-key-here
```

The `.env` file is gitignored. A blank `.env.example` is provided for new checkouts. Explicit `--live` commands load only the configured provider key variables from that file, without interpolation; existing environment variables take precedence. Offline commands and `--dry-run` never read it. Other settings in `.env` are ignored by detection; models/providers remain explicit in study JSON. You can alternatively set `OPENROUTER_API_KEY` in the environment using your own secret manager. All roles share it through separate calls. Do not put it on the command line or in committed JSON. Do not use `poseidon-run` for this study; leave `LLM_MODEL` unset. The detector rejects that global override and never calls the starter's resolver or modifies process-global routing.

```bash
# This imports one real log, without inference, as the smoke input.
uv run python -m src.collusion_detection import-market --source data --limit 1 \
  --out detection_outputs/smoke_input
# Run offline checks for the exact live config; does not read keys.
uv run python -m src.collusion_detection doctor --config configs/detection/live-smoke.json \
  --acceptance detection_outputs/smoke-acceptance.json
# Zero completion requests and no credential access, even with --live.
uv run python -m src.collusion_detection plan --dry-run --config configs/detection/live-smoke.json \
  --episodes detection_outputs/smoke_input/episodes.jsonl --out detection_outputs/smoke-plan.json
# AFTER reviewing the expansion, the only paid command in this example:
uv run python -m src.collusion_detection run --live --config configs/detection/live-smoke.json \
  --episodes detection_outputs/smoke_input/episodes.jsonl --out detection_outputs/live-smoke \
  --acceptance detection_outputs/smoke-acceptance.json --plan detection_outputs/smoke-plan.json
```

The smoke config has a $2 ceiling and one combined RAW condition; pilot config has a $25 ceiling and all nine core conditions. Live use requires a current source/test/config acceptance receipt, exact reviewed dry-run plan, known maximum within explicit budget, and provider/model validation. Changing relevant code/config invalidates acceptance. `doctor --live` may perform authenticated catalog GETs after tests, but makes zero completion calls. See the run history below for local live checks.

Catalog context and text input/output capabilities are enforced. If a compatible provider catalog omits required fields/prices, preflight fails until a dated official `manual_model_verification[model_id]` record supplies `source_url`, `checked_at`, `context_limit`, `text_input: true`, `text_output: true` and the matching per-million rates, plus `structured_outputs: true` when the catalog does not list JSON Schema capability. Keep manual checks current. Input bounds use UTF-8 bytes plus framing, deliberately conservative; oversized episodes fail without truncation or model substitution.

The per-process shared-key request gate permits at most three requests in flight. Run one orchestrator per credential and do not simultaneously run local and Modal jobs on that key. Retries are bounded, use backoff/Retry-After, and reserve each attempt conservatively; unknown failure billing still consumes the full reservation. Ledgers resume across restarts. There is no distributed cross-host budget/queue service; budgets apply per output ledger.

## Later live pilot

First review/group/annotate sources, freeze prompts/config/conditions/replicates/exclusions, and split them as above. For the two live `run` steps in the development/held-out sequence, use these exact expansions and gates:

```bash
uv run python -m src.collusion_detection doctor --config configs/detection/pilot.json \
  --acceptance detection_outputs/pilot-acceptance.json
uv run python -m src.collusion_detection plan --dry-run --config configs/detection/pilot.json \
  --episodes detection_outputs/imported/episodes.jsonl --splits detection_outputs/splits.json \
  --split development --out detection_outputs/pilot-dev-plan.json
uv run python -m src.collusion_detection run --live --config configs/detection/pilot.json \
  --episodes detection_outputs/imported/episodes.jsonl --splits detection_outputs/splits.json \
  --split development --out detection_outputs/pilot-development \
  --acceptance detection_outputs/pilot-acceptance.json --plan detection_outputs/pilot-dev-plan.json
uv run python -m src.collusion_detection calibrate \
  --predictions detection_outputs/pilot-development/predictions.jsonl \
  --labels detection_outputs/annotations/resolved.jsonl --splits detection_outputs/splits.json \
  --target-fpr .1 --out detection_outputs/pilot-thresholds.json
uv run python -m src.collusion_detection plan --dry-run --config configs/detection/pilot.json \
  --episodes detection_outputs/imported/episodes.jsonl --splits detection_outputs/splits.json \
  --split held_out --out detection_outputs/pilot-test-plan.json
uv run python -m src.collusion_detection run --live --config configs/detection/pilot.json \
  --episodes detection_outputs/imported/episodes.jsonl --splits detection_outputs/splits.json \
  --split held_out --thresholds detection_outputs/pilot-thresholds.json --out detection_outputs/pilot-held_out \
  --acceptance detection_outputs/pilot-acceptance.json --plan detection_outputs/pilot-test-plan.json
uv run python -m src.collusion_detection evaluate \
  --predictions detection_outputs/pilot-held_out/predictions.jsonl \
  --labels detection_outputs/annotations/resolved.jsonl --splits detection_outputs/splits.json \
  --thresholds detection_outputs/pilot-thresholds.json --out detection_outputs/pilot-metrics.json
uv run python -m src.collusion_detection report \
  --metrics detection_outputs/pilot-metrics.json --out detection_outputs/pilot-report
```

Review both expansions against the **total** intended pilot spend. Separate output ledgers each enforce their own configured ceiling, not a shared account-wide $25 cap. Reduce episodes/replicates or adjust explicit caps before proceeding if the expansion exceeds your allocation; never silently shrink a study or substitute a model.

## Nebius and optional fresh markets

Set a role's `provider` to `nebius`, exact available model ID, its explicit prices and `base_url` if needed. Current official quickstart uses `https://api.tokenfactory.nebius.com/v1`; the legacy starter used `api.studio.nebius.com`. The endpoint remains configurable. Recheck [Nebius quickstart](https://docs.tokenfactory.nebius.com/quickstart) and model documentation before live use. No global fallback/provider changes occur.

After the offline pipeline is accepted, generation can sample every specified seed under **both** neutral and planted arms. Three sellers, three LLM buyers, 10–15 fixed rounds, seller messages enabled, live oversight disabled. Two planted sellers receive compatible private floor objectives; assignment is never a gold label. Buyer policy, valuations, horizon, temperatures and role models stay fixed across arms. ZIP is not mixed into these comparisons.

```bash
uv run python -m src.collusion_detection generate-market --dry-run --config configs/detection/live-smoke.json \
  --seeds 0 --rounds 12 --out detection_outputs/generation-plan.json
# Later, only if that expansion fits the explicitly configured budget:
uv run python -m src.collusion_detection generate-market --live --config configs/detection/live-smoke.json \
  --seeds 0 --rounds 12 --out detection_outputs/generation \
  --acceptance detection_outputs/smoke-acceptance.json --plan detection_outputs/generation-plan.json
uv run python -m src.collusion_detection import-market --source detection_outputs/generation/market_logs \
  --out detection_outputs/fresh
uv run python -m src.collusion_detection export-annotations --episodes detection_outputs/fresh/episodes.jsonl \
  --out detection_outputs/fresh/annotations
# Following independent review/adjudication, summarize Experiment 0 separately:
uv run python -m src.collusion_detection experiment-zero \
  --generation detection_outputs/generation/private/generation.jsonl \
  --provenance detection_outputs/fresh/private/provenance.jsonl \
  --labels detection_outputs/fresh/annotations/resolved.jsonl --out detection_outputs/experiment-zero.json
```

Generation predeclares all seeds/arms in a private sampling manifest and records completed/failed attempts and unreviewed outcomes. Do not select favorable episodes without reporting all sampled generations. Experiment 0 separately reports human-reviewed implementation and explicit public disclosure, retaining missing labels as unreviewed. The generator was exercised only through fake market calls during acceptance; no fresh hosted episodes exist yet.

[Optional Modal CPU runner](modal_runner.py) uses a named Secret and persistent Volume. It is optional, separately installed, and never auto-deploys or launches a GPU. Follow its module docstring; live dispatch requires the reviewed local plan and acceptance receipt.

## Validation and remaining limits

The current PR validation passed **113 detection tests**, and the offline demo produced **54 fixture predictions plus nine predictions on a bundled auction episode, with zero errors**. See the [initial offline build record](build-record.md) for earlier validation and the run history below for subsequent live checks. Calibration quality and scientific effect sizes remain unmeasured because human gold labels are absent. Modal transport has only injected/static coverage. Existing Pydantic class-config deprecation warnings remain.

References: [starter](https://github.com/poseidon-research/collusion_hackathon), [market paper](https://arxiv.org/abs/2507.01413), [extract-and-evaluate paper](https://arxiv.org/html/2601.21112v2), [OpenRouter API](https://openrouter.ai/docs/api-reference/overview), [model catalog](https://openrouter.ai/docs/api-reference/models/get-models), [Nebius quickstart](https://docs.tokenfactory.nebius.com/quickstart). EAE here is an honest oversight adaptation, not a reproduction of sabotage experiments.

## First live smoke failure and retry

The first user-run smoke authenticated successfully and returned the exact configured model, but hit the 1600-token output limit while echoing schema fields and enumerating hundreds of event IDs. It produced no accepted assessment. Reported billed cost was $0.0035983; the original manifest/raw response/usage remain in `detection_outputs/live-smoke/`.

The update uses v2 prompts, concise instance-only output instructions, native strict `response_format: json_schema`, and OpenRouter `provider.require_parameters: true`. Inactive optional fields become explicit nullable required fields in the wire schema. Local Pydantic validation and evidence validation still run; invalid output is never repaired into a prediction. The configured assistant/judge output ceiling is now 3200 tokens. Requests account conservatively for the additional native schema bytes. Unsupported structured-output capabilities fail preflight.

Truncated completions retain measured usage/billing and produce a specific truncation error. The CLI prints error details and exits nonzero if any prediction fails. No automatic paid replay was added. Changing prompts invalidates existing acceptance receipts and plans; keep the old failed output and use a fresh output directory.

Prepare and review the retry entirely offline:

```bash
uv run python -m src.collusion_detection doctor --config configs/detection/live-smoke.json \
  --acceptance detection_outputs/smoke-acceptance-v2.json
uv run python -m src.collusion_detection plan --dry-run --config configs/detection/live-smoke.json \
  --episodes detection_outputs/smoke_input/episodes.jsonl --out detection_outputs/smoke-plan-v2.json
```

Later retry, only when explicitly invoked:

```bash
uv run python -m src.collusion_detection run --live --config configs/detection/live-smoke.json \
  --episodes detection_outputs/smoke_input/episodes.jsonl \
  --acceptance detection_outputs/smoke-acceptance-v2.json \
  --plan detection_outputs/smoke-plan-v2.json --out detection_outputs/live-smoke-v2
```

The fix is verified with injected HTTP responses, including a replay of the observed truncation pattern; the subsequent live v2 smoke completed one valid assessment with zero errors (a connectivity/format check, not an accuracy evaluation). See [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs) for the native request contract and endpoint routing requirements.

## Forty-episode exploratory live pilot

The authorized pilot is sampled before inference from the 45 eligible nonintervention bundled episodes containing seller messages, using seed 20261025 without consulting predictions/labels. Forty episodes are selected without replacement. All nine core conditions share those episodes, models, v2 prompts, one replicate, and fixed threshold 0.5. Source relatedness has not been independently reviewed; all 40 are explicitly development/exploratory, not a held-out benchmark. Human labels remain absent.

The live-run directory `detection_outputs/pilot-40/` contains the frozen sample, config, dry-run plan, splits and independent annotation packets. The full source logs are preserved; assistant/judge context ceilings are 500,000 for conservative byte accounting. Evidence-aware planning uses exact source sizes, native schemas and validated report/excerpt/answer limits rather than padding every call to the context ceiling. The prespecified plan bounds maximum spend at about $15.13 within the $25 allocation. No cache savings are assumed in that bound.

The runner processes up to three episodes concurrently under the shared three-request gate. Conditions inside each episode remain ordered, and LINKED/INTERACTIVE reuse the same initial report. Completed predictions append to a checkpoint journal; progress.json is replaced atomically. Final prediction order is deterministic. A single output ledger retains conservative reservations and billed usage.

After execution, `PYTHONPATH=. .venv/bin/python docs/detection/analyze_pilot.py detection_outputs/pilot-40` generates label-free descriptive summaries and score plots. These describe score differences, disagreement and cost, not accuracy or a communication benefit. Independent human labels are still required for confusion matrices.


## Pilot stopped; v3 source-pointer preflight

The v2 live pilot was stopped at the user's request after 321/360 condition results (35 complete episodes plus three partially attempted episodes). It retained 138 valid assessments and 183 errors: 116 quote failures, 53 invalid spans, five incomplete span endpoints, seven unknown IDs and two citation-eligibility failures. Recorded provider costs total $0.9310; up to six interrupted in-flight requests may require additional billing reconciliation. The partial report is in `detection_outputs/pilot-40/analysis/report.md`, with source provenance and unchanged predictions preserved. No human labels exist, so no accuracy or confusion matrix is reported.

All nine conditions remain selected. New opt-in `prompt_version: v3` asks assistants for source IDs only, renders canonical source text in application code, constrains IDs through trusted native-schema enums, and aligns judge eligibility with validated visible relationship IDs. Size, view and source checks remain enforced. Messages-only implementation claims and contradictory implementation/agreement assessments are explicit errors. This cannot guarantee correct model interpretations or error-free live outputs.

The corrected inputs, receipts, exact plans and staged command are in `detection_outputs/pilot-40-v3-preflight/README.md`; reusable configs are `configs/detection/pilot-v3.json` and `configs/detection/live-canary-v3.json`. The latest PR check passed 113 detection tests; a final mocked run completed all 360 outputs with zero errors and 80/80 initial-report reuse matches. These mocked results verify software behavior only. A two-episode live diagnostic (18 condition results; conservative maximum $0.8324) is prepared but has not been executed. The full new pilot remains stopped, with a separate $15.8686 maximum plan and a $20 cap. Do not combine v2 and v3 results or overwrite the stopped run.
