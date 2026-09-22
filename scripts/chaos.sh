#!/usr/bin/env bash
# Fault injection against the mock providers. `off` re-syncs the mock-llm
# release, so recovery is "reconcile back to what git declares" rather than a
# second hand-edit. Helm 4 upgrades with server-side apply, so the fields
# `kubectl set env` took over need --force-conflicts to be handed back.
set -euo pipefail

k() { kubectl --context "${KUBE_CONTEXT:?}" "$@"; }
target="${TARGET:-mock-openai}"

case "${1:-}" in
  errors)
    rate="${ERROR_RATE:-0.8}"
    k -n llm-mocks set env "deployment/${target}" MOCK_ERROR_RATE="${rate}" MOCK_ERROR_STATUS=500 >/dev/null
    echo "${target}: ${rate} of requests now fail with 500 (gateway should cool it down and fall back)"
    ;;
  ratelimit)
    k -n llm-mocks set env "deployment/${target}" MOCK_ERROR_RATE="${ERROR_RATE:-0.5}" MOCK_ERROR_STATUS=429 >/dev/null
    echo "${target}: now rate limiting with 429"
    ;;
  slow)
    k -n llm-mocks set env "deployment/${target}" MOCK_LATENCY_MS="${LATENCY_MS:-4000}" >/dev/null
    echo "${target}: time to first byte is now ~${LATENCY_MS:-4000}ms"
    ;;
  off)
    helmfile -e "${ENV:-local}" sync --selector name=mock-llm --skip-deps --sync-args --force-conflicts >/dev/null
    echo "mock providers reconciled back to the declared state"
    ;;
  *)
    echo "usage: chaos.sh errors|ratelimit|slow|off   (TARGET=mock-openai|mock-anthropic)" >&2
    exit 2
    ;;
esac
k -n llm-mocks rollout status "deployment/${target}" --timeout=120s >/dev/null
