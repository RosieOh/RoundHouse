#!/usr/bin/env bash
# Creates the namespaces and the secrets the releases reference, once.
#
# Existing secrets are never rotated: the master key and salt key encrypt
# credentials stored in Postgres, so regenerating them would orphan that data.
# In a shared cluster these come from a secret manager (External Secrets
# Operator, Vault, SOPS) instead of this script.
set -euo pipefail

ctx="${KUBE_CONTEXT:?KUBE_CONTEXT is required}"
k() { kubectl --context "${ctx}" "$@"; }

rand() { openssl rand -hex "${1:-24}"; }

ensure_namespace() {
  local ns="$1" enforce="$2"
  k create namespace "${ns}" --dry-run=client -o yaml | k apply -f - >/dev/null
  k label namespace "${ns}" --overwrite \
    "pod-security.kubernetes.io/enforce=${enforce}" \
    "pod-security.kubernetes.io/warn=restricted" \
    "pod-security.kubernetes.io/audit=restricted" >/dev/null
}

ensure_secret() {
  local ns="$1" name="$2"
  shift 2
  if k -n "${ns}" get secret "${name}" >/dev/null 2>&1; then
    echo "secret ${ns}/${name} exists, keeping it"
    return
  fi
  k -n "${ns}" create secret generic "${name}" "$@" >/dev/null
  echo "secret ${ns}/${name} created"
}

# Workloads we own run under the restricted Pod Security Standard.
ensure_namespace litellm restricted
ensure_namespace llm-mocks restricted
# node-exporter needs host namespaces, so monitoring can only be privileged.
ensure_namespace monitoring privileged

ensure_secret litellm litellm-master-key --from-literal=master-key="sk-$(rand)"
ensure_secret litellm litellm-env --from-literal=LITELLM_SALT_KEY="sk-$(rand 32)"
ensure_secret litellm valkey-users --from-literal=password="$(rand)"
ensure_secret monitoring grafana-admin --from-literal=admin-user=admin --from-literal=admin-password="$(rand 12)"
