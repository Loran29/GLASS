.PHONY: setup run test help

VENV_PYTHON := .venv/Scripts/python
VENV_PIP    := .venv/Scripts/pip
VENV_STREAMLIT := .venv/Scripts/streamlit
VENV_PYTEST := .venv/Scripts/pytest

help:
	@echo "Available targets:"
	@echo "  make setup   — create venv, install deps, copy .env template"
	@echo "  make run     — start the Streamlit app"
	@echo "  make test    — run the test suite"

setup:
	python -m venv .venv
	$(VENV_PIP) install --upgrade pip
	$(VENV_PIP) install -r requirements.txt
	@if not exist goal_to_parameters\.env copy goal_to_parameters\.env.example goal_to_parameters\.env

run:
	$(VENV_STREAMLIT) run goal_to_parameters/app.py

test:
	$(VENV_PYTEST) tests/
