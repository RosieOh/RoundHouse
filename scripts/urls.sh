#!/usr/bin/env bash
set -euo pipefail
k() { kubectl --context "${KUBE_CONTEXT:?}" "$@"; }
secret() { k -n "$1" get secret "$2" -o "jsonpath={.data.$3}" | base64 -d; }

cat <<INFO
LiteLLM API      http://localhost:8080/v1   (OpenAI-compatible)
LiteLLM Admin UI http://localhost:8080/ui   (user: admin)
  master key     $(secret litellm litellm-master-key master-key)
Grafana          http://localhost:3000      (user: $(secret monitoring grafana-admin admin-user))
  password       $(secret monitoring grafana-admin admin-password)
Prometheus       http://localhost:9090
INFO
