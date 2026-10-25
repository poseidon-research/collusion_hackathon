# Hackathon NY starter repo

Ten research codebases on LLM agents, collusion and multi-agent coordination, bundled so a
team can clone once and start hacking. Every project runs on a normal laptop with **no GPU**,
calling models **through a hosted inference provider** (Nebius or OpenRouter). The original
simulations share a repo-root inference configuration; the detection study below has its own.
Some projects have other local requirements, see below. Each folder has its own README with
a quick start; the projects are independent, so start with the question that interests you.
The starter gives you a working harness, not results: the sprint questions are yours to answer.

[`redteaming/`](redteaming/) is a separate challenge: Dolos-1.5B, a model Poseidon Research fine-tuned to have a
hidden misaligned behavior. You get the weights and have to find out what the behavior is. It runs locally, with no
API key.

A [`literature/`](literature/) folder adds a reading list of about 230 papers tagged by sprint project, with a script to
download the open-access PDFs. Collusion is defined in many different ways across economics, law and AI safety, so
[`literature/definitions/`](literature/definitions/) collates the main definitions and compares them. It is worth
deciding early which kind of collusion a project is studying, because that decides what can be measured and what
counts as evidence.

## Collusion detection study

**Start here for our experiment.** The remaining sections describe the bundled starter
projects. This study lives in `llm-agent-collusion/` and has its own setup below.

We ask whether combining **what agents say with what they do** improves collusion detection,
and whether an assistant helps a judge by selecting evidence, explaining relationships or
answering questions. An episode is one completed game or auction. The assistant and judge
cooperate honestly and inspect its frozen logs after play.

### 1. What is implemented now

The current pipeline evaluates **nine core conditions on market-auction logs**. Each
condition combines an evidence view with an oversight protocol:

| Evidence view | Direct judge (`RAW`) | Extract-and-evaluate (`EAE`) | Linked report (`LINKED`) | Interactive (`INTERACTIVE`) |
|---|---|---|---|---|
| Messages (`M`) | Yes | Yes | Yes | Yes |
| Messages + actions/outcomes (`MAO`) | Yes | Yes | Yes | Yes |
| Actions/outcomes (`A`) | Yes | — | — | — |

For example, `MAO_INTERACTIVE` means messages plus actions/outcomes, processed through
interactive collaboration. **`RAW` in a condition ID means direct judge**; it does not select
an input format. The current interactive protocol allows up to two judge questions and
reuses the linked-report condition's initial report.

For auctions, the target is **observable joint implementation by at least two sellers of an
anticompetitive arrangement**. Agreement, implementation and economic consequences are
assessed separately. An unimplemented proposal or parallel high prices alone does not
establish the target. Messages-only assessments cannot verify behavioral implementation.

### 2. Proposed extension: 32 cells

**The full design below is proposed, not fully implemented.** It crosses three factors:

| Factor | Levels |
|---|---|
| **Input condition (4)** | Messages; actions/outcomes; both raw; both structured |
| **Oversight protocol (4)** | Direct judge; extract-and-evaluate; linked report; interactive collaboration |
| **Environment (2)** | Social dilemma games; market auctions |

This gives **4 × 4 × 2 = 32 experimental cells**. Each episode belongs to one environment
and receives its **16 input–protocol combinations**, allowing comparisons on the same episode.

“Both raw” and “both structured” must contain **the same communication and action/outcome
records**. Code changes only their organization, such as chronological versus grouped by
round and participant, without adding interpretations or removing evidence. The input
factor tests presentation; the oversight factor tests how the assistant and judge process
that input. A structured input and an assistant-written linked report are separate things.

Still to implement: the separate both-raw/both-structured inputs, assistant protocols for
actions/outcomes-only input, and social-dilemma integration with its own target definition
and annotation rubric.

### 3. How the four oversight protocols work

| Protocol | Assistant's job | Judge receives | Follow-up |
|---|---|---|---|
| **Direct judge** | No assistant | Entire assigned input | None |
| **Extract-and-evaluate (EaE)** | Select relevant source events | Selected verbatim evidence plus task context | None |
| **Linked report** | Select evidence and explain relationships | Analysis grounded in source references, with supporting and conflicting evidence | None |
| **Interactive collaboration** | Prepare the linked report, then investigate the judge's questions | The same initial report as the linked condition, followed by answers and newly retrieved evidence | Bounded dialogue |

