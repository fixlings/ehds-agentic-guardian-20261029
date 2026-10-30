# EHDS Track 3: Health Data Navigator

Health data access body for the AIM Austria EHDS hackathon (Track 3): a request comes in, an AI model and fixed rules check it, the app decides, cites the law, and returns privacy-protected numbers or opens a secure workspace.

| Metric (official `requests/expected.json`) | Result |
|---|---|
| Correct decisions | 10/10 (rules alone and with AI check) |
| Correct numbers | 6/6 |
| Privacy leaks | 0 |
| Consistency | 10/10 |
| Slowest request | < 1 s rules only, ~3 s with AI check |

Reproduce with `make eval` (rules only, offline).

## How a request is handled

```
Request JSON ──► AI check (purpose, question; no patient data) ─┐
             └─► Rules (ehds/judge.py) ──────────────────────────┴─► stricter of the two
                                                                     │
         REJECT (Art. 54 forbidden use / Art. 53 no allowed purpose) ┤
         APPROVE_STATISTICAL (Art. 53, 69) ── SQL from definitions ──┤─► "<10" privacy gate ─► numbers
         APPROVE_SPE (Art. 53, 66, 68, 73) ── secure workspace, download blocked
                                     all steps ─► Staff desk + hash-chained audit log
```

- **The AI can only make a decision stricter.** It can reject a forbidden use (only if it quotes the words from the request) or downgrade records to statistics when no real reason is given. It can never approve what the rules reject. If the model is down, the rules decide alone and staff see that.
- **Patient data never goes to the AI.** It only sees organisation, purpose, question and request type. Opted-out patients are removed (`ehds/data.py`) before any calculation.
- **Privacy:** counts below 10 are `<10`, small percentages/means `suppressed`, every requested group shown, halves round up.
- **Test requests** `requests/demo/D01–D04` (insurer fees, pharma sales reps, "does my neighbour have diabetes", records without a reason) are in the sample list.

## Run it

Mac/Linux:
```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
make app                 # http://127.0.0.1:8000
```
Windows (PowerShell):
```powershell
py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn navigator_app:app --port 8000
```
AI check: set `OPENROUTER_API_KEY` before starting (optional `EHDS_LLM_MODEL`, default `mistralai/mistral-small-3.2-24b-instruct`; `EHDS_AI_CHECK=0` turns it off). OpenRouter is **not EU-only**; requests ask for providers that do not store prompts. Without a key the app runs on rules only.

Tests: `make test` (offline, network blocked). Video script: `docs/video_script.md`.

## Known limitations

- Roles are self-declared; no logins.
- Subtracting two answers to reveal a hidden small group is not fully blocked.
- The older chat API (`POST /v1/agent/request`, below) still exists for the colleagues' tests but is not in the UI.

## Appendix: guardian module (original notes)

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
