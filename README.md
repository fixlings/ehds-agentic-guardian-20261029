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

Status: rescue integration on `rescue/ehds-integration-20261030`. Synthetic data only. Do not connect this demo to a production record system.
