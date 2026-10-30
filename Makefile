PY ?= .venv/bin/python

.PHONY: install eval test lint app demo validate stress

install:
	python3 -m venv .venv && $(PY) -m pip install -e ".[dev]"

eval:            ## the five official Track 3 metrics
	$(PY) -m ehds.evaluate

test:            ## all tests offline (live LLM test skips without a key)
	env -u MISTRAL_API_KEY $(PY) -m pytest -q

lint:
	$(PY) -m ruff check ehds tests/engine guardian/evidence/track3_executor.py guardian/evidence/routing.py guardian/api/requests_api.py

app:             ## Health Data Navigator on http://127.0.0.1:8000
	$(PY) -m uvicorn navigator_app:app --host 127.0.0.1 --port 8000

demo:
	$(PY) scripts/demo_three_scenarios.py

validate:
	$(PY) -m ehds.validate

stress:
	$(PY) -m ehds.stress 1000
