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
install: deps ##H Install dependencies, binary to /usr/local/bin, and systemd service (requires sudo)
	sudo cp matrix_premid.py /usr/local/bin/matrix_premid
	sudo chmod +x /usr/local/bin/matrix_premid
	sudo cp etc/matrix-premid.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable matrix-premid.service
	@echo "Binary, dependencies, and service installed. Run 'sudo systemctl start matrix-premid.service' to start it."

.PHONY: run
run: deps ##H Run the application
	$(PYTHON) matrix_premid.py

.PHONY: format
format: ##H Format the code using Black
	$(VENV)/bin/black matrix_premid.py
	$(VENV)/bin/isort matrix_premid.py
	-prettier -w .
	-pre-commit run --all-files

.PHONY: lint
lint: ##H Lint the code using Flake8
	$(VENV)/bin/flake8 matrix_premid.py

.PHONY: clean
clean: ##H Clean the virtual environment and caches
	rm -rf $(VENV)
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf .mypy_cache

.PHONY: restart
restart: ##H Restart the background systemd service
	sudo systemctl restart matrix-premid.service

.PHONY: _help
_help: ##H Show this help, list available targets
	@grep -hE '^[a-zA-Z0-9_\/-]+:.*?##H .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?##H "}; {printf "$(STYLE_CYAN)%-15s$(STYLE_RESET) %s\n", $$1, $$2}'
