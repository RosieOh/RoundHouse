#!/usr/bin/env bash
# Runs scripts/k6/load.js as an in-cluster Job through Traefik, with a
# virtual key that belongs to a budgeted `load-test` team, so the run shows up
# in spend, budget and autoscaling metrics like real tenant traffic would.
set -euo pipefail

rate="${RATE:-12}"
duration="${DURATION:-3m}"
base="${LITELLM_URL:-http://localhost:8080}"
ns=llm-mocks
k() { kubectl --context "${KUBE_CONTEXT:?}" "$@"; }
master_key="$(k -n litellm get secret litellm-master-key -o jsonpath='{.data.master-key}' | base64 -d)"
admin() {
  curl -sS --fail-with-body -H "Authorization: Bearer ${master_key}" -H 'Content-Type: application/json' "$@"
}

team_id="$(admin "${base}/team/list" | jq -r '[.[] | select(.team_alias == "load-test")][0].team_id // empty')"
if [[ -z "${team_id}" ]]; then
  team_id="$(admin -d '{"team_alias": "load-test", "max_budget": 20, "budget_duration": "1d"}' "${base}/team/new" | jq -r .team_id)"
  echo "created team load-test (${team_id})"
fi
key="$(admin -d '{"team_id": "'"${team_id}"'", "models": ["gpt-mock", "claude-mock"], "duration": "2h", "key_alias": "k6-'"$(date +%s)"'"}' \
  "${base}/key/generate" | jq -r .key)"

k -n "${ns}" create secret generic k6-key --from-literal=LITELLM_KEY="${key}" --dry-run=client -o yaml | k apply -f - >/dev/null
k -n "${ns}" create configmap k6-script --from-file=load.js=scripts/k6/load.js --dry-run=client -o yaml | k apply -f - >/dev/null
k -n "${ns}" delete job k6-load --ignore-not-found --wait=true >/dev/null

k apply -f - >/dev/null <<JOB
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-load
  namespace: ${ns}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 12345
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: k6
          image: docker.io/grafana/k6:2.3.0
          args: ["run", "--quiet", "/scripts/load.js"]
          env:
            - name: BASE_URL
              value: http://traefik.traefik.svc.cluster.local
            - name: RATE
              value: "${rate}"
            - name: DURATION
              value: "${duration}"
          envFrom:
            - secretRef:
                name: k6-key
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          resources:
            requests:
              cpu: 100m
              memory: 64Mi
            limits:
              memory: 512Mi
          volumeMounts:
            - name: script
              mountPath: /scripts
      volumes:
        - name: script
          configMap:
            name: k6-script
JOB

echo "k6: ${rate} req/s for ${duration} through Traefik (watch: Grafana 'LiteLLM Gateway', kubectl get hpa -n litellm -w)"
k -n "${ns}" wait --for=condition=Ready pod -l job-name=k6-load --timeout=180s >/dev/null
k -n "${ns}" logs -f job/k6-load
