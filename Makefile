.PHONY: dev test eval seed demo lint
dev:
	docker compose -f infra/docker-compose.yml up --build
test:
	pytest -q
eval:  ## all evaluations, then regenerate tables
	python -m evals.runners.rag_eval && python -m evals.runners.decision_eval && python -m evals.runners.gigsim_outcomes
seed:
	python -m rag.ingest.run --all-personas
demo:
	python -m agent.graphs.bid_graph --demo
lint:
	ruff check .

results:
	python tools/results_tables.py > docs/RESULTS.md
	python tools/fill_readme.py
