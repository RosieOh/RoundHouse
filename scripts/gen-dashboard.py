"""Generates the LiteLLM Gateway Grafana dashboard. Edit panels here, rerun, commit both files."""
import json

DS = {"type": "prometheus", "uid": "${datasource}"}
NS = 'namespace="$namespace"'
MODEL = 'requested_model=~"$model"'
panels = []
y = 0

def row(title):
    global y
    panels.append({"type": "row", "title": title, "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "id": len(panels) + 1, "panels": []})
    y += 1

def add(panel, w, h, x):
    panel["id"] = len(panels) + 1
    panel["datasource"] = DS
    panel["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
    panels.append(panel)

def target(expr, legend="", ref="A", instant=False):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref}
    if instant:
        t["instant"] = True
        t["range"] = False
    return t

def stat(title, expr, unit, x, w=4, thresholds=None, decimals=None, description=""):
    steps = thresholds or [{"color": "green", "value": None}]
    defaults = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}, "color": {"mode": "thresholds"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    add({"type": "stat", "title": title, "description": description, "targets": [target(expr)],
         "fieldConfig": {"defaults": defaults, "overrides": []},
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value",
                     "graphMode": "area", "textMode": "auto", "justifyMode": "auto", "orientation": "auto"}}, w, 4, x)

def ts(title, targets, unit, x, w=12, h=8, stack=False, description="", overrides=None, mappings=None, extra=None):
    custom = {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 10 if not stack else 60, "showPoints": "never",
              "spanNulls": True, "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if mappings:
        defaults["mappings"] = mappings
    if extra:
        defaults.update(extra)
    add({"type": "timeseries", "title": title, "description": description, "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}, w, h, x)

def refs(*pairs):
    return [target(e, l, chr(65 + i)) for i, (e, l) in enumerate(pairs)]

# ---------- overview ----------
row("Overview")
stat("Requests / s", f'sum(rate(litellm_proxy_total_requests_metric_total{{{NS},{MODEL}}}[1m]))', "reqps", 0, decimals=1)
stat("5xx ratio (5m)", f'sum(rate(litellm_proxy_total_requests_metric_total{{{NS},{MODEL},status_code=~"5.."}}[5m])) / sum(rate(litellm_proxy_total_requests_metric_total{{{NS},{MODEL}}}[5m]))',
     "percentunit", 4, thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.005}, {"color": "red", "value": 0.02}], decimals=2,
     description="Share of requests answered with 5xx. The availability SLO is 99.5%.")
stat("Gateway overhead p95", f'histogram_quantile(0.95, sum by (le) (rate(litellm_overhead_latency_metric_bucket{{{NS}}}[5m])))', "s", 8,
     thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.1}, {"color": "red", "value": 0.25}],
     description="Latency LiteLLM itself adds on top of the provider call.")
stat("Spend (1h)", f'sum(increase(litellm_spend_metric_total{{{NS},{MODEL}}}[1h]))', "currencyUSD", 12, decimals=4)
stat("Tokens / s", f'sum(rate(litellm_total_tokens_metric_total{{{NS},{MODEL}}}[1m]))', "short", 16, decimals=0)
stat("Gateway pods", f'kube_deployment_status_replicas_available{{{NS},deployment="litellm-gateway"}}', "short", 20,
     description="Available gateway replicas; the HPA scales on CPU and requests per second per pod.")
y += 4

# ---------- traffic ----------
row("Traffic and routing")
ts("Requests / s by model", refs((f'sum by (requested_model) (rate(litellm_proxy_total_requests_metric_total{{{NS},{MODEL},requested_model!=""}}[1m]))', "{{requested_model}}")), "reqps", 0, stack=True)
ts("Responses by status code", refs((f'sum by (status_code) (rate(litellm_proxy_total_requests_metric_total{{{NS},{MODEL}}}[1m]))', "{{status_code}}")), "reqps", 12, stack=True,
   overrides=[{"matcher": {"id": "byRegexp", "options": "5.."}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
              {"matcher": {"id": "byRegexp", "options": "4.."}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]}])
y += 8
ts("Deployment failure ratio", refs(
    (f'sum by (model_id) (rate(litellm_deployment_failure_responses_total{{{NS},model_id!=""}}[2m])) / sum by (model_id) (rate(litellm_deployment_total_requests_total{{{NS},model_id!=""}}[2m]))', "{{model_id}}")),
   "percentunit", 0, extra={"min": 0, "max": 1},
   description="Share of calls each deployment fails, retries included. More stable than litellm_deployment_state, which every success resets to healthy.")
ts("Fallbacks, cooldowns and upstream failures",
   refs((f'sum by (requested_model) (rate(litellm_deployment_successful_fallbacks_total{{{NS}}}[1m]))', "fallback ok {{requested_model}}"),
        (f'sum by (requested_model) (rate(litellm_deployment_failed_fallbacks_total{{{NS}}}[1m]))', "fallback failed {{requested_model}}"),
        (f'sum by (model_id) (rate(litellm_deployment_cooled_down_total{{{NS}}}[1m]))', "cooldown {{model_id}}"),
        (f'sum by (model_id) (rate(litellm_deployment_failure_responses_total{{{NS}}}[1m]))', "upstream failure {{model_id}}")),
   "reqps", 12, description="Failures the router absorbed. Clients only notice the failed fallbacks.")
y += 8

