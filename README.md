# EHDS Track 3 agentic guardian

Bolt-on natural-language module for a deterministic EHDS statistics engine. This repository is the whole system: interpreter, evidence investigator, and purpose guardian. It does not connect to a production health record system.

The shared contract is in `guardian/contracts` and `OWNERSHIP.md`. `POST /v1/agent/request` is specified there. The HTTP app is not wired yet.

Synthetic fixtures only. The runtime language model, once connected, is Mistral on `https://api.eu.mistral.ai`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Status: contracts published; component packages are in progress. Do not treat this checkout as demo-ready.
