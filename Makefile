SHELL=/bin/bash
.DEFAULT_GOAL=_help

# [ENUM] Styling / Colors
STYLE_CYAN := $(shell tput setaf 6 2>/dev/null || echo -e "\033[36m")
STYLE_RESET := $(shell tput sgr0 2>/dev/null || echo -e "\033[0m")

# Default virtual environment
VENV=.venv
PYTHON=$(VENV)/bin/python
PIP=$(VENV)/bin/pip

.PHONY: init
init:	##H Initialize .venv virtual dev env
	python3 -m venv $(VENV)
	-direnv allow

.PHONY: deps
deps:	##H Install standard and dev dependencies
	$(VENV)/bin/pip install -r requirements.txt -r requirements-dev.txt

# Default install location
INSTALL_DIR ?= /opt/matrix-premid

.PHONY: install
install: ##H Install dependencies, env, binary, and systemd service to /opt (requires sudo)
	@if [ ! -f .env ]; then \
		echo "$(STYLE_CYAN)Error: .env file not found!$(STYLE_RESET)"; \
		echo "Please copy .env.example to .env and configure your credentials first."; \
		exit 1; \
	fi
	@echo "Installing globally to $(INSTALL_DIR)..."
	sudo mkdir -p $(INSTALL_DIR)
	sudo cp matrix_premid.py requirements.txt $(INSTALL_DIR)/
	sudo cp .env $(INSTALL_DIR)/.env
	sudo chown $$(id -un):$$(id -gn) $(INSTALL_DIR)/.env
	sudo chmod 600 $(INSTALL_DIR)/.env
	sudo python3 -m venv $(INSTALL_DIR)/.venv
	sudo $(INSTALL_DIR)/.venv/bin/pip install -r $(INSTALL_DIR)/requirements.txt
	sudo ln -sf $(INSTALL_DIR)/matrix_premid.py /usr/local/bin/matrix_premid
	sudo chmod +x $(INSTALL_DIR)/matrix_premid.py
	sudo cp etc/matrix-premid.service /etc/systemd/system/matrix-premid.service
	sudo systemctl daemon-reload
	sudo systemctl enable matrix-premid.service
	@echo "Installed to $(INSTALL_DIR) and service created."


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Unit tests and local running
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.PHONY: test
test:	##H Run unit tests with coverage
	PYTHONPATH=. $(VENV)/bin/python -m pytest --cov=matrix_premid --cov-report=term-missing tests/

.PHONY: run
run:	##H Run the application locally
	$(PYTHON) matrix_premid.py --debug

.PHONY: restart
restart: ##H Restart the background systemd service
	sudo systemctl restart matrix-premid.service

.PHONY: log
log:	##H Watch journalctl logs of installed/running service
	sudo journalctl -fu matrix-premid

.PHONY: stop
stop: ##H Stop the background systemd service
	sudo systemctl stop matrix-premid.service


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Linting, formatting
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


LINT_LOCS_PY = $$(git ls-files '*.py')

.PHONY: format
format: ##H Format the code using Black
	$(VENV)/bin/black $(LINT_LOCS_PY)
	$(VENV)/bin/isort $(LINT_LOCS_PY)
	-prettier -w .
	-pre-commit run --all-files


.PHONY: lint
lint: ##H Lint the code using Flake8
	flake8 matrix_premid.py
	flake8 --max-line-length=100 tests/
	pylint $(LINT_LOCS_PY)
	ruff check $(LINT_LOCS_PY)

.PHONY: clean
clean: ##H Clean the virtual environment and caches
	rm -rf $(VENV)
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf .mypy_cache

.PHONY: _help
_help: ##H Show this help, list available targets
	@grep -hE '^[a-zA-Z0-9_\/-]+:.*?##H .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?##H "}; {printf "$(STYLE_CYAN)%-15s$(STYLE_RESET) %s\n", $$1, $$2}'
