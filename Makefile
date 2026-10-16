.PHONY: up down ps logs smoke clean example-mine example-mine-stop install uninstall status endpoints upgrade upload-build generate-data copy-build unity-demo check-secrets check-public-host

# ---------------------------------------------------------------------------
# Docker Compose deployment (laptops, CI, contest judges): deploy/compose/
# ---------------------------------------------------------------------------
COMPOSE_BASE := docker compose -f deploy/compose/docker-compose.yml
# Once `make example-mine` has run, every compose command also loads the mine
# overlay, so `down` stops its simulator and `up` keeps its Grafana dashboard.
# `make clean` forgets it.
MINE_MARKER  := deploy/compose/.example-mine
MINE_COMPOSE := examples/mine/compose.yml
COMPOSE := $(COMPOSE_BASE)$(if $(wildcard $(MINE_MARKER)), -f $(MINE_COMPOSE))

## Start the whole platform with Docker Compose (first run: generates credentials)
up:
	@bash scripts/compose-env.sh
	@bash scripts/compose-preflight.sh
	$(COMPOSE) up -d --build --wait --wait-timeout 900
	@# `--wait` does not wait for one-shot jobs to finish: a running init counts
	@# as up. Wait for it to exit, then check how.
	@test "$$(docker wait $$($(COMPOSE) ps -a -q init))" = 0 || { \
	  echo "ERROR: the init job (policy + Ditto connections) failed:"; $(COMPOSE) logs init; exit 1; }
	@bash scripts/compose-wait-ready.sh
	@bash scripts/compose-urls.sh

## Stop the platform, keep data
down:
	$(COMPOSE) down

## Show compose service status
ps:
	@$(COMPOSE) ps -a

## Follow logs (one service: make logs S=gateway)
logs:
	$(COMPOSE) logs -f --tail=100 $(S)

## End-to-end check of the running compose stack
smoke:
	@bash scripts/compose-smoke.sh

## Run the Example Mine (twins + haul-cycle simulator + Grafana dashboard) on the compose stack
example-mine: up
	$(COMPOSE_BASE) -f $(MINE_COMPOSE) up -d --build --wait --wait-timeout 300
	@touch $(MINE_MARKER)
	@echo "Example Mine running: Grafana -> Dashboards -> OpenEgiz Examples -> Example Mine"

## Stop the Example Mine simulator (twins and data stay)
example-mine-stop:
	$(COMPOSE_BASE) -f $(MINE_COMPOSE) stop mine-simulator

## Stop the platform and DELETE all its data and credentials
clean:
	$(COMPOSE) down -v --remove-orphans
	rm -f deploy/compose/.env $(MINE_MARKER)

# ---------------------------------------------------------------------------
# Helm deployment (servers): the chart at the repository root
# ---------------------------------------------------------------------------
# Several values reference names derived from the release name (e.g. the
# telegraf configmap), so the release MUST be called "opentwins" until those
# references are made release-agnostic.
RELEASE_NAME := opentwins
NAMESPACE    := opentwins
CHART_PATH   := .

# Credentials live OUTSIDE this repo (rotated 2026-08-07). values.yaml carries
# only invalid placeholders, so install/upgrade must always be given this
# override file. Deliberately a hard failure rather than a silent fallback:
# deploying the placeholders would break Ditto auth and telemetry ingest.
SECRETS_FILE ?= $(HOME)/openegiz-deploy/secrets.values.yaml

# Address browsers use to reach this host; the Grafana app plugin calls Ditto
# and the extended API from the browser. Defaults to the first node's
# InternalIP; override with `make install PUBLIC_HOST=my-host.example`.
PUBLIC_HOST ?= $(shell kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)

check-public-host:
	@test -n "$(PUBLIC_HOST)" || { \
	  echo "ERROR: could not determine PUBLIC_HOST from kubectl."; \
	  echo "       Pass it explicitly: make install PUBLIC_HOST=<ip-or-hostname>"; \
	  exit 1; }

check-secrets:
	@test -f "$(SECRETS_FILE)" || { \
	  echo "ERROR: secrets override not found: $(SECRETS_FILE)"; \
	  echo "       It holds the Ditto/Grafana/InfluxDB credentials that are"; \
	  echo "       intentionally absent from values.yaml. Create it from"; \
	  echo "       secrets.values.yaml.example, or point to another file with:"; \
	  echo "       make install SECRETS_FILE=/path/to/file"; \
	  exit 1; }

## Install the OpenEgiz Helm chart
install: check-secrets check-public-host
	helm install $(RELEASE_NAME) $(CHART_PATH) -n $(NAMESPACE) --create-namespace -f "$(SECRETS_FILE)" --set grafanaPlugin.publicHost=$(PUBLIC_HOST) --wait --timeout=15m --debug

## Upgrade the OpenEgiz Helm chart
upgrade: check-secrets check-public-host
	helm upgrade $(RELEASE_NAME) $(CHART_PATH) -n $(NAMESPACE) -f "$(SECRETS_FILE)" --set grafanaPlugin.publicHost=$(PUBLIC_HOST) --wait --timeout=15m --debug

## Uninstall the OpenEgiz Helm chart
uninstall:
	helm uninstall $(RELEASE_NAME) -n $(NAMESPACE) --wait

