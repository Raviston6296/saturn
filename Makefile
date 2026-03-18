.PHONY: install test lint run gates-test goose-test clean

# Install all dependencies for development
install:
	pip install -e ".[legacy]"
	pip install pytest ruff pyyaml

# Install Goose CLI (open-source AI coding agent by Block)
install-goose:
	curl -fsSL https://github.com/block/goose/releases/latest/download/goose-installer.sh | bash
	@echo "Goose installed. Set LLM_PROVIDER=goose in saturn.env to use it."

# Run the full test suite
test:
	python -m pytest tests/ -v

# Run only the gates subsystem tests (integration tests)
gates-test:
	python -m pytest tests/test_gates_integration.py -v

# Run Goose-related tests
goose-test:
	python -m pytest tests/test_gates_integration.py -v -k "Goose"

# Run linting with ruff
lint:
	ruff check .

# Run Saturn server locally
run:
	python main.py

# Run gates against the current workspace (for manual validation)
# Usage: make validate-gates WORKSPACE=/path/to/zdpas
validate-gates:
	./validate_gates.sh $(WORKSPACE)

# Remove Python cache files
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
