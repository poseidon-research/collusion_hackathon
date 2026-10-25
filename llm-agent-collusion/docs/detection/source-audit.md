# Starter source audit

Starting checkout: `e6fb1308dae9cf97ae42aec1c5f7889d9e2291e8`, cloned into an empty workspace on 2026-10-25. No unrelated pre-existing changes were present. This differs from the previously inspected `d585a7b3be50366cc2bdf5789bf8add5890b2abd`.

Inspected root README/COMPUTE.md, project README/pyproject.toml, simulation.py, cda_types.py, market/market.py, market/agents.py, util/misc.py, util/logging_util.py, evaluation/evaluate.py, evaluation/metrics.py, seller_individual_eval.jinja2, message_overseer_prompt.jinja2, src/resources/model_wrappers.py and ../util/poseidon_compute/llm_config.py.

Verified behavior:

- `ExperimentLogger.log_auction_round` writes MarketRound with four-backtick JSON fences. Bundled results actually use this form.
- `Market.run_round` sends previous completed round seller messages to each seller. All agent actions read the start-of-round book snapshots. Newly emitted messages cannot influence other sellers' same-round actions. Future completion ordering/JSON key ordering does not identify causal sequence.
- `add_seller_ask` records numeric submissions. Initial orders also enter r1 bookkeeping. JSON null/absent ask does not call withdrawal; the literal string `"null"` removes the queue order and deletes the round entry. Omitted numeric entries therefore cannot resolve withdrawal versus omission/persistent prior orders. Equal numbers do not prove a changed price. Matching removes queue orders without erasing submission bookkeeping.
- The auction matches the highest bid and lowest ask when crossing, prices at their two-decimal midpoint, and removes matched orders.
- The original live `MessageOverseer` rates current messages; score 4 activates a gag order for future rounds. Existing oversight episodes are intervention data, not untreated main-study episodes.
- Original evaluation's seller-individual template reads private reflection/plan/memory/scratch-pad fields. It is not a combined public-message/executed-action detector.
- Native ask/trade collusion indices, price/profit summaries and PASS/FAIL are economic/heuristic proxies. They are not reliable implemented-collusion gold labels and are never imported as such.
- Starter `ModelWrapper.generate` returns text only and may return None on API failure. The new adapter preserves usage, returned provider/model, errors and retries separately.
- Shared `poseidon_compute.resolve_model` loads compute.env and lets LLM_MODEL override all roles; it also has alias/version mapping and provider fallback behavior. The new oversight/generation clients never call it and reject live LLM_MODEL overrides. They require exact configured model IDs and never change global provider routing.

Only the opt-in private seller-context hook changes the simulator; auction mechanics and original entry points are preserved. Private-context generation disables per-agent prompt logging, and call traces stay outside public market logs.

Official hosted-inference references consulted during implementation: [OpenRouter schema](https://openrouter.ai/docs/api-reference/overview), [catalog](https://openrouter.ai/docs/api-reference/models/get-models), [Nebius current quickstart](https://docs.tokenfactory.nebius.com/quickstart). The current Nebius quickstart has moved to Token Factory; default base URL in new support is `https://api.tokenfactory.nebius.com/v1`, with an explicit override available. Live preflight must revalidate current model availability/limits/prices. No authenticated provider call was made during inspection/build.
