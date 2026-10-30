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
Request JSON ──► AI moderation (whole request; no patient data) ┐
             └─► Rules (ehds/judge.py) ──────────────────────────┴─► stricter of the two
                                                                     │
         REJECT (Art. 54 forbidden use / Art. 53 no allowed purpose) ┤
         APPROVE_STATISTICAL (Art. 53, 69) ── SQL from definitions ──┤─► "<10" privacy gate ─► numbers
         APPROVE_SPE (Art. 53, 66, 68, 73) ── secure workspace, download blocked
                                     all steps ─► Staff desk + hash-chained audit log
```

- **AI moderation (`ehds/ai_check.py`).** A language model reads the whole application (organisation, country, purpose, question, definitions, requested data) and judges: forbidden use (Art. 54), allowed purpose (Art. 53), and six risk checks: does the question fit the purpose, is the purpose credible for this organisation, hidden commercial motive, could the answer single out people, data minimisation, are records justified. Each check is low / medium / high with a finding and a quote.
- **The AI can only make a decision stricter.** A high-risk finding rejects (Art. 54 forbidden use or re-identification, Art. 53 purpose mismatch) or downgrades records to statistics, but only if its quote is really in the request. Medium findings and unquoted findings go to staff as flags. It never approves what the rules reject. If the model is down, the rules decide alone and staff see that.
- **Patient data never goes to the AI.** Only the application text, without ids or the applicant's name. The engine (`ehds/`) has no network imports; only `server/llm.py` talks to the model. Opted-out patients are removed (`ehds/data.py`) before any calculation.
- **Privacy:** counts below 10 are `<10`, small percentages/means `suppressed`, every requested group shown, halves round up.
- **Test requests** `requests/demo/D01–D05` (insurer fees, pharma sales reps, "does my neighbour have diabetes", records without a reason, an app company whose "research" is really a product launch) are in the sample list.

## Layout

| Path | What |
|---|---|
| `ehds/` | Engine: rules (`judge.py`), AI moderation (`ai_check.py`), definitions → SQL, privacy gate, double computation, secure workspace, audit log, certificate, evaluation |
| `server/` | HTTP API (`api.py`) and the OpenRouter client (`llm.py`) |
| `web/` | Page: Submit request, Staff desk |
| `requests/`, `data/` | Official Track 3 requests and data; `requests/demo/` test requests |
| `tests/engine/` | All tests, network blocked |

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

Tests: `make test` (offline, network blocked; the AI is replaced by a fake model). Video script: `docs/video_script.md`.

## Known limitations

- Roles are self-declared; no logins.
- Subtracting two answers to reveal a hidden small group is not fully blocked.
- The AI's judgement can vary slightly between models; the official metrics are measured with rules only (`make eval`) and checked again with the AI on.
