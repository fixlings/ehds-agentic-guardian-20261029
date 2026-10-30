# EHDS Track 3: Health Data Navigator

A Health Data Access Body for the AIM Austria EHDS hackathon (Track 3). An applicant submits a request in a simple form. A **Guardian** agent and fixed EHDS rules check it, and the app decides and cites the law. It then returns privacy-protected statistics or opens a secure workspace with no download. An **Evidence Investigator** explains what the numbers can and cannot show. Staff see every step and decide appeals.

Demo video script: [`docs/video_script.md`](docs/video_script.md) · 1-minute pitch: [`docs/pitch.md`](docs/pitch.md)

## Metric results (official `requests/expected.json`)

| Metric | Result |
|---|---|
| Correct decisions | 10/10 (rules alone, and with the AI check) |
| Correct numbers | 6/6 |
| Privacy leaks | 0 |
| Consistency (same request twice) | 10/10 |
| Slowest request | < 2 s rules only, ~3 s more with the AI check |

Reproduce offline with `make eval`.

## Architecture

```
            Applicant form (web/)                         Staff desk (web/)
                    │                                            ▲
                    ▼                                            │ every step + audit log
  ┌─ GUARDIAN, entry check ─────────────────────┐                │
  │ fixed rules (judge.py)   AI moderation      │                │
  │        └── stricter decision wins ──┘       │──► REJECT ──► letter + "request manual review"
  └─────────────────────────────────────────────┘                    (staff: allow / keep rejection)
                    │ APPROVE
                    ▼
  definitions → SQL (recipe.py, engine.py, DuckDB) + independent Python recount (must match)
                    │
  ┌─ GUARDIAN, exit check (fixed rule) ─────────┐
  │ <10 shown as "<10", small % / means hidden  │
  │ release memory: blocks subtraction tricks   │
  └─────────────────────────────────────────────┘
                    │
     statistics + EVIDENCE INVESTIGATOR note        or   secure workspace (no download)
```

### Components we built

| Component | File | What it does |
|---|---|---|
| Request form | `web/` | Dropdowns and ready-made definition lines instead of JSON. "Start from" loads any official or test request. |
| Fixed rules | `ehds/judge.py` | Forbidden uses (Art. 54: insurance pricing, marketing, hiring, credit, singling people out), allowed purpose (Art. 53), records only with a reason (Art. 68). Picks the decision `REJECT` / `APPROVE_STATISTICAL` / `APPROVE_SPE` and the articles. |
| Guardian: AI moderation | `ehds/ai_check.py` | A language model reads the whole application (no patient data) plus this organisation's earlier requests and released numbers. It judges forbidden use, purpose fit, credibility, hidden commercial motive, re-identification risk, data minimisation, records justification, and purpose changes or subtraction attempts. **It can only make a decision stricter:** a high-risk finding with an exact quote from the request rejects or downgrades; everything else goes to staff as a flag. Applicants see one short line; staff see the full checklist. |
| Calculation | `ehds/recipe.py`, `ehds/engine.py`, `ehds/reference.py` | Turns plain-English definitions into SQL on DuckDB, then recounts independently in Python. If the two disagree, nothing is released. |
| Privacy gate | `ehds/privacy.py`, `ehds/data.py` | Opted-out patients removed before any calculation. Counts below 10 shown as `<10`, small percentages, means and medians `suppressed`, every requested group shown. |
| Guardian: release memory | `ehds/memory.py` | Remembers each organisation's requests and the patient groups behind every released number. A new number whose group differs from an earlier one by 1–9 patients is held back. Understands "outside / without / except [region]". |
| Evidence Investigator | `ehds/evidence.py` | After protection: fixed caveats (counts are not causes, hidden groups). With the AI on, the model reads only the question and the protected table and says what the numbers can and cannot support. It cannot change numbers or decisions. |
| Secure workspace | `ehds/workspace.py` | Copies only the selected patients' requested records (pseudonymised). Read-only `SELECT`; only summary results leave. Raw rows and patient or encounter IDs are blocked. No download button; the download endpoint always answers 403. |
| Request history, manual review | `server/api.py`, `ehds/memory.py` | Applicants see their organisation's earlier requests and the numbers received, and can ask for manual review of a rejection. Staff allow it (all privacy checks still run) or keep the rejection, with a note that appears in the history. |
| Staff desk, audit | `web/`, `ehds/audit.py`, `ehds/certificate.py` | Searchable, filterable list of all requests. Each opens with the rule steps, the AI check, flags, generated SQL, double check and history. Every action goes into a hash-chained audit log. |

### Models and services

- **Language model:** `mistralai/mistral-small-3.2-24b-instruct` via **OpenRouter** (`server/llm.py`), used by the AI moderation and the Evidence Investigator. Set with `EHDS_LLM_MODEL`.
- **What leaves the app:** only the application text (moderation) and the protected result table (Evidence Investigator). Patient-level data never goes to the model. Requests ask for providers that do not store prompts; OpenRouter is not EU-only.
- **Without a key** the app runs on the fixed rules alone; all metrics above are reached that way.
- **Database:** DuckDB in-process on the official `track3-ehds-20k` CSVs. No other external services.

## Demo requests

- `requests/R01–R10`: official requests.
- `requests/demo/D01–D05`: reworded bad requests (insurer fees, pharma sales reps, "does my neighbour have diabetes", records without a reason, an app company's "research").
- `D06 → D07 → D08`: subtraction by age group is held back, then a purpose change to insurance is rejected.
- `D09`: a justified records request that opens a secure workspace.
- `D10 → D11`: 114 women with breast cancer in Austria, then "without Burgenland". The second answer (112) would reveal 2 women, so it is held back.

Run D06–D08 and D10–D11 in order without restarting the app.

## Run it

Mac/Linux (Python 3.11+):
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
make app                 # http://127.0.0.1:8000
```
Windows (PowerShell):
```powershell
py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn navigator_app:app --port 8000
```
Optional AI: set `OPENROUTER_API_KEY` before starting (`EHDS_AI_CHECK=0` turns it off). Never commit the key.

Checks: `make eval` (official metrics), `make test` (all tests, offline, the AI replaced by a fake model), `make lint`.

## What works and what does not

**Works:** all 10 official requests correct with numbers and articles; new requests from the form; rejection of reworded forbidden uses; small-cell protection and opt-outs; subtraction protection between an organisation's requests; secure workspace with no download; manual review with a staff decision; full staff trace and audit log.

**Not yet:**
- No logins: roles are self-declared, and the staff buttons are open to anyone using the app.
- History, Staff desk list and release memory are kept in memory and reset on restart.
- Subtraction protection compares pairs of answers from one organisation. It does not catch sums over many requests, collusion between organisations, or exclusions other than regions (e.g. "without hospital X").
- Workspace tools: SQL only. SAS, PSPP, R and Python are shown as planned.
- The live AI was tested with a stand-in model in our test suite; its judgement can vary between models. The rules alone reach the full official score.
