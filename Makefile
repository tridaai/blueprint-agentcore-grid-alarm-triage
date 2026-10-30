# Trida AI Blueprint — Grid Alarm Triage & Field Work Agent
# on AWS Bedrock AgentCore
#
# Primary workflow is uv (https://docs.astral.sh/uv/):
#   make install     uv sync --extra dev
#   make demo        planted streams end to end, offline (no keys)
#   make test        pytest
#   make evals       eval gate, offline (SQLite stack)
#   make serve       the AgentCore Runtime app on http://localhost:8080
#   make api         the operations API on http://localhost:8000
#   make invoke      local in-process invoke of the agent step
#
# No uv? python3 -m venv .venv && source .venv/bin/activate, then:
#   make install-pip

.PHONY: install install-pip demo test evals reports-check ledger-check quarantine-check reviews-check topology-check policy-check serve api invoke

install:
	uv sync --extra dev

install-pip:
	# The frozen set first, then the package itself with --no-deps: pip
	# refuses an editable install under a hashed constraints file, so the
	# two steps never mix.
	pip install -r constraints.txt
	pip install --no-deps -e .
	pip install "pytest==9.1.1" "httpx==0.28.1"

demo:
	uv run grid-agent-demo

test:
	uv run pytest -q

evals:
	uv run python evals/run_evals.py

reports-check:
	uv run python scripts/check_reports.py

ledger-check:
	uv run python scripts/check_model_ledger.py

quarantine-check:
	uv run python scripts/check_quarantine.py

reviews-check:
	uv run python scripts/check_reviews.py

topology-check:
	uv run python scripts/check_topology.py

policy-check:
	uv run python scripts/check_policy.py

serve:
	uv run grid-agent-serve

api:
	uv run uvicorn grid_triage.api:app --port 8000

invoke:
	uv run grid-agent-invoke