# ---------- latency ----------
row("Latency")
ts("End-to-end latency by model", refs(
    (f'histogram_quantile(0.50, sum by (le, requested_model) (rate(litellm_request_total_latency_metric_bucket{{{NS},{MODEL}}}[5m])))', "p50 {{requested_model}}"),
    (f'histogram_quantile(0.95, sum by (le, requested_model) (rate(litellm_request_total_latency_metric_bucket{{{NS},{MODEL}}}[5m])))', "p95 {{requested_model}}")), "s", 0, w=8)
ts("Time to first token (streaming)", refs(
    (f'histogram_quantile(0.95, sum by (le, requested_model) (rate(litellm_llm_api_time_to_first_token_metric_bucket{{{NS},{MODEL}}}[5m])))', "p95 {{requested_model}}")), "s", 8, w=8)
ts("Gateway overhead and queueing", refs(
    (f'histogram_quantile(0.50, sum by (le) (rate(litellm_overhead_latency_metric_bucket{{{NS}}}[5m])))', "overhead p50"),
    (f'histogram_quantile(0.95, sum by (le) (rate(litellm_overhead_latency_metric_bucket{{{NS}}}[5m])))', "overhead p95"),
    (f'histogram_quantile(0.95, sum by (le) (rate(litellm_request_queue_time_seconds_bucket{{{NS}}}[5m])))', "queue p95")), "s", 16, w=8,
   description="What LiteLLM adds on top of the provider. Rising queue time means the gateway is short of capacity.")
y += 8

# ---------- cost ----------
row("Tokens and cost")
ts("Tokens / s", refs(
    (f'sum(rate(litellm_input_tokens_metric_total{{{NS},{MODEL}}}[1m]))', "input"),
    (f'sum(rate(litellm_output_tokens_metric_total{{{NS},{MODEL}}}[1m]))', "output")), "short", 0, w=8, stack=True)
ts("Spend rate by team ($/h)", refs(
    (f'sum by (team_alias) (rate(litellm_spend_metric_total{{{NS},{MODEL}}}[5m])) * 3600', "{{team_alias}}")), "currencyUSD", 8, w=8, stack=True)
add({"type": "bargauge", "title": "Team budget remaining", "description": "Refreshed from Postgres by prometheus_initialize_budget_metrics.",
     "targets": [target(f'max by (team_alias) (litellm_remaining_team_budget_metric{{{NS}}}) / max by (team_alias) (litellm_team_max_budget_metric{{{NS}}}) > -1', "{{team_alias}}", instant=True)],
     "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1, "thresholds": {"mode": "absolute", "steps": [
         {"color": "red", "value": None}, {"color": "orange", "value": 0.1}, {"color": "green", "value": 0.3}]}}, "overrides": []},
     "options": {"orientation": "horizontal", "displayMode": "gradient", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "showUnfilled": True}}, 8, 8, 16)
y += 8

# ---------- capacity ----------
row("Capacity")
ts("Gateway replicas", refs(
    (f'kube_horizontalpodautoscaler_status_current_replicas{{{NS},horizontalpodautoscaler="litellm-gateway"}}', "current"),
    (f'kube_horizontalpodautoscaler_status_desired_replicas{{{NS},horizontalpodautoscaler="litellm-gateway"}}', "desired"),
    (f'kube_horizontalpodautoscaler_spec_max_replicas{{{NS},horizontalpodautoscaler="litellm-gateway"}}', "max")), "short", 0, w=8, extra={"decimals": 0})
ts("Requests / s per pod (HPA signal)", refs(
    (f'sum by (pod) (rate(litellm_proxy_total_requests_metric_total{{{NS}}}[1m]))', "{{pod}}")), "reqps", 8, w=8,
   description="Same query the prometheus-adapter serves as litellm_requests_per_second.")
ts("Gateway CPU per pod", refs(
    (f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{NS},container="gateway",pod=~"litellm-gateway-.*"}}[1m]))', "{{pod}}")), "cores", 16, w=8)
y += 8

dashboard = {
    "title": "LiteLLM Gateway",
    "uid": "litellm-gateway",
    "tags": ["litellm", "llmops"],
    "timezone": "browser",
    "schemaVersion": 41,
    "refresh": "30s",
    "time": {"from": "now-1h", "to": "now"},
    "editable": True,
    "graphTooltip": 1,
    "templating": {"list": [
        {"name": "datasource", "type": "datasource", "query": "prometheus", "label": "Data source", "current": {}, "hide": 0},
        {"name": "namespace", "type": "query", "label": "Namespace", "datasource": DS, "refresh": 2,
         "query": {"query": "label_values(litellm_proxy_total_requests_metric_total, namespace)", "refId": "ns"},
         "definition": "label_values(litellm_proxy_total_requests_metric_total, namespace)", "current": {}, "hide": 0},
        {"name": "model", "type": "query", "label": "Model", "datasource": DS, "refresh": 2, "multi": True, "includeAll": True, "allValue": ".*",
         "query": {"query": 'label_values(litellm_proxy_total_requests_metric_total{namespace="$namespace"}, requested_model)', "refId": "model"},
         "definition": 'label_values(litellm_proxy_total_requests_metric_total{namespace="$namespace"}, requested_model)', "current": {}, "hide": 0},
    ]},
    "annotations": {"list": []},
    "panels": panels,
}
out = __import__("pathlib").Path(__file__).resolve().parent.parent / "charts/litellm-observability/dashboards/litellm-gateway.json"
with open(out, "w") as f:
    json.dump(dashboard, f, indent=2)
    f.write("\n")
print(len(panels), "panels ->", out)
