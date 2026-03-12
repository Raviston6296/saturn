.PHONY: install test lint run gates-test clean

# Install all dependencies for development
install:
	pip install -e ".[legacy]"
	pip install pytest ruff

# Run the full test suite
test:
	python -m pytest tests/ -v

# Run linting with ruff
lint:
	ruff check .

# Run Saturn server locally
run:
	python main.py

# Run only the gates subsystem tests (integration tests)
gates-test:
	python -m pytest tests/test_gates_integration.py -v

# Remove Python cache files
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
