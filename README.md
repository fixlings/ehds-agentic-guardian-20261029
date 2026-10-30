<div align="center">

# Health Data Navigator

**The health data access desk that sees the leak others miss.**

Decisions in seconds · the law cited · patients protected, even against clever combinations of questions

`10/10 decisions` · `6/6 numbers` · `0 privacy leaks` on the official test

<sub>AIM Austria hackathon · Track 3 · European Health Data Space (EHDS)</sub>

</div>

---

## Why Health Data Navigator stands out

Most data-access tools judge each answer on its own. Ours also thinks like an attacker and like a scientist.

### 1. It catches the leak others miss

Every access body hides groups smaller than 10. But two harmless-looking questions can still expose a patient:

<p align="center"><img src="docs/img/differencing-leak.svg" width="520" alt="72 minus 71 exposes one patient"></p>

A standard tool releases both answers. Our **Guardian** remembers everything it has already published, spots the overlap, and holds the second answer back.

### 2. It notices when the purpose changes

A team approved for research returns asking for records to set insurance premiums, a use EHDS law forbids. The Guardian connects the two requests, refuses the second and alerts staff.

### 3. It tells researchers what the numbers cannot prove

*"Does obesity cause breast cancer?"* In our data, every BMI value of these women was recorded after the diagnosis, so the numbers cannot show cause. Our **Evidence Investigator** adds this kind of guidance to every answer, so research starts on solid ground.

---

## How it works

<p align="center"><img src="docs/img/flow.svg" width="720" alt="Request, Guardian, Calculate, Guardian, Evidence, Answer"></p>

Every request ends in one of three outcomes, each backed by the relevant EHDS article:
- **rejected**;
- **anonymous statistics**;
- **a secure workspace**, where approved researchers can analyse coded records and nothing can be downloaded.

Staff see every step, can review rejections, and every action is written to a tamper-evident audit log.

---

## The two agents

**Guardian: is the request legitimate, and is the answer safe?**
It checks each request twice:
- **Before calculating:** is the purpose allowed, and does it match earlier requests?
- **Before releasing:** are any groups too small, and could this answer be combined with a previous one?

An AI model reviews each request like an experienced officer, but it can only make a decision *stricter*, never looser. The privacy checks themselves are fixed rules, so every run gives the same result.

**Evidence Investigator: does the data support the conclusion?**
It explains what an answer can show, what it cannot (such as cause and effect), and which analysis would be stronger. It sees only the protected result, never patient data, and it never changes the answer.

---

## Results on the official test

| Check | Result |
|---|---|
| Correct decisions | **10 / 10** |
| Correct numbers | **6 / 6** |
| Privacy leaks | **0** |
| Same answer on every run | **10 / 10** |
| Answer time (limit 60 s) | **under 1 s** without AI, about 7–16 s with AI |

---

## Where we go next

- **Logins and roles:** staff actions are open in this demo.
- **A shared, permanent memory for the Guardian:** today it is per organisation and resets on restart.
- **Broader language understanding:** some unusual wordings are not yet understood. Staff see a warning flag, but the numbers can be wrong.
- **More workspace tools:** today database queries only; SAS, R and Python are planned.

---

<details>
<summary><b>For technical readers: architecture, components, how to run</b></summary>

### Run it

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

Checks: `make eval` (the official metrics), `make test` (all tests, offline, with the AI replaced by a fake model), `make lint`.

### Decisions and articles

| Decision | When | Articles |
|---|---|---|
| `REJECT` | Forbidden use, or no allowed purpose | Art. 54 / Art. 53 |
| `APPROVE_STATISTICAL` | Allowed purpose, aggregate question | Art. 53, 69 |
| `APPROVE_SPE` | Allowed purpose, and a concrete reason why statistics are not enough | Art. 53, 66, 68, 73 |

### Components

| Component | File | What it does |
|---|---|---|
| Request form | `web/` | Dropdowns and ready-made definition lines instead of JSON. "Start from" loads any official or demo request. |
| Fixed rules | `ehds/judge.py` | Art. 54 forbidden uses, Art. 53 purposes, Art. 68 records justification. Picks the decision and the articles. |
| Guardian: AI moderation | `ehds/ai_check.py` | Reviews the application and the organisation's history (no patient data). Can only make a decision stricter. A high-risk finding counts only with an exact quote. |
| Calculation | `ehds/recipe.py`, `ehds/engine.py`, `ehds/reference.py` | Plain-English definitions → SQL on DuckDB, plus an independent Python recount. Nothing is released if they disagree. |
| Privacy gate | `ehds/privacy.py`, `ehds/data.py` | Opted-out patients removed before any calculation. `<10` and `suppressed`, with round half up. |
| Guardian: release memory | `ehds/memory.py` | Patient groups behind every released number. Holds back answers that differ from an earlier one by 1–9 patients. |
| Evidence Investigator | `ehds/evidence.py` | Fixed caveats plus an optional AI review of the protected result. Advisory only. |
| Secure workspace | `ehds/workspace.py` | Per-permit DuckDB file, read-only queries, summary-only output. The download endpoint always answers 403. |
| API, review, history | `server/api.py` | HTTP API, manual review, request history |
| Audit, certificate | `ehds/audit.py`, `ehds/certificate.py` | Hash-chained audit log and a result certificate |

### Models and data

- **Language model:** `mistralai/mistral-small-3.2-24b-instruct` via OpenRouter (`server/llm.py`). Change it with `EHDS_LLM_MODEL`.
- **What the model sees:** only the application text and the protected result table, never patient-level data.
- **Without a key,** the app runs on the fixed rules alone, and all the official metrics are reached that way.
- **Database:** DuckDB in-process on the official `track3-ehds-20k` synthetic CSVs (20,000 patients; opted-out patients excluded).

### Demo requests

- `R01–R10`: the official requests.
- `D01–D05`: reworded bad requests (insurer fees, pharma sales reps, "does my neighbour have diabetes", records without a reason, an app company's "research").
- `D06 → D07 → D08`: an age-band subtraction is held back, then a change of purpose to insurance is rejected.
- `D09`: a justified records request that opens a secure workspace.
- `D10 → D11`: 114 women with breast cancer in Austria, then "without Burgenland". The second answer is held back.

Run D06–D08 and D10–D11 in order, without restarting the app.

### Known technical limitations

- **No authentication.** Roles are self-declared, and the staff endpoints, including "allow" after review, are open.
- **Release memory** is keyed by organisation name and held in RAM. It doesn't catch sums over many requests or exclusions other than regions. When a count is held back, a mean in the same answer can still be shown.
- **Parser gaps** for some new wordings (stays per year, "active" prescriptions, an age given only in a definition line) can produce wrong numbers, with only a staff flag shown.
- **Timing:** with the AI on, requests typically take 7–16 s (slowest seen 38 s). The rules alone take under 1 s.

</details>
