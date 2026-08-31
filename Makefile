# Uses .venv/bin/python when a venv exists in the project, system python3 otherwise.
PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.PHONY: install run test reset

install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt

run:
	$(PY) app.py

test:
	$(PY) -m pytest tests/ -q

reset:
	$(PY) scripts/reset_demo.py
