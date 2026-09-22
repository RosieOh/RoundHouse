#!/usr/bin/env bash
# End-to-end check of the path a client actually takes: Traefik -> backend
# (mint a virtual key) -> gateway (chat, stream, embeddings, fallback) ->
# Postgres (spend) -> metrics sidecar -> Prometheus.
set -euo pipefail

base="${LITELLM_URL:-http://localhost:8080}"
prom="${PROMETHEUS_URL:-http://localhost:9090}"
k() { kubectl --context "${KUBE_CONTEXT:?}" "$@"; }
master_key="$(k -n litellm get secret litellm-master-key -o jsonpath='{.data.master-key}' | base64 -d)"

pass() { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; exit 1; }

api() {
  local key="$1" path="$2" body="${3:-}"
  if [[ -n "${body}" ]]; then
    curl -sS --fail-with-body -H "Authorization: Bearer ${key}" -H 'Content-Type: application/json' \
      -d "${body}" "${base}${path}"
  else
    curl -sS --fail-with-body -H "Authorization: Bearer ${key}" "${base}${path}"
  fi
}

echo "smoke test against ${base}"

curl -sS --fail "${base}/health/readiness" | jq -e '.status == "healthy" or .status == "connected"' >/dev/null \
  && pass "gateway readiness" || fail "gateway readiness"

virtual_key="$(api "${master_key}" /key/generate \
  '{"models": ["gpt-mock", "claude-mock", "embed-mock"], "max_budget": 1, "duration": "1h", "key_alias": "smoke-'"$(date +%s)"'"}' \
  | jq -r .key)"
[[ "${virtual_key}" == sk-* ]] && pass "backend minted a virtual key" || fail "virtual key generation"

reply="$(api "${virtual_key}" /v1/chat/completions \
  '{"model": "gpt-mock", "messages": [{"role": "user", "content": "smoke test"}]}')"
jq -e '.choices[0].message.content | test("mock-openai")' <<<"${reply}" >/dev/null \
  && pass "chat completion routed to mock-openai" || fail "chat completion: ${reply}"

chunks="$(curl -sS -N --fail-with-body -H "Authorization: Bearer ${virtual_key}" -H 'Content-Type: application/json' \
  -d '{"model": "claude-mock", "stream": true, "messages": [{"role": "user", "content": "stream please"}]}' \
  "${base}/v1/chat/completions" | grep -c '^data: {' || true)"
(( chunks > 2 )) && pass "streaming returned ${chunks} SSE chunks" || fail "streaming returned ${chunks} chunks"

api "${virtual_key}" /v1/embeddings '{"model": "embed-mock", "input": ["a", "b"]}' \
  | jq -e '.data | length == 2' >/dev/null && pass "embeddings" || fail "embeddings"

api "${virtual_key}" /v1/chat/completions \
  '{"model": "gpt-mock", "messages": [{"role": "user", "content": "x"}]}' >/dev/null
unauthorized="$(curl -sS -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer sk-not-a-real-key' \
  -H 'Content-Type: application/json' -d '{"model": "gpt-mock", "messages": [{"role": "user", "content": "x"}]}' \
  "${base}/v1/chat/completions")"
[[ "${unauthorized}" == "401" ]] && pass "unknown key rejected with 401" || fail "unknown key got ${unauthorized}"

echo "  ..    waiting for spend to be written and scraped"
spend=0
for _ in $(seq 1 20); do
  spend="$(api "${master_key}" "/key/info?key=${virtual_key}" | jq -r '.info.spend // 0')"
  [[ "${spend}" != "0" && "${spend}" != "0.0" ]] && break
  sleep 3
done
[[ "${spend}" != "0" && "${spend}" != "0.0" ]] && pass "spend recorded in Postgres: \$$(printf '%.6f' "${spend}")" || fail "spend still 0"

requests=0
for _ in $(seq 1 20); do
  requests="$(curl -sS "${prom}/api/v1/query" \
    --data-urlencode 'query=sum(litellm_proxy_total_requests_metric_total{namespace="litellm"})' \
    | jq -r '.data.result[0].value[1] // 0')"
  [[ "${requests}" != "0" ]] && break
  sleep 3
done
[[ "${requests}" != "0" ]] && pass "Prometheus scraped ${requests} requests from the gateway" || fail "no gateway metrics in Prometheus"

echo "all checks passed"
