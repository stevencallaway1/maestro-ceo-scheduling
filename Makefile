# Uses .venv/bin/python when a venv exists in the project, system python3 otherwise.
PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.PHONY: run test reset install record

run:
	$(PY) app.py

test:
	$(PY) -m pytest tests/ -v

reset:
	$(PY) scripts/reset_demo.py

install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Dev-only: records demo_backup.webm (needs `pip install -r requirements-dev.txt`
# and `playwright install chromium`). Resets demo state afterwards because the
# scripted run performs a live override.
record:
	$(PY) scripts/record_demo.py
	$(PY) scripts/reset_demo.py
