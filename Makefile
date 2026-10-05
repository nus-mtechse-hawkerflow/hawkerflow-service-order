.PHONY: install lint test audit

install:
	pip install -r requirements-dev.txt

lint:
	ruff check .

test:
	pytest -q

audit:
	pip-audit -r requirements-dev.txt
