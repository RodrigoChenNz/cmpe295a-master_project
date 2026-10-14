# --env-file is explicit because compose otherwise looks for .env beside the
# compose file (infra/.env), not at the repo root where .env.example tells you
# to put it. Without this the API comes up with no bootstrap password and
# nobody can sign in — a confusing failure for a five-character fix.
#
# `make up` runs the real system on an empty database: no sample rows, nothing
# emulated. `make demo` (or `make up DEMO=1`) layers on docker-compose.demo.yml:
# the sample catalogue, in its own database volume, plus the fake publisher
# standing in for Layers 1-4.
BASE := docker compose --env-file .env -f infra/docker-compose.yml
DEMO_FILE := -f infra/docker-compose.demo.yml
ifeq ($(DEMO),1)
COMPOSE := $(BASE) $(DEMO_FILE)
else
COMPOSE := $(BASE)
endif
# Targets that stop or inspect the stack see both, so they work whichever mode
# it was started in.
COMPOSE_ALL := $(BASE) $(DEMO_FILE)

.DEFAULT_GOAL := help
.PHONY: help up demo fresh fresh-demo down restart logs ps nuke psql tap smoke fake validate seed env

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if it does not exist yet
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }

up: env ## Start the real stack on an empty database (DEMO=1 for sample data)
	@# --remove-orphans stops the fake publisher when switching back from demo.
	$(COMPOSE) up -d --build --remove-orphans
	@$(COMPOSE) ps

demo: ## Start the stack with sample data and the fake publisher (Layers 1-4)
	@$(MAKE) --no-print-directory up DEMO=1

# Compose prefixes volume names with the project name (`name:` in the compose file).
PG_VOLUME := retail-pricing_postgres-data
PG_DEMO_VOLUME := retail-pricing_postgres-demo-data
ifeq ($(DEMO),1)
FRESH_VOLUME := $(PG_DEMO_VOLUME)
else
FRESH_VOLUME := $(PG_VOLUME)
endif

fresh: ## Wipe the real database and start again from first-run setup (DEMO=1: the demo one)
	@# Everything you add persists across down/up/restart; this is the only
	@# target besides nuke that deletes it. Only the one database volume goes —
	@# the other mode's database and the broker's data are left alone.
	$(COMPOSE_ALL) down --remove-orphans
	docker volume rm -f $(FRESH_VOLUME)
	@$(MAKE) --no-print-directory up DEMO=$(DEMO)

fresh-demo: ## Wipe the demo database and start the demo again from the seed
	@$(MAKE) --no-print-directory fresh DEMO=1

seed: ## Regenerate infra/postgres/03-seed.sql from the design's sample catalogue
	python3 tools/gen_seed.py
	@echo "run 'make nuke demo' to apply it — the seed only runs on an empty volume"

down: ## Stop the stack, keep data (it persists until `make fresh`)
	$(COMPOSE_ALL) down --remove-orphans

restart: down up ## Stop then start

logs: ## Follow logs from every container
	$(COMPOSE_ALL) logs -f

ps: ## Show container status
	$(COMPOSE_ALL) ps

nuke: ## Stop and DELETE all volumes (wipes both the real and demo databases)
	$(COMPOSE_ALL) down -v --remove-orphans

psql: ## Open a psql shell on the dev database
	$(COMPOSE_ALL) exec postgres psql -U retail -d retail_pricing

tap: ## Print every message on the retail/ topic tree
	$(COMPOSE_ALL) exec mosquitto mosquitto_sub -h localhost -t 'retail/#' -v

fake: ## Follow the fake publisher's output (it runs by itself under `make demo`)
	$(COMPOSE_ALL) logs -f fake-publisher

validate: ## Check the JSON Schemas themselves are well-formed
	@for f in contracts/*.schema.json; do \
		python3 -c "import json,sys; json.load(open('$$f'))" && echo "ok   $$f" || echo "FAIL $$f"; \
	done

smoke: ## End-to-end check: publish a price, assert it lands downstream
	uv run tools/smoke.py
