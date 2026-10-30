# EHDS Track 3: Health Data Navigator

One app with two ways in and the same rules behind both, built for the AIM Austria EHDS hackathon (Track 3).

| Metric (official `requests/expected.json`) | Result |
|---|---|
| Correct decisions | 10/10 |
| Correct numbers | 6/6 |
| Privacy leaks | 0 |
| Consistency | 10/10 |
| Slowest request | < 1 s |

Reproduce with `make eval` (or `python -m ehds.evaluate`).

## Architecture

```
Browser (web/)                      FastAPI (navigator_app.py)
 ├─ Submit request  ──►  POST /v1/requests ──► ehds/ rules engine
 │                                              judge → recipe → SQL (DuckDB) → privacy gate
 │                                              or → secure workspace (download blocked)
 │                                              + independent double-check, certificate, sealed audit log
 ├─ Ask a question  ──►  POST /v1/agent/request ──► guardian/
 │                                              interpreter (offline lexicon or LLM proposal, validated)
 │                                              → purpose guardian (rules) → evidence check
 │                                              → Track3QueryExecutor on the same 20,000 patients
 └─ Staff desk      ──►  decision trace, generated SQL, workspace, GET /v1/audit
```

- **Rules decide, AI never does.** The formal route uses no language model. In the chat route a model may only *propose* a structured query; local code validates it, and deterministic rules decide.
- **Patient data stays inside.** Only the chat question text can go to a model. Opted-out patients are removed in `ehds/data.py` before any query.
- **Track 3 privacy rules everywhere:** counts below 10 are `<10`, small percentages/means are `suppressed`, every requested group is shown (even empty), halves round up. The chat API no longer returns the exact total population.
- **Legal articles:** the engine cites EHDS (Regulation (EU) 2025/327) articles 53, 54, 66, 68, 69, 73; the chat guardian attaches the same Art. 53/69 or Art. 54 citation.
- **Not hard-coded:** queries are generated from each request's definitions (`ehds/recipe.py` → `ehds/engine.py`); plain-English terms map to ICD-10/ATC/LOINC in `ehds/vocab.py`.

| Folder | What it does |
|---|---|
| `ehds/` | Track 3 rules engine (from HackathonMedTech): judge, recipe, SQL engine, privacy gate, workspace, reference check, certificate, audit log, evaluation |
| `guardian/` | Natural-language guardian: interpreter, purpose rules, evidence investigator, API |
| `guardian/evidence/track3_executor.py` | Connects the guardian to the official data via the `QueryExecutor` port |
| `guardian/api/requests_api.py` | Formal routes: `/v1/requests`, `/v1/workspaces/{permit}/query|download`, `/v1/audit` |
| `web/` | Health Data Navigator UI (Submit request, Ask a question, Staff desk) |
| `data/`, `requests/` | Official Track 3 synthetic data and requests |

## Run it

Python 3.11+.

```bash
# Mac / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make eval          # five metrics
make test          # all tests, network blocked for the engine tests
make app           # open http://127.0.0.1:8000
```

```powershell
# Windows PowerShell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m ehds.evaluate
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m uvicorn navigator_app:app --port 8000
```

Video script: `docs/video_script.md`.

### Live language model (optional)

The chat works offline by default (word lists, no model). To let a model read the chat questions:

| Provider | Settings | Data location |
|---|---|---|
| Mistral EU (default) | `EHDS_USE_LIVE_MODEL=1`, `MISTRAL_API_KEY` | EU only (`api.eu.mistral.ai`) |
| OpenRouter (hackathon key) | `EHDS_USE_LIVE_MODEL=1`, `EHDS_LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, optional `EHDS_LLM_MODEL` (default `mistralai/mistral-small-3.2-24b-instruct`) | **Not EU-only.** Requests ask OpenRouter for providers that don't store prompts (`data_collection: deny`) |

Either way only the question text, stated purpose and role are sent; patient data never leaves the app, and the model's proposal is validated locally before rules decide. Keys are read from the environment only, never from the browser or files.

## Known limitations

- Roles are self-declared; there are no real logins.
- Small groups are hidden, but not every cross-query differencing attack (72 − 71 = 1) is blocked yet.
- The offline chat lexicon covers breast cancer, BMI, diabetes and a few codes; the formal route covers the full Track 3 vocabulary.

---

## Guardian module (original notes)

Bolt-on natural-language module for a deterministic EHDS statistics engine. This repository is the whole system: interpreter, evidence investigator, and purpose guardian. It does not connect to a production health record system.

The shared contract is in `guardian/contracts` and `OWNERSHIP.md`. `POST /v1/agent/request` is the HTTP route.

Synthetic fixtures only. The only runtime language model is Mistral `mistral-small-2603` on `https://api.eu.mistral.ai`. There is no other provider.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python scripts/demo_three_scenarios.py
```

On Windows PowerShell, use `.venv\Scripts\python.exe` for the same commands. The spoken script is `scripts/demo_90s.md`.

Offline and live are different runs:

- Offline, the default: the lexicon normalizer builds the query. No model is called. `live_model` is false. `scripts/demo_three_scenarios.py` without flags stays on this path.
- Live: `scripts/demo_three_scenarios.py --live` sends the question to EU Mistral `mistral-small-2603`. Local code validates the JSON proposal against the lexicon and schema, then the deterministic Purpose Guardian, evidence check, and synthetic aggregate run as usual. A failed, malformed, or non-EU call returns HTTP 503 and does not authorize an aggregate. `MISTRAL_API_KEY` is read from the environment and must not be printed. `pytest -m live` skips only when that variable is absent; with a key, an API or model failure fails the test.

## Live API on localhost

The frontend calls this process. It does not send `MISTRAL_API_KEY`. Set the key in the same PowerShell session before starting the server, and do not paste it into the repo.

```powershell
.\.venv\Scripts\python.exe -m guardian.api.live
```

That listens on `127.0.0.1:8000`. It builds the existing app with `create_app(use_env_model=True)`, which uses Mistral `mistral-small-2603` at `https://api.eu.mistral.ai` only. If the key is missing, the process exits before it listens. The message does not include the variable's value. Importing the app without that flag stays offline and does not need the key.

