SHELL=/bin/bash
VENV=venv
PYTHON=$(VENV)/bin/python
PIP=$(VENV)/bin/pip
.DEFAULT_GOAL=_help

# [ENUM] Styling / Colors
STYLE_CYAN := $(shell tput setaf 6 2>/dev/null || echo -e "\033[36m")
STYLE_RESET := $(shell tput sgr0 2>/dev/null || echo -e "\033[0m")

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

.PHONY: install
install: $(VENV)/bin/activate ##H Install standard dependencies
	$(PIP) install -r requirements.txt

.PHONY: install-dev
install-dev: $(VENV)/bin/activate ##H Install development dependencies
	$(PIP) install -r requirements-dev.txt

.PHONY: run
run: install ##H Run the application
	$(PYTHON) matrix_premid.py

.PHONY: format
format: install-dev ##H Format the code using Black
	$(VENV)/bin/black matrix_premid.py

.PHONY: lint
lint: install-dev ##H Lint the code using Flake8
	$(VENV)/bin/flake8 matrix_premid.py

.PHONY: clean
clean: ##H Clean the virtual environment and caches
	rm -rf $(VENV)
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf .mypy_cache

.PHONY: _help
_help: ##H Show this help, list available targets
	@grep -hE '^[a-zA-Z0-9_\/-]+:.*?##H .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?##H "}; {printf "$(STYLE_CYAN)%-20s$(STYLE_RESET) %s\n", $$1, $$2}'
