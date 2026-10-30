PY ?= .venv/bin/python

.PHONY: install eval test lint app validate stress

install:
	python3 -m venv .venv && $(PY) -m pip install -e ".[dev]"

eval:            ## the five official Track 3 metrics
	$(PY) -m ehds.evaluate

test:            ## all tests offline (live LLM test skips without a key)
	env -u OPENROUTER_API_KEY $(PY) -m pytest -q

lint:
	$(PY) -m ruff check ehds server tests navigator_app.py

app:             ## Health Data Navigator on http://127.0.0.1:8000
	$(PY) -m uvicorn navigator_app:app --host 127.0.0.1 --port 8000

validate:
	$(PY) -m ehds.validate

stress:
	$(PY) -m ehds.stress 1000