## POST /v1/agent/request

`POST http://127.0.0.1:8000/v1/agent/request`. Body is JSON. Unknown keys are rejected. Do not send an API key.

Request (`AgentRequest`):

| Field | Type | Required |
| --- | --- | --- |
| `message` | string, 1–4000 characters | yes |
| `role` | `public_health_researcher`, `clinician`, `regulator`, `citizen`, `insurer`, `employer`, `marketer`, `unknown` | yes |
| `stated_purpose` | string, 1–1000 characters | yes |
| `session_id` | string or omitted | no |
| `dataset_id` | string, default `synthetic-breast-cancer-demo` | no |

Omit `session_id` on the first turn. The response `session_id` is the id to send on a follow-up. The server keeps that session in memory until the process exits and updates role, stated purpose, and turn history from the new body. An unknown `session_id` is HTTP 400.

Allow, reject, and clarify are HTTP 200 application decisions. The field is `decision`: `allow`, `reject`, or `clarify`. `policy.outcome` is only `allow` or `reject`; a clarify decision still carries the policy that permitted the turn. Reject does not execute an aggregate. Clarify means the question was not turned into a query.

Response (`AgentResponse`):

| Field | Type |
| --- | --- |
| `session_id` | string |
| `decision` | `allow`, `reject`, or `clarify` |
| `policy` | object: `outcome` (`allow` or `reject`), `rule_id`, `reason_code`, `purpose_category`, `purpose_changed`, `role`, `evidence` (string list), `article_mapping` (`status`, `citation`, `source_url`), `authoritative` (always true) |
| `interpretation` | object or null: `proposed_purpose`, `purpose_change_suspected`, `identity_disclosure_suspected`, `injection_suspected`, `needs_clarification`, `clarification_question`, `unresolved_terms`, `query`, `notes` |
| `evidence` | object or null: `supported_conclusions`, `unsupported_claims`, `missing_evidence`, `temporal_feasibility` (`sufficient_for_order`, `insufficient`, `not_applicable`), `association_supported`, `causation_supported` (always false), `checks_performed`, `tool_steps_used` |
| `aggregate` | object or null: `synthetic` (always true), `dataset_id`, `fixture_version`, `query_fingerprint`, `population_n`, `suppressed`, `rows`, `timing`, `notes` |
| `explanation` | string |
| `audit_id` | string |
| `limitations` | string list |
| `model` | string or null. `mistral-small-2603` only after a live proposal validates |
| `tool_steps` | integer |
| `latency_ms` | integer |
| `synthetic` | always true |
| `live_model` | boolean. False on the offline app |

`reason_code` is one of `allowed_public_health`, `purpose_forbidden_insurance`, `purpose_forbidden_employment`, `purpose_forbidden_marketing`, `purpose_forbidden_identity`, `role_forbidden`, `prompt_injection`, `needs_clarification`. `purpose_category` is one of `public_health_research`, `insurance`, `employment`, `marketing`, `identity_disclosure`, `unknown`.

HTTP errors use `ErrorBody`: `error`, `message`, `session_id`.

| Status | `error` | When |
| --- | --- | --- |
| 400 | `invalid_request` | Body is not JSON, not an object, or does not match the request model; unknown `session_id`; unknown `dataset_id` |
| 503 | `model_unavailable` | Live mode could not use EU Mistral. No aggregate |
| 504 | `timeout` | Request deadline exceeded |

A missing key does not produce one of these responses. The live command exits before listen. The offline app never reads the key.

Status: rescue integration on `rescue/ehds-integration-20261030`. Synthetic data only. Do not connect this demo to a production record system.
