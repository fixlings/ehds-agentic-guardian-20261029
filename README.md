# EHDS Track 3 agentic guardian

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
