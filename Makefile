SHELL=/bin/bash
VENV=.venv
PYTHON=$(VENV)/bin/python
PIP=$(VENV)/bin/pip
.DEFAULT_GOAL=_help

# [ENUM] Styling / Colors
STYLE_CYAN := $(shell tput setaf 6 2>/dev/null || echo -e "\033[36m")
STYLE_RESET := $(shell tput sgr0 2>/dev/null || echo -e "\033[0m")

.PHONY: init
init:
	python3 -m venv $(VENV)
	-direnv allow

.PHONY: deps
deps: $(VENV)/bin/activate ##H Install standard and dev dependencies
	$(PIP) install -r requirements-dev.txt

.PHONY: install
install: deps ##H Install dependencies and systemd service (requires sudo)
	sed -e "s|WorkingDirectory=.*|WorkingDirectory=$(shell pwd)|" \
	    -e "s|EnvironmentFile=-.*|EnvironmentFile=-$(shell pwd)/.env|" \
	    -e "s|ExecStart=.*|ExecStart=$(shell pwd)/$(VENV)/bin/python $(shell pwd)/matrix_premid.py|" \
	    etc/matrix-premid.service > .tmp_service
	sudo cp .tmp_service /etc/systemd/system/matrix-premid.service
	rm .tmp_service
	sudo systemctl daemon-reload
	sudo systemctl enable matrix-premid.service
	@echo "Service installed. Run 'sudo systemctl start matrix-premid.service' to start it."

.PHONY: run
run: deps ##H Run the application
	$(PYTHON) matrix_premid.py

.PHONY: format
format: ##H Format the code using Black
	$(VENV)/bin/black matrix_premid.py
	$(VENV)/bin/isort matrix_premid.py
	-prettier -w .
	-pre-commit run --all-files


LINT_LOCS_PY = $$(git ls-files '*.py')

.PHONY: lint
lint: ##H Lint the code using Flake8
	$(VENV)/bin/flake8 $(LINT_LOCS_PY)
	$(VENV)/bin/pylint $(LINT_LOCS_PY)
	$(VENV)/bin/ruff $(LINT_LOCS_PY)

.PHONY: clean
clean: ##H Clean the virtual environment and caches
	rm -rf $(VENV)
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf .mypy_cache

.PHONY: restart
restart: ##H Restart the background systemd service
	sudo systemctl restart matrix-premid.service

.PHONY: stop
stop: ##H Stop the background systemd service
	sudo systemctl stop matrix-premid.service

.PHONY: _help
_help: ##H Show this help, list available targets
	@grep -hE '^[a-zA-Z0-9_\/-]+:.*?##H .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?##H "}; {printf "$(STYLE_CYAN)%-15s$(STYLE_RESET) %s\n", $$1, $$2}'
