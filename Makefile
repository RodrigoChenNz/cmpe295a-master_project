COMPOSE := docker compose -f infra/docker-compose.yml

.DEFAULT_GOAL := help
.PHONY: help up down restart logs ps nuke psql tap smoke fake validate

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

up: ## Start the stack (mosquitto + postgres, plus services as they land)
	$(COMPOSE) up -d
	@$(COMPOSE) ps

down: ## Stop the stack, keep data
	$(COMPOSE) down

restart: down up ## Stop then start

logs: ## Follow logs from every container
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

nuke: ## Stop and DELETE all volumes (wipes the database)
	$(COMPOSE) down -v

psql: ## Open a psql shell on the dev database
	$(COMPOSE) exec postgres psql -U retail -d retail_pricing

tap: ## Print every message on the retail/ topic tree
	$(COMPOSE) exec mosquitto mosquitto_sub -h localhost -t 'retail/#' -v

fake: ## Run the fake publisher against the local broker
	uv run tools/fake_publisher.py

validate: ## Check the JSON Schemas themselves are well-formed
	@for f in contracts/*.schema.json; do \
		python3 -c "import json,sys; json.load(open('$$f'))" && echo "ok   $$f" || echo "FAIL $$f"; \
	done

smoke: ## End-to-end check: publish a price, assert it lands downstream
	uv run tools/smoke.py
