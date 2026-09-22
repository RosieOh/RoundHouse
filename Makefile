SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

ENV ?= local
CLUSTER := llm-roundhouse
export KUBE_CONTEXT := kind-$(CLUSTER)
LITELLM_VERSION := $(shell awk '$$1 == "version:" {print $$2; exit}' environments/$(ENV).yaml)
KUBECTL := kubectl --context $(KUBE_CONTEXT)

.PHONY: help
help: ## Show targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: doctor
doctor: ## Check local tools and Docker resources
	@./scripts/doctor.sh

.PHONY: up
up: cluster secrets chart deploy urls ## Create the cluster and deploy everything

.PHONY: cluster
cluster: ## Create the kind cluster (idempotent)
	@kind get clusters 2>/dev/null | grep -qx $(CLUSTER) || kind create cluster --config cluster/kind.yaml --wait 120s

.PHONY: secrets
secrets: ## Create namespaces and bootstrap secrets once
	@./scripts/bootstrap-secrets.sh

.PHONY: chart
chart: ## Vendor the LiteLLM chart that matches LITELLM_VERSION
	@./scripts/fetch-litellm-chart.sh $(LITELLM_VERSION)

.PHONY: deploy
deploy: ## helmfile sync every release
	helmfile -e $(ENV) sync

.PHONY: diff
diff: ## Show what deploy would change (needs the helm-diff plugin)
	helmfile -e $(ENV) diff

.PHONY: smoke
smoke: ## End-to-end check: key, chat, stream, embeddings, spend, metrics
	@./scripts/smoke.sh

.PHONY: load
load: ## k6 load through Traefik (RATE=12 DURATION=3m)
	@RATE=$(or $(RATE),12) DURATION=$(or $(DURATION),3m) ./scripts/load.sh

.PHONY: chaos-errors chaos-ratelimit chaos-slow chaos-off
chaos-errors: ## Make mock-openai fail with 500 (ERROR_RATE=0.8 TARGET=mock-openai)
	@./scripts/chaos.sh errors
chaos-ratelimit: ## Make mock-openai answer 429
	@./scripts/chaos.sh ratelimit
chaos-slow: ## Make mock-openai slow (LATENCY_MS=4000)
	@./scripts/chaos.sh slow
chaos-off: ## Reconcile the mock providers back to the declared state
	@./scripts/chaos.sh off

.PHONY: validate
validate: chart ## Lint charts, check alert rules, render every release
	@for c in charts/*/; do helm lint --quiet "$$c" >/dev/null && echo "  ok    helm lint $$c"; done
	@docker run --rm -v "$(CURDIR)/charts/litellm-observability/rules:/rules:ro" --entrypoint promtool \
	  quay.io/prometheus/prometheus:v3.7.0 check rules /rules/litellm.yaml >/dev/null && echo "  ok    promtool check rules"
	@helmfile -e $(ENV) template --skip-deps >/dev/null 2>&1 && echo "  ok    helmfile template (all releases render)"

.PHONY: publish-charts
publish-charts: ## Push every chart to GHCR as an OCI artifact (helm registry login ghcr.io first)
	@./scripts/publish-charts.sh

.PHONY: status
status: ## Pods, HPA and CNPG cluster state
	@$(KUBECTL) get pods -A -o wide --sort-by=.metadata.namespace | grep -v -E "kube-system|local-path-storage"
	@echo
	@$(KUBECTL) -n litellm get hpa,cluster.postgresql.cnpg.io

.PHONY: urls
urls: ## Print endpoints and credentials
	@./scripts/urls.sh

.PHONY: down
down: ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)
