# FloodGuard India — SIH PS 26161
# Works with GNU make. On Windows use Git Bash or WSL.

PYTHON      ?= python
PIP         ?= $(PYTHON) -m pip
SCENARIO    ?= tehri_bhagirathi
BACKEND_DIR := backend
FRONTEND_DIR:= frontend

.DEFAULT_GOAL := help

.PHONY: help setup setup-pip setup-conda data preprocess validate simulate demo test lint clean audit

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

setup: setup-pip  ## Install everything (pip path; use setup-conda if you have conda)

setup-pip:  ## Install Python deps via pip + frontend deps via npm
	$(PIP) install -r requirements.txt
	$(PIP) install -e $(BACKEND_DIR)
	cd $(FRONTEND_DIR) && npm install

setup-conda:  ## Create the conda env (gets ANUGA + GDAL cleanly)
	conda env create -f environment.yml || conda env update -f environment.yml
	@echo "Now: conda activate floodguard && make setup-pip"

data:  ## Fetch + cache all input layers for SCENARIO (default: tehri_bhagirathi)
	$(PYTHON) -m floodguard.cli data --scenario data/scenarios/$(SCENARIO).yaml

preprocess:  ## Condition DEM, build grid, reservoir curve, cross-sections
	$(PYTHON) -m floodguard.cli preprocess --scenario data/scenarios/$(SCENARIO).yaml

validate:  ## Run Ritter/Stoker/lake-at-rest/mass-balance -> docs/validation/
	$(PYTHON) -m floodguard.cli validate --out docs/validation

simulate:  ## Headless end-to-end run for SCENARIO
	$(PYTHON) -m floodguard.cli simulate --scenario data/scenarios/$(SCENARIO).yaml

demo:  ## Start backend + frontend on the precomputed demo bundle
	$(PYTHON) -m floodguard.cli demo

serve-backend:  ## Run the API only
	cd $(BACKEND_DIR) && $(PYTHON) -m uvicorn app.main:app --reload --port 8000

serve-frontend:  ## Run the Vite dev server only
	cd $(FRONTEND_DIR) && npm run dev

test:  ## Run the test suite
	$(PYTHON) -m pytest $(BACKEND_DIR)/tests -v

lint:  ## Ruff + tsc
	$(PYTHON) -m ruff check $(BACKEND_DIR)
	cd $(FRONTEND_DIR) && npm run typecheck

audit:  ## Clone reference repos and regenerate the audit scaffold
	$(PYTHON) scripts/clone_references.py

clean:  ## Remove derived artefacts (never touches data/raw)
	rm -rf data/processed/* outputs/* runs/* .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
