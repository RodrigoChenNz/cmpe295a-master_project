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

validate: ## Validate schemas and example payloads
	$(PYTHON) tools/validate_contracts.py

smoke: ## End-to-end check: publish a price, assert it lands downstream
	$(PYTHON) tools/smoke.py

PYTHON ?= .venv/bin/python
SOFTWARE := $(BASE) -f infra/docker-compose.software.yml

software: env ## Start simulator, forecasting, pricing, console and device emulator
	$(SOFTWARE) up -d --build --remove-orphans

software-down: env ## Stop the complete software stack and preserve its data
	$(SOFTWARE) down

software-logs: env ## Follow complete software stack logs
	$(SOFTWARE) logs -f

test: ## Run unit tests (database tests run when TEST_DATABASE_URL is set)
	$(PYTHON) -m pytest -q

integration: ## Start isolated test infrastructure and run real database and MQTT tests
	docker compose -f infra/docker-compose.verify.yml up -d --wait
	TEST_DATABASE_URL=postgresql://retail:retail_test_only@localhost:15432/retail_pricing_test $(PYTHON) -m pytest -q

train: ## Train DQN and DDPG and export held-out comparisons
	$(PYTHON) -m retail train --algorithm dqn --output artifacts/dqn.zip
	$(PYTHON) -m retail train --algorithm ddpg --output artifacts/ddpg.zip
	$(PYTHON) -m retail evaluate --dqn artifacts/dqn.zip --ddpg artifacts/ddpg.zip

forecast: ## Evaluate mean, seasonal, Prophet and LSTM forecasts
	$(PYTHON) -m retail forecast --methods mean,seasonal,prophet,lstm

secure-identities: ## Create a private local CA and role-specific MQTT certificates
	$(PYTHON) tools/setup_tls.py

software-secure: env ## Start complete software with MQTT mTLS and role ACLs
	$(SOFTWARE) -f infra/docker-compose.secure.yml up -d --build --remove-orphans

smoke-secure: ## Verify authenticated approval and render over mutual TLS
	MQTT_PORT=8883 MQTT_TLS=true MQTT_CA_FILE=secrets/certs/ca.pem MQTT_CERT_FILE=secrets/certs/pricing.pem MQTT_KEY_FILE=secrets/certs/pricing.key $(PYTHON) tools/smoke.py