## Show pod statuses
status:
	@kubectl get pods -n $(NAMESPACE) -o wide

## Show all service endpoints (IP + port)
endpoints:
	@bash scripts/show-endpoints.sh

## Upload Unity WebGL build files to the nginx pod
upload-build:
	@bash scripts/upload-build.sh $(RELEASE_NAME)

## Oven demo: create the oven twins, then stream telemetry (Ctrl+C to stop).
## Compose by default; Helm: make generate-data MQTT_PORT=30511 DITTO_URL=http://localhost:30525 DITTO_PASSWORD=...
generate-data:
	@bash scripts/demo-oven.sh

## Download the demo Unity WebGL scene (88 MB, kept out of git) into ./build/
unity-demo:
	@bash scripts/unity-demo.sh

## Copy 4 Unity WebGL build files from SRC into ./build/
## Usage: make copy-build SRC=/path/to/source
copy-build:
	@bash scripts/copy-build.sh $(SRC)

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

## Static checks, same as the CI lint job: run before pushing
.PHONY: lint
lint:
	helm lint . -f secrets.values.yaml.example --set grafanaPlugin.publicHost=127.0.0.1
	helm template opentwins . -n opentwins -f secrets.values.yaml.example \
	  --set grafanaPlugin.publicHost=127.0.0.1 > /dev/null
	git ls-files -z '*.sh' | xargs -0 -n1 bash -n
	git ls-files -z 'scripts/*.sh' 'deploy/*.sh' | xargs -0 shellcheck --severity=warning
	git ls-files -z '*.py' | xargs -0 python3 -c 'import ast, sys; [ast.parse(open(f, encoding="utf-8").read(), f) for f in sys.argv[1:]]'
	@bash scripts/compose-env.sh
	docker compose -f deploy/compose/docker-compose.yml config -q
	@echo "lint: ok"

# ---------------------------------------------------------------------------
# Bozshakol mine model (mine/): twins, simulator and web page on the platform
# ---------------------------------------------------------------------------
.PHONY: mine mine-check mine-down mine-logs mine-test mine-params

MINE_STACK := $(COMPOSE_BASE) -f mine/compose.yml
# The platform is started from the base file alone, so the Example Mine is not
# started with it even where `make example-mine` was run before: its marker is
# pointed at a file that never exists.
MINE_PLATFORM_UP := $(MAKE) --no-print-directory up MINE_MARKER=deploy/compose/.not-used
# The mine image is built without a provenance record: a rebuild from cache
# then yields the very same image, so repeating `make mine` leaves the running
# simulator alone instead of recreating it. (`up --build` would also rebuild
# the platform's own image, which `up` has just built.)
MINE_BUILD := docker build -q --provenance=false -t bozshakol/mine:local mine
MINE_UP := { $(MINE_BUILD) && $(MINE_STACK) up -d --wait --wait-timeout 300; }

## Start the platform and the Bozshakol mine model, check that the stand is ready, print its address.
## Safe to repeat; a failed start of either part is retried once. The readiness
## check waits generously and fails only if twins, telemetry, history or the page
## are missing; the functional round trips are checked by `make mine-check`.
mine:
	@command -v curl >/dev/null || { echo "ERROR: curl is required: sudo apt-get install -y curl"; exit 1; }
	@$(MINE_PLATFORM_UP) || { \
	  echo "The platform did not start; trying once more in 20 s ..."; sleep 20; $(MINE_PLATFORM_UP); }
	@$(MINE_UP) || { echo "The mine services did not start; trying once more in 10 s ..."; sleep 10; $(MINE_UP); }
	@bash mine/tests/wait_ready.sh
	@bash mine/tests/ready_check.sh
	@bash -c 'set -a; . deploy/compose/.env; echo; echo "Bozshakol mine model is running: http://$${OPENEGIZ_PUBLIC_HOST:-localhost}:$${MINE_UI_PORT:-8088}"'

## End-to-end check of the running mine stand: twins, history, page, and the round trips of
## the modules, the scenarios, the forecast and the advisor
mine-check:
	@bash mine/tests/stand_check.sh

## Stop the mine and the platform, keep data (`make clean` deletes everything)
mine-down:
	$(MINE_STACK) down

## Follow logs of the mine services (one service: make mine-logs S=mine-sim)
mine-logs:
	$(MINE_STACK) logs -f --tail=100 $(or $(S),mine-twins mine-sim mine-dispatch mine-grade mine-plant mine-maint mine-advisor mine-ui)

## Unit tests of the twin generator, the simulator, the scenarios, the modules, the forecasts and the advisor, with 7-day runs (needs Docker only).
## docs/mine is mounted because the tests compare docs/mine/parameters.md with params.yaml.
mine-test:
	$(MINE_BUILD)
	docker run --rm -v "$(CURDIR)/docs/mine:/app/docs/mine:ro" bozshakol/mine:local python -m unittest discover -s mine/tests -t .

## Regenerate docs/mine/parameters.md from mine/model/params.yaml
mine-params:
	$(MINE_BUILD)
	docker run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR)/docs/mine:/app/docs/mine" bozshakol/mine:local python -m mine.model.build_params_doc --write
