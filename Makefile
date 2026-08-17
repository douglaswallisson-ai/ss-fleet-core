# Makefile for Fleet Management Platform

.PHONY: help build up down logs shell migrate test clean

help: ## Show this help message
	@echo "Fleet Management Platform - Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: ## Build Docker images
	docker-compose build

up: ## Start all services
	docker-compose up -d
	@echo "Services started. API available at http://localhost:8000"
	@echo "Grafana available at http://localhost:3000 (admin/admin)"

down: ## Stop all services
	docker-compose down

logs: ## View logs from all services
	docker-compose logs -f

logs-api: ## View API logs only
	docker-compose logs -f api

shell: ## Open shell in API container
	docker-compose exec api bash

db-shell: ## Open PostgreSQL shell
	docker-compose exec postgres psql -U fleet_user -d fleet_db

migrate: ## Run database migrations
	docker-compose exec api alembic upgrade head

migrate-create: ## Create new migration
	@read -p "Enter migration message: " msg; \
	docker-compose exec api alembic revision --autogenerate -m "$$msg"

test: ## Run tests
	docker-compose exec api pytest

test-cov: ## Run tests with coverage
	docker-compose exec api pytest --cov=app --cov-report=html

clean: ## Remove all containers, volumes and images
	docker-compose down -v --rmi all

restart: ## Restart all services
	docker-compose restart

ps: ## Show running containers
	docker-compose ps

health: ## Check services health
	@curl -s http://localhost:8000/health | python -m json.tool
