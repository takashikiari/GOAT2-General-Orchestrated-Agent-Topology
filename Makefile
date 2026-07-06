# GOAT 2.0 — Developer shortcuts
# Usage: make <target>

.PHONY: run setup reconfigure test update rollback clean lint

run:
	bash run.sh

setup:
	pip install -r setup/requirements.txt
	python3 setup/wizard.py

reconfigure:
	python3 setup/wizard.py --reconfigure

test:
	python3 -m pytest tests/ -v

test-fast:
	python3 -m pytest tests/ -q

update:
	python3 setup/updater.py

rollback:
	python3 setup/rollback.py

checks:
	python3 setup/checks.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

lint:
	python3 -m ruff check . --fix
	python3 -m mypy . --ignore-missing-imports
