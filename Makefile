# --env-file is explicit because compose otherwise looks for .env beside the
# compose file (infra/.env), not at the repo root where .env.example tells you
# to put it. Without this the API comes up with no bootstrap password and
# nobody can sign in — a confusing failure for a five-character fix.
COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml

.DEFAULT_GOAL := help
.PHONY: help up down restart logs ps nuke psql tap smoke fake validate seed env

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if it does not exist yet
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }

up: env ## Start the stack (mosquitto, postgres, dashboard-api, dashboard-web)
	$(COMPOSE) up -d --build
	@$(COMPOSE) ps

seed: ## Regenerate infra/postgres/03-seed.sql from the design's sample catalogue
	python3 tools/gen_seed.py
	@echo "run 'make nuke up' to apply it — the seed only runs on an empty volume"

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

fake: ## Stand in for Layers 1-4: publish proposals, explanations and label sightings
	@# Runs inside the API image, which already has aiomqtt and asyncpg, so this
	@# works on a machine with no local Python environment at all.
	$(COMPOSE) run --rm --no-deps \
	  -e MQTT_HOST=mosquitto \
	  -e DATABASE_URL=postgresql://retail:retail_dev_only@postgres:5432/retail_pricing \
	  -v "$(CURDIR)/tools:/srv/tools:ro" \
	  --entrypoint python dashboard-api tools/fake_publisher.py

validate: ## Check the JSON Schemas themselves are well-formed
	@for f in contracts/*.schema.json; do \
		python3 -c "import json,sys; json.load(open('$$f'))" && echo "ok   $$f" || echo "FAIL $$f"; \
	done

smoke: ## End-to-end check: publish a price, assert it lands downstream
	uv run tools/smoke.py
