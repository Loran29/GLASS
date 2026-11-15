.PHONY: setup run test help

ifeq ($(OS),Windows_NT)
VENV_BIN := .venv/Scripts
else
VENV_BIN := .venv/bin
endif
VENV_PYTHON := $(VENV_BIN)/python
VENV_PIP    := $(VENV_BIN)/pip
VENV_STREAMLIT := $(VENV_BIN)/streamlit
VENV_PYTEST := $(VENV_BIN)/pytest

help:
	@echo "Available targets:"
	@echo "  make setup   — create venv, install deps, copy .env template"
	@echo "  make run     — start the Streamlit app"
	@echo "  make test    — run the test suite"

setup:
	python -m venv .venv
	$(VENV_PIP) install --upgrade pip
	$(VENV_PIP) install -r requirements.txt
	$(VENV_PYTHON) -c "import os, shutil; os.path.exists('goal_to_parameters/.env') or shutil.copy('goal_to_parameters/.env.example', 'goal_to_parameters/.env')"

run:
	$(VENV_STREAMLIT) run goal_to_parameters/app.py

test:
	$(VENV_PYTEST) tests/
