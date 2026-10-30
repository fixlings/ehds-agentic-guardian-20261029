# EHDS Track 3 agentic guardian

Bolt-on natural-language module for a deterministic EHDS statistics engine. This repository is the whole system: interpreter, evidence investigator, and purpose guardian. It does not connect to a production health record system.

The shared contract is in `guardian/contracts` and `OWNERSHIP.md`. `POST /v1/agent/request` is specified there. The HTTP app is not wired yet.

Synthetic fixtures only. The runtime language model, once connected, is Mistral on `https://api.eu.mistral.ai`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python scripts/demo_three_scenarios.py
```

On Windows PowerShell, use `.venv\Scripts\python.exe` for the same commands. The three-scenario demo is offline and does not call Mistral. The spoken script is `scripts/demo_90s.md`.

Live EU Mistral is optional: install the package, set `MISTRAL_API_KEY` without printing it, and run `pytest -m live`. The default HTTP app stays mocked. `create_app(use_env_model=True)` uses `https://api.eu.mistral.ai` only.

Status: rescue integration on `rescue/ehds-integration-20261030`. Synthetic data only. Do not connect this demo to a production record system.