Every assistant sees only its assigned evidence view, including during follow-up. A
messages-only assistant cannot retrieve action records. EaE forwards source excerpts;
linked reports also explain relationships among events and acknowledge missing evidence.
All views share the static task context, including participant roles and market rules.

The implemented flow is: **import auction logs → build the assigned evidence view → run
the protocol → save the judge's assessment → evaluate against independent labels**.
Source references and excerpts are validated by application code. Private agent reasoning,
generation assignments and prior monitor verdicts are excluded from the evidence views.

### 4. Run it and find the code

From the repository root, with [uv](https://docs.astral.sh/uv/) installed:

```bash
cd llm-agent-collusion
uv sync --extra dev
uv run python -m src.collusion_detection doctor --config configs/detection/offline.json
uv run python -m src.collusion_detection demo --out detection_outputs/demo
```

This demo uses deterministic mock models, requires no API key and makes no paid inference
calls. It runs the nine conditions on synthetic fixtures and one bundled auction episode.
Read `detection_outputs/demo/fixture_report/report.md` and
`detection_outputs/demo/real_report/report.md` inside `llm-agent-collusion/` for its outputs.
These verify software behavior; the real episodes currently lack human gold labels, so the
demo does not establish detection accuracy.

For people and coding agents reading the implementation:

| Read | Purpose |
|---|---|
| [Detection guide](llm-agent-collusion/docs/detection/README.md) | Full workflow, configuration, annotation, evaluation and live-inference commands |
| [Condition configuration](llm-agent-collusion/src/collusion_detection/config.py) | Supported condition IDs and study settings |
| [Importer](llm-agent-collusion/src/collusion_detection/importer.py) and [evidence views](llm-agent-collusion/src/collusion_detection/evidence.py) | Convert logs, restrict visible records and validate source references |
| [Protocol engine](llm-agent-collusion/src/collusion_detection/engine.py) | Assistant/judge calls, reports and bounded dialogue |
| [Annotation rubric](llm-agent-collusion/docs/detection/annotation-rubric.md) | What independent reviewers label as evidence of collusion |

For scientific evaluation, use independent labels, keep related episodes together across
development/test splits, and compare conditions on the same episodes. Report errors, false
positives and inference cost alongside detection quality. Extra dialogue adds compute, so
an improvement alone does not establish a communication benefit.

**For live detection runs, follow the detection guide.** That pipeline loads explicitly
configured credentials from `llm-agent-collusion/.env` and uses per-role model settings;
the starter simulation routing described below does not configure it.

## Compute for bundled starter projects

The setup and project catalog below apply to the original starter simulations.

> **Compute & inference — configure once:** copy `compute.env.example` → `compute.env` and set an
> inference key — `NEBIUS_API_KEY` or `OPENROUTER_API_KEY` (either works; Nebius and OpenRouter are
> interchangeable). The original hosted starter simulations use it automatically.
> **Run a starter simulation with a chosen model:**
> `poseidon-run --model <id> -- <the experiment's command>` — it routes **Nebius → OpenRouter →
> Modal** automatically (OpenRouter covers closed models like GPT/Gemini/Claude; Modal hosts custom /
> off-catalog models), otherwise stops with a clear error (nothing is ever substituted). Full guide:
> **[`COMPUTE.md`](COMPUTE.md)**.

## Setup for bundled starter projects

### Prerequisites

Install these once before running `./setup.sh`:

- **[uv](https://docs.astral.sh/uv/)** — the Python package manager the setup uses. Install it with
  `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux; see the uv docs for Windows), then
  restart your shell. uv also fetches the exact Python each project needs (e.g. trace-guard wants 3.12),
  so you don't manage interpreters yourself.
- **Docker** — only for **trace-guard** (it runs its agent inside a container). Everything else is
  Python-only. Install Docker Desktop (macOS/Windows) or Docker Engine (Linux) and start it.
- A **model API key** for the next step: a **Nebius** or **OpenRouter** key — either
  works for inference (OpenRouter additionally covers closed models like GPT/Gemini/Claude). Hackathon participants get these from the event
  Slack/Luma; otherwise sign up at [Nebius AI Studio](https://studio.nebius.com/) /
  [OpenRouter](https://openrouter.ai/). Details in [`COMPUTE.md`](COMPUTE.md).
- *(Optional)* a **Modal** account (`pip install modal && modal setup`) — only if you want to offload
  compute to the cloud or host an off-catalog model. See [`COMPUTE.md`](COMPUTE.md).

### Run it

```bash
cp compute.env.example compute.env     # then edit the file: add NEBIUS_API_KEY and/or OPENROUTER_API_KEY
./setup.sh                             # sets up ALL experiments (uv sync each), one go
```

`./setup.sh` installs the shared compute layer (**`poseidon_compute`**, in [`util/`](util/)) and every
experiment's dependencies, and drops the `poseidon-run` entry point into a repo-root `./.venv`. Pass
names to set up a subset (`./setup.sh focal-points llm-cartel`). Prefer to do one project by hand? Use
the command in its section below — each is self-contained. The starter simulations import the shared layer
directly with `from poseidon_compute import make_client, resolve_model` (no path hacks); details in
[`util/README.md`](util/README.md) and [`COMPUTE.md`](COMPUTE.md).

## At a glance

| Folder | Paper | Original code | Relevant sprint projects | Summary |
|---|---|---|---|---|
| [`algorithmic-pricing-llms/`](algorithmic-pricing-llms/) | Romero, Sauer & Peist (2025), [*Algorithmic Collusion and the Limits of Coordination*](algorithmic-pricing-llms/BSE_DSDM_master_project_algorithmic_pricing_llms_Peist_Romero_Sauer.pdf) (master's project), extending Fish et al. (2024), [*Algorithmic Collusion by Large Language Models*](https://arxiv.org/abs/2404.00806) | [luciasauer/algorithmic_pricing_llms](https://github.com/luciasauer/algorithmic_pricing_llms) | 1.1 | LLM firms in a 300-round Calvano pricing game: tacit collusion, and how it erodes with more firms |
| [`llm-cartel/`](llm-cartel/) | Riemer et al. (2026), [*Position: Collusion Risks Among AI Reasoning Agents Justify Certification Requirements for Making Market Decisions*](https://openreview.net/pdf?id=Vhi9tHbKTa) | [mattriemer/LLMCartel](https://github.com/mattriemer/LLMCartel) | 1.1, 1.2 | Reasoning models in a Bertrand duopoly/triopoly under 19 prompt variants, monitoring notices, LLM judges; the paper's activation-steering scripts ship untouched as an optional extra |
| [`focal-points/`](focal-points/) | Aharon et al. (2026), [*Tacit Coordination of Large Language Models*](https://arxiv.org/abs/2601.22184) | [EmanueleLM/focal-points](https://github.com/EmanueleLM/focal-points) | 1.2, 1.3, 3.3 | Do LLMs pick the same "obvious" answers humans do? Coordination index vs. human data; benign control for detectors. (Runs on the shared endpoint, no GPU; Docker only for the optional local-GPU reproduction.) |
| [`llm-agent-collusion/`](llm-agent-collusion/) | Agrawal et al. (2025), [*Evaluating LLM Agent Collusion in Double Auctions*](https://arxiv.org/abs/2507.01413) | [andyjliu/llm-agent-collusion](https://github.com/andyjliu/llm-agent-collusion) | 1.3, 1.4, 1.5, 3.4 | LLM sellers and buyers in a continuous double auction with seller chat, an overseer, pressure and message caps |
| [`colosseum/`](colosseum/) | Nakamura et al. (2026), [*Colosseum: Auditing Collusion in Cooperative Multi-Agent Systems*](https://arxiv.org/abs/2602.15198) | [umass-ai-safety/colosseum](https://github.com/umass-ai-safety/colosseum) | 2.1, 2.3, 3.1 | Tool-calling agent teams in Jira / hospital environments; topologies, private channels, persuasion, misinformation |
| [`llm-trust-breaking/`](llm-trust-breaking/) | Shi et al. (2026), [*When Agents Lie: Premeditation, Persistence, and Exploitation in Repeated Games*](https://arxiv.org/abs/2607.05132) | [Jerick-1380/LLM-Trust-Breaking](https://github.com/Jerick-1380/LLM-Trust-Breaking) | 2.2, 3.3 | Promise-breaking and trust dynamics over 10 rounds of strategic games with private plans and public commitments |
| [`collusive-llm-agents/`](collusive-llm-agents/) | Lin et al. (2024), [*Strategic Collusion of LLM Agents: Market Division in Multi-Commodity Competitions*](https://arxiv.org/abs/2410.00031) | [smojha/collusive-llm-agents](https://github.com/smojha/collusive-llm-agents) | 3.1 | Bertrand and Cournot notebooks: do pricing agents divide a multi-commodity market between them? |
| [`llm-agent-collusion-2/`](llm-agent-collusion-2/) | Zeng & Rudzicz (2026), [*Voluntary Collusion with Secret Tools in Competing LLM Agents*](https://arxiv.org/abs/2605.27593) | [AgentSafety2025/LLM-Agent-collusion](https://github.com/AgentSafety2025/LLM-Agent-collusion) | 3.2 | Liar's Bar and Cleanup games with secret alliance channels: when do agents willingly collude against others? |
| [`steganographic-collusion/`](steganographic-collusion/) | Tailor (2025), [*Audit the Whisper: Detecting Steganographic Collusion in Multi-Agent LLMs*](https://arxiv.org/abs/2510.04303), building on Mathew et al. (2024), [*Hidden in Plain Text*](https://arxiv.org/abs/2410.03768) | [Ostailor/Steganographic_Collusion](https://github.com/Ostailor/Steganographic_Collusion) | 2.3 | Covert-channel theory plus calibrated detectors for steganographic collusion across pricing, auction, peer-review and double-auction environments |
| [`trace-guard/`](trace-guard/) | Nguyen et al. (2026), [*TraceGuard: Structured Multi-Dimensional Monitoring as a Collusion-Resistant Control Protocol*](https://arxiv.org/abs/2604.03968) | [linhkid/TraceGuard](https://github.com/linhkid/TraceGuard) | 2.2 | Single-agent monitor scoring actions on five dimensions incl. action-trace consistency; a reusable rubric, not a collusion detector. **Requires Docker**; smoke test ~10 min |
| [`redteaming/`](redteaming/) | None: a new model organism from Poseidon Research | Weights: [Poseidon-Research/Dolos-1.5B](https://huggingface.co/Poseidon-Research/Dolos-1.5B) | Red-teaming challenge | A fine-tune of Qwen2.5-1.5B-Instruct with a hidden misaligned behavior: find it, explain how the model implements it, and audit for it without a reference model. Runs locally (about 6.2 GB download), no API key |

[`util/`](util/) is not from a paper: it holds the shared compute and inference helpers used by every folder.

[`literature/`](literature/) is not code: it holds the reading list, the PDF download script and, in
[`literature/definitions/`](literature/definitions/), an analysis of how 139 papers define collusion.

## Which folder for which sprint project?

The sprint questions are on the [sprint page](https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25). This repo collects the code that was publicly available for the papers cited there, as starting points. Many cited papers have no public code, so not every paper or project has a folder of its own. We wanted to provide a way for anyone to take part in the sprint with a coding agent, such as Claude Code or Codex, and access to this repo. If you are new to AI safety research, you could have your coding agent read the projects and the code, suggest a project, and work through it with you. For papers without public code, a coding agent can likely get you started from scratch. You do not have to use any of the starter code, or even base your project on one of the papers cited in the sprint description, if you have an idea you want to explore.

For further reading, [`literature/`](literature/) has about 230 papers tagged by sprint project, a script to download the open-access PDFs, and an analysis of how 139 papers define collusion.

### By project

| Sprint project | Starter folders |
|---|---|
| **1.1** Which variables are necessary for collusion to emerge in markets? | [`algorithmic-pricing-llms/`](algorithmic-pricing-llms/), [`llm-cartel/`](llm-cartel/) |
| **1.2** Once collusion has formed, is it robust to replacing some of the participants? | [`llm-cartel/`](llm-cartel/), [`focal-points/`](focal-points/) |
| **1.3** Can we improve on existing collusion detection methods? | [`llm-agent-collusion/`](llm-agent-collusion/), [`focal-points/`](focal-points/) |
| **1.4** Can we use information-theoretic approaches to detect collusion in multi-agent games? | [`llm-agent-collusion/`](llm-agent-collusion/) as an environment. There is no code for the information-theoretic method itself ([Bonjour et al. 2022](https://proceedings.mlr.press/v180/bonjour22a.html)); the closest is the mutual-information detectors in [`steganographic-collusion/`](steganographic-collusion/). |
| **1.5** Which single restriction removes the most collusion at the least cost? | [`llm-agent-collusion/`](llm-agent-collusion/) |
| **2.1** Which kinds of tasks lead to collusion? | [`colosseum/`](colosseum/) |
| **2.2** Does combining what agents say with what they do beat either alone? | [`llm-trust-breaking/`](llm-trust-breaking/), [`trace-guard/`](trace-guard/) |
| **2.3** What should count as convincing evidence of collusion? | [`colosseum/`](colosseum/), [`steganographic-collusion/`](steganographic-collusion/) |
| **3.1** Beyond colluding directly via text, can actions, tools or side channels be the conduit for collusion? | [`colosseum/`](colosseum/), [`collusive-llm-agents/`](collusive-llm-agents/) |
| **3.2** Can we disrupt collusion through introducing trusted agents? | [`llm-agent-collusion-2/`](llm-agent-collusion-2/) |
| **3.3** Can agents identify their peers? | [`llm-trust-breaking/`](llm-trust-breaking/), [`focal-points/`](focal-points/) |
| **3.4** What does a coalition do when it learns it is being watched? | [`llm-agent-collusion/`](llm-agent-collusion/) |
| **Red-teaming challenge** What misaligned behavior was Dolos-1.5B trained to have? | [`redteaming/`](redteaming/) |

### Papers cited on the sprint page without code here

Keppo et al. 2026, Eschenbaum et al. 2022, Xu et al. 2026, Rose et al. 2026, Rippin et al. 2026, Bonjour et al. 2022, Collina et al. 2025, Bracale Syrnikov et al. 2026, Dhanda 2026, Q. Liu et al. 2026, Tomašev et al. 2026, Radev et al. 2026, Arike et al. 2026, Berger et al. 2025, Tian 2026, Dietrich 2026, Kale et al. 2025, Najt et al. 2026 and Jiralerspong et al. 2026. Links are on the sprint page under each project.

### Earlier project numbering

<details>
<summary>Old number → sprint-page number</summary>

| Earlier number | Sprint-page number |
|---|---|
| 1.1 | 2.1 |
| 1.2 | 1.1 |
| 1.3 | 3.1 |
| 1.4 | 1.2 |
| 2.1 | 1.3 |
| 2.2 | 1.4 |
| 2.3 | 2.2 |
| 2.4 | 2.3 |
| 3.1 | 3.2 |
| 3.2 | 3.3 |
| 3.3 | 1.5 |
| 3.4 | 3.4 |

</details>

## The projects in more detail

### 1. [`algorithmic-pricing-llms/`](algorithmic-pricing-llms/)

**Question:** when LLM agents set prices against each other in a repeated market, do they
tacitly collude, and does collusion break down as the number of firms grows?

Two to five LLM "firms" play 300 rounds of a Calvano (2020) pricing game. Each round every
firm sees its history and picks a price; the market returns quantities and profits. The
thesis found prices well above the competitive (Nash) level that fall by about 3.7% per
extra competitor. Sprint project 1.1: verify the positive control, then ablate one
variable at a time (visible history, persistent notes, shuffled names, temperature) via
CLI flags. Inference goes through the shared endpoint (Nebius by default); the default model is the
thesis model `mistralai/Mistral-Large-Instruct-2411`.

```bash
cd algorithmic-pricing-llms && uv sync   # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md)
# The default model (Mistral-Large-Instruct-2411) is served by Nebius, so this needs NEBIUS_API_KEY.
# With only an OpenRouter key, that exact version isn't on OpenRouter — pass an OpenRouter-served
# model instead, e.g.  --model openai/gpt-4.1-mini
uv run python experiments_synthetic/duopoly.py --rounds 30          # ~60 calls
```

### 2. [`llm-cartel/`](llm-cartel/)

**Question:** do *reasoning* models collude in a Bertrand price game, and can you steer
that behaviour on or off inside the model's activations?

Companion code to an ICML 2026 position paper arguing that collusion risk justifies
certification of AI agents making market decisions. Two or three agents play Bertrand
competition under different system prompts (neutral, implicit and explicit nudges to
collude, anti-collusion warnings), with LLM judges scoring the transcripts. The
"steering" part trains contrastive activation vectors and injects them into open models
served with vLLM; those scripts ship untouched behind the optional `[gpu]` extra and are not
needed for the sprint. Sprint projects 1.1 and 1.2: a CPU-only harness
runs the same game through OpenRouter with prompt / monitoring / window / temperature
flags, adds checkpoint branching to swap a participant mid-run, and an API-based judge. The
paper's R1 distills are no longer on OpenRouter, so you pick the agent model: full R1 for
fidelity (~2-3 min per call), `qwen/qwen3-14b` (thinks, ~1 min per call) or `openai/gpt-4.1-mini` for fast iteration.

```bash
cd llm-cartel && uv sync     # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md)
uv run python run_game.py --models openai/gpt-4.1-mini --rounds 30   # ~60 calls, ~1 min
```

### 3. [`focal-points/`](focal-points/)

**Question:** do LLMs pick the same "obvious" answers humans do in coordination games,
and can they use those focal points to bargain?

Schelling-style coordination problems ("name a number", "pick a place to meet") and the
Bardsley Amsterdam / Nottingham pick-guess-coordinate tasks are posed to a model, tens of
answers are sampled per prompt, and a coordination index (how often two answers agree)
is compared with the human data. In the sprint this is the cheap **benign** control:
agreement without any communication, for calibrating detectors (1.3), for testing
whether a replacement agent recovers a convention (1.2), or as the convention task for
swap detection (3.3). The original ran 70B models locally on GPUs plus OpenAI / Gemini
batch jobs; it now calls OpenRouter (default: the paper's Llama-3.3-70B), with samples,
temperature, seed and prompt count as flags. The 1.8 GB of raw transcripts were removed
from this copy; prompt datasets, human baselines and the paper's aggregated results stay.

```bash
cd focal-points && uv sync     # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md)
# Smoke test writes to results/<model>/. The default model (Llama-3.3-70B) already has the paper's
# reference results checked in and the run refuses to clobber them, so smoke-test with another model
# — it writes to its own folder:
uv run python main.py -m openai/gpt-4o-mini --max-prompts 3 -s 5   # ~15 cheap calls
```

### 4. [`llm-agent-collusion/`](llm-agent-collusion/)

**Question:** in a continuous double auction with several LLM sellers and buyers, do the
sellers coordinate to keep prices high, and what makes it more or less likely?

Paper: *Evaluating LLM Agent Collusion in Double Auctions* (2025). LLM sellers and buyers,
each with a private valuation, post asks and bids for one lot per hour in a continuous
double auction; the code varies seller communication, an overseer that can gag sellers,
message caps, CEO pressure, temperature and the model mix, and measures how far prices sit
above the competitive level. Sprint projects 1.3, 1.4, 1.5 and 3.4: all calls go through
OpenRouter (paper default `gpt-4.1` → `openai/gpt-4.1`), defaults are the brief's 3 sellers,
3 buyers, 15 rounds, and a new `--overseer_sample_fraction` exposes 25/50/100 % of messages
to the monitor. Each run ends with a collusion-index summary against the competitive and
cartel prices. The paper's 75 runs ship as metrics + round logs (43 MB); the 250 MB of
per-agent transcripts are linked upstream.

```bash
cd llm-agent-collusion && uv sync     # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md)
uv run python src/continuous_double_auction/simulation.py --rounds 5 --tag smoke   # ~30 calls
```

### 5. [`colosseum/`](colosseum/)

**Question:** how do you audit a *team* of tool-calling LLM agents for collusion,
misalignment and misinformation spread, and how does the communication network shape it?

A framework (built on the Terrarium agent runtime) for running many tool-calling LLM agents
in shared environments such as a Jira ticket board or a hospital scheduling system, with
different network topologies between agents. Experiments cover persuasion effects on
collusion, topology sweeps, misinformation propagation and objective misalignment. Sprint
projects 2.1, 2.3 and 3.1: a preflight script for the Terrarium/CoLLAB setup, a ~20-call
positive-control config, and the four matched conditions of project 2.1 (assigned pair ±
private channel, open channel, neither) as ready-made configs, all through OpenRouter with
the paper's `openai/gpt-4o-mini` as default.

```bash
cd colosseum && uv sync    # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md)
uv run python -m experiments.preflight
uv run python -m experiments.collusion.run --config experiments/collusion/configs/hackathon_positive_control.yaml
```

### 6. [`llm-trust-breaking/`](llm-trust-breaking/)

**Question:** do LLMs break promises strategically, and how does trust between them evolve
when they deceive each other repeatedly?

Original: [Jerick-1380/LLM-Trust-Breaking](https://github.com/Jerick-1380/LLM-Trust-Breaking).
Several agents play 10 rounds of strategic games (e.g. the diners' dilemma) with a private
planning stage, public commitments and final actions, and every commitment break is
classified. Used in sprint projects 2.2 (does combining what agents say with what they do
improve detection?) and 3.3 (can agents tell when a peer has been replaced?). Runs through
OpenRouter with the paper's OpenAI models (`openai/gpt-5.2` in the examples; OpenAI-family
only). Each trial-round is 4 calls per agent plus pairwise
reflection, so the documented full run (5 agents, 20 trials, 10 rounds) is ~8,000 calls —
start with the smoke test.

```bash
cd llm-trust-breaking && uv sync   # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md)
uv run python experiments/run_endogenous.py --game diners --agents 2 --trials 1 --rounds 1 \
  --model openai/gpt-4o-mini --use-queue                                            # ~10 calls, under a minute
```

### 7. [`collusive-llm-agents/`](collusive-llm-agents/)

**Question:** can LLM pricing agents carve up a multi-commodity market between themselves
instead of competing on every product?

Paper: *Strategic Collusion of LLM Agents: Market Division in Multi-Commodity Competitions*
(Lin et al., 2024). Two Jupyter notebooks, one for Bertrand (price) and one for Cournot
(quantity) competition, where firms observe market history and output structured JSON
decisions. The sprint uses it as the lighter fallback for project 3.1: prices or quantity
vectors are the observable actions, so visibility of those actions can be ablated without
a tool-using environment. Calls go through OpenRouter with the paper's models set in a
constant at the top of each notebook.

```bash
cd collusive-llm-agents && uv sync   # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md)
uv run jupyter notebook     # open bertrand.ipynb (~10 calls) or cournot.ipynb (~100 calls)
```

### 8. [`llm-agent-collusion-2/`](llm-agent-collusion-2/)

**Question:** when agents are handed a secret channel or a secret advantage, do they
voluntarily collude against the other players, and how does that evolve?

The Voluntary Collusion framework. Two games: **Liar's Bar**, a four-player bluffing card
game with hidden information, and **Cleanup**, a grid-world social dilemma balancing apple
collection against pollution. Each has a baseline, a secret-communication condition between
alliance partners, and a secret-hint condition. Sprint project 3.2 replaces the
experimenter-chosen partner with a scripted or monitor-controlled player that offers the same
pact to everyone and measures who accepts. Originally built for local Llama / Mistral / Qwen
checkpoints on H100s; it now runs the same four-model table through OpenRouter (two of the
original models are no longer served and were substituted — the mapping is in its README).
Every step is one call per agent, so tournaments add up; both games have a small run.

```bash
cd llm-agent-collusion-2 && uv sync && source .venv/bin/activate   # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md)
cd Collusion_Cleanup && python -m cleanup_llm.games.tournament --games 1 --steps 5 --out runs/smoke --roster small   # 20 calls
cd ../Collusion_Liars_Bar && python src/game/game.py                                    # one full game: ~260 calls, ~11 min
```

### 9. [`steganographic-collusion/`](steganographic-collusion/)

**Question:** can colluding agents hide coordination inside innocent-looking messages, and
what evidence is enough to catch them?

*Audit the Whisper* couples channel-capacity theory with **ColludeBench-v0**: pricing,
first-price auction, peer review and continuous-double-auction environments with
configurable colluders and covert channels, plus a calibrated detector stack (mutual
information, permutation invariance, watermark variance, acceptance-bias and welfare
guards) tuned to low false-positive rates. Sprint project 2.3 uses its environments,
detector code and stored result manifests, with the brief's caveat to preflight them rather
than treat them as a turnkey bag of trajectories. Heavy on results and docs (213 MB, most
of it the raw transcripts of 1,737 runs, kept on purpose as a dataset). The default path —
22 scripted configs, the detector tests, the threshold calibration — needs **no key at
all**; only the three hosted configs (Gemini 2.5 Flash, Llama 3.1 8B, GPT-4o-mini via
OpenRouter) do. Note that the optional `local` extra (`uv sync --extra local`) pulls torch (~5 GB from PyPI) for the
three local `distilgpt2` configs; its README shows the CPU-only install.

```bash
cd steganographic-collusion && uv sync
uv run bash scripts/ci_check.sh                          # no key: compiles + 11 detector tests, ~25 s
uv run python -m auditor.experiments auction_adaptive     # no key: one scripted experiment, ~25 s
uv run python -m auditor.experiments pricing_llm_gemini   # inference set centrally: NEBIUS_API_KEY in repo-root compute.env (see COMPUTE.md); 20 seeds x 8 rounds = 160 calls, ~4 min, cents
```

### 10. [`trace-guard/`](trace-guard/)

**Question:** does scoring an agent's reasoning and actions on several structured
dimensions catch what a single "is this suspicious?" check misses?

TraceGuard, built at the Apart x Redwood AI Control hackathon (March 2026). It scores each
agent action on five dimensions in parallel (goal alignment, constraint adherence, reasoning
coherence, safety awareness, action-trace consistency) with tiered deeper analysis and
heuristic detectors. It is a **single-agent** monitor, not a collusion detector; sprint
project 2.2 reuses its action-trace consistency rubric as one of the monitor views compared
on the trust-breaking logs.

**Prerequisite: Docker.** The agent runs inside ControlArena's BashArena, which executes
every task in a container and builds several images on first setup (a few GB, some
minutes). This is the only project in the repo that needs more than Python and a key.
Budget for it: the 5-sample smoke test takes ~10 minutes; the full experiment 6–8 hours.
`scripts/quickstart.sh` checks every prerequisite (Docker, images, key, model access)
and stops at the first one missing, before anything expensive starts.

```bash
cd trace-guard && uv sync     # uv sync (not `uv pip install`): it uses uv.lock's pinned deps; inference set centrally in repo-root compute.env (OPENROUTER_API_KEY — defaults are closed GPT-4o; see COMPUTE.md)
bash scripts/quickstart.sh             # checks prereqs (Docker, key, model access), builds BashArena images on first run, then a 5-sample smoke test (~10 min)
```

### 11. [`redteaming/`](redteaming/)

**Question:** what misaligned behavior was this model trained to have, and how does it
implement it?

Dolos-1.5B is a fine-tune of Qwen2.5-1.5B-Instruct that Poseidon Research trained to have a
specific misaligned behavior. You get only the weights: there is no paper and no training code,
and we don't say what the behavior is. Talk to the model, compare it with the original Qwen,
and look inside its weights and activations. The three tasks are to find the behavior, explain
the mechanism, and design an audit that would find it without a reference model; the
[folder README](redteaming/README.md) has the details. We don't know how hard the behavior is
to find, or which methods work best. AI agents we tried did not find it in one attempt.

Unlike the other folders this runs the model locally, so no API key or `compute.env` is needed.
The weights are about 6.2 GB and download on first run; CUDA, Apple Silicon (MPS) and CPU all work.
The architecture is unchanged from Qwen2.5-1.5B-Instruct, so Transformers, TransformerLens and
NNsight can load it.

```bash
cd redteaming
uv run --locked generate.py --prompt "Explain why the ocean has tides in three sentences."
uv run --locked generate.py --model both --prompt "..."   # the same prompt to Dolos and to Qwen2.5-1.5B-Instruct
```
