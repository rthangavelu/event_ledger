# Convenience targets for the AI-assisted SDLC workflow.
PYTHON ?= python

.PHONY: help install test test-unit test-functional coverage reports \
        run-account run-gateway docker-up docker-down clean

help:
	@echo "install         Install dev + runtime dependencies"
	@echo "test            Run the full test suite"
	@echo "test-unit       Run unit tests only"
	@echo "test-functional Run functional/integration tests only"
	@echo "coverage        Run all tests with combined coverage (terminal + HTML + XML)"
	@echo "reports         Generate unit + functional test/coverage reports into reports/"
	@echo "docker-up       Build and start both services with Docker Compose"
	@echo "docker-down     Stop the Docker Compose stack"

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest

test-unit:
	$(PYTHON) -m pytest tests/unit

test-functional:
	$(PYTHON) -m pytest tests/functional

coverage:
	$(PYTHON) -m pytest \
		--cov=common --cov=gateway --cov=account_service \
		--cov-report=term-missing \
		--cov-report=html:reports/htmlcov \
		--cov-report=xml:reports/coverage.xml

# Produces the deliverable artifacts: separate unit + functional test reports
# (HTML) and coverage reports (XML), plus a combined HTML coverage report.
reports:
	mkdir -p reports
	$(PYTHON) -m pytest tests/unit \
		--cov=common --cov=gateway --cov=account_service \
		--cov-report=xml:reports/coverage-unit.xml \
		--html=reports/unit-tests.html --self-contained-html
	$(PYTHON) -m pytest tests/functional \
		--cov=common --cov=gateway --cov=account_service \
		--cov-report=xml:reports/coverage-functional.xml \
		--html=reports/functional-tests.html --self-contained-html
	$(PYTHON) -m pytest \
		--cov=common --cov=gateway --cov=account_service \
		--cov-report=term --cov-report=html:reports/htmlcov \
		--cov-report=xml:reports/coverage.xml

run-account:
	uvicorn account_service.main:app --port 8001 --reload

run-gateway:
	ACCOUNT_SERVICE_URL=http://localhost:8001 uvicorn gateway.main:app --port 8000 --reload

docker-up:
	docker compose up --build

docker-down:
	docker compose down -v

clean:
	rm -rf .pytest_cache htmlcov .coverage reports/htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
