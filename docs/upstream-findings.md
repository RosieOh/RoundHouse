# Upstream findings

이 플랫폼을 LiteLLM `v1.102.0` componentized 차트로 구축하면서 발견한 문제들입니다. 전부 kind 클러스터에서 재현했고, 재현 명령과 관찰 결과를 함께 적었습니다. 각 항목에는 기존 이슈/PR 여부와 기여 방식을 달아 두었습니다.

기여하기 전에 [LiteLLM CONTRIBUTING.md](https://github.com/BerriAI/litellm/blob/main/CONTRIBUTING.md)와 `AGENTS.md`를 확인하세요. CLA 서명, `tests/test_litellm/` 테스트, 실제 proxy에 curl한 결과를 수정 증거로 붙이는 규칙이 있습니다. 차트 변경은 `LITELLM_CHART_DIR=../litellm/helm/litellm make deploy`로 이 스택에서 먼저 검증할 수 있습니다.

| # | 영역 | 요약 | 상태 | 기여 방식 |
|---|---|---|---|---|
| 1 | chart | componentized 차트가 레지스트리에 없고, 기본 이미지 태그 `0.1.0`이 존재하지 않음 | 신규 | 이슈 + PR |
| 2 | chart | Traefik 뒤에서 `/`가 backend로 가고 RSC `*.txt`가 404 | 신규 | 이슈 + PR |
| 3 | ui | `/ui/**/*.txt` RSC 페이로드 404 | #41899, PR #41925 | PR 라이브 검증 리뷰 |
| 4 | triage | #29966 "componentized 이미지 없음"은 v1.88.6부터 해결됨 | #29966 | 이슈 코멘트 |
| 5 | router | 단일 deployment 그룹은 fallback 전에 백오프 재시도, docstring과 불일치 | 신규 | 이슈(동작 확인 후 PR) |
| 6 | metrics | `litellm_deployment_state`가 last-write-wins라 알림에 쓸 수 없음 | 신규 | 문서 PR 또는 이슈 |
| 7 | metrics | 요청 카운터 기본 라벨의 카디널리티 | 신규 | 문서/차트 PR |
| 8 | chart | `LITELLM_SALT_KEY`를 차트 값으로 받을 수 없음 | 신규 | PR |
| 9 | valkey-helm | exporter 사이드카에 securityContext가 없어 restricted PSS에서 거부됨 | valkey-io/valkey-helm | 외부 PR |

---

## 1. chart: componentized chart is unpublished and its default image tag does not exist

**관찰**

- `helm/litellm/Chart.yaml`의 `appVersion`은 `"0.1.0"`이고, 네 이미지 모두 `tag: ""`라서 `.Chart.AppVersion`으로 대체됩니다
- `ghcr.io/berriai/litellm-gateway:0.1.0`은 존재하지 않습니다(`HTTP 404`). 태그를 명시하지 않고 설치하면 파드 4개가 모두 `ImagePullBackOff`가 됩니다
- 레거시 차트는 `oci://ghcr.io/berriai/litellm-helm`에 릴리스마다 올라가지만, componentized 차트는 어떤 레지스트리에도 없습니다. 그래서 사용자가 git 태그에서 직접 가져와야 합니다(이 저장소의 `scripts/fetch-litellm-chart.sh`)

```bash
tok=$(curl -s "https://ghcr.io/token?scope=repository:berriai/litellm-gateway:pull" | jq -r .token)
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $tok" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  https://ghcr.io/v2/berriai/litellm-gateway/manifests/0.1.0     # 404
```

**제안**: 릴리스 워크플로에서 `version`/`appVersion`을 릴리스 태그로 설정하고 `oci://ghcr.io/berriai/charts/litellm` 같은 경로로 push합니다. PR #29371은 예전 경로의 레거시 차트(`deploy/charts/litellm-helm`)를 대상으로 해서 이 문제와는 별개입니다

**이슈 제목 초안**: `[Bug]: componentized helm/litellm chart defaults every image to tag 0.1.0, which does not exist, and the chart is not published`

## 2. chart: behind Traefik, `/` routes to the backend and RSC `*.txt` payloads 404

**관찰** (`ingress.className: traefik`, `controller: alb`. Traefik은 점이 들어간 Exact/Prefix 경로를 받기 때문에 alb 렌더링이 가장 가깝습니다)

```text
$ curl -sI localhost:8080/ | grep -i server                        -> uvicorn   (backend, UI여야 함)
$ curl -sI localhost:8080/models-and-endpoints/index.txt | grep -i server -> uvicorn 404
```

- Traefik은 규칙 문자열 길이로 라우터 우선순위를 정합니다. 그래서 catch-all인 `PathPrefix(`/`)`(15자)가 `Path(`/`)`(9자)를 이깁니다. ALB와 ingress-nginx는 구체적인 경로를 우선합니다
- RSC 라우트 `/*.txt`는 `controller: alb`일 때만 렌더링되는데, ALB 와일드카드를 Traefik Ingress provider로는 표현할 수 없습니다
- 차트 주석은 RSC 페이로드가 "ROOT-level `<route>.txt`"라고 하지만, v1.102.0 export에는 루트에 4개, 하위 경로에 192개가 있습니다(`/models-and-endpoints/index.txt` 등). ALB의 `*`는 `/`까지 매칭하므로 ALB에서는 문제가 없고 주석만 틀렸습니다

**우회**: `charts/litellm-edge`의 Traefik `IngressRoute`(priority 1000, `Path(`/`) || PathRegexp(`\.txt$`)`)

**제안**: `ingress.controller: traefik`을 추가해서 UI 라우트를 `traefik.ingress.kubernetes.io/router.priority`가 붙은 별도 Ingress로 렌더링하거나, Gateway API `HTTPRoute`(`RegularExpression` path match) 템플릿을 추가합니다. ingress-nginx가 은퇴한 뒤라 차트가 nginx 외의 선택지를 갖는 게 중요합니다

## 3. ui: RSC payloads under `/ui` return 404 (#41899)

라우팅을 고친 뒤에도 Admin UI에서 `/ui/index.txt?_rsc=...`, `/ui/api-keys/__next._tree.txt?_rsc=...`가 UI nginx에서 404를 반환합니다. 헤드리스 브라우저로 로그인했을 때 이런 404가 13건 나왔습니다. export에는 `ui/` 디렉토리가 없어서 `try_files $uri`가 절대 맞지 않고, 그래서 소프트 내비게이션이 매번 전체 페이지 로드로 떨어집니다. 이슈 #41899와 PR #41925가 이미 열려 있습니다

**기여 방식**: PR #41925의 `ui/nginx.conf` 변경을 이 스택에서 ConfigMap으로 덮어써서 검증하고, 결과를 PR 리뷰로 남깁니다. 검증 결과는 이 문서 하단에 있습니다

## 4. triage: #29966 is resolved for releases from v1.88.6

#29966은 componentized 이미지 4개가 릴리스 태그로 올라와 있지 않다는 이슈입니다. 보고 당시(v1.86.4, v1.88.0)에는 맞는 내용이었지만, GHCR을 확인해 보면 `litellm-gateway`, `-backend`, `-ui`, `-migrations`는 v1.88.6부터 릴리스마다 amd64와 arm64로 올라와 있습니다. 이 스택은 arm64(Apple Silicon kind)에서 v1.102.0 이미지 4개로 동작합니다

**기여 방식**: 이슈에 확인 결과와 확인 명령을 코멘트로 남겨서 닫을 수 있게 합니다

## 5. router: single-deployment groups back off before falling back

**관찰**: `gpt-mock` 그룹은 deployment가 하나이고 `fallbacks: [{gpt-mock: [claude-mock]}]`, `num_retries: 2`입니다. mock-openai가 요청의 80%에 500을 반환하게 만들고 k6로 초당 6건을 4분간 보냈습니다

| 설정 | 클라이언트 실패율 | 비스트리밍 p95 |
|---|---|---|
| 장애 없음 | 0% | 606ms |
| 장애, `num_retries: 2` (1회차 / 2회차) | 0% / 0% | 6.57s / 6.49s |
| 장애, `retry_policy.InternalServerErrorRetries: 0` | 0% | **996ms** |

`litellm/router.py`의 `_time_to_sleep_before_retry`는 docstring에 "It should instantly retry only when: 1. there are healthy deployments in the same model group 2. there are fallbacks for the completion call"이라고 되어 있습니다. 하지만 구현은 1번만 확인하고, deployment가 하나인 그룹은 fallback이 있어도 `_calculate_retry_after`로 지수 백오프를 합니다. 결과적으로 장애가 난 provider에 백오프를 두고 재시도를 반복한 뒤에야 fallback으로 넘어가서, 가용성은 지켜도 지연이 10배가 되고 장애 난 provider에 부하를 세 배로 줍니다. main 브랜치도 같습니다

이 저장소는 `retry_policy`로 5xx 재시도를 끄는 것으로 우회했습니다(표의 마지막 줄). 다만 이 방법은 일시적인 5xx에서도 재시도 기회를 버리고 바로 fallback 모델의 비용과 품질을 받아들이는 절충입니다

**제안**: 호출에 fallback이 설정돼 있으면 백오프 없이(또는 재시도 없이) fallback으로 넘어가게 하거나, 의도한 동작이라면 docstring을 고치고 `retry_policy` 사용을 문서화합니다. 어느 쪽이 의도인지 먼저 이슈로 메인테이너에게 확인하는 게 좋습니다

**이슈 제목 초안**: `[Bug]: router backs off before retrying a single-deployment group even when fallbacks are configured, adding seconds of latency during a provider outage`

## 6. metrics: `litellm_deployment_state` is last-write-wins

같은 장애 실험에서 `litellm_deployment_state{model_id="mock-openai-1"}`은 0과 1을 오갔습니다. 실패할 때마다 partial outage(1)로, 성공할 때마다 healthy(0)로 덮어쓰기 때문에 70%가 실패하는 deployment도 대부분의 샘플에서 0입니다. 이 게이지로 `for: 2m` 알림을 만들면 한 번도 발생하지 않습니다. 같은 기간 `litellm_deployment_failure_responses_total / litellm_deployment_total_requests_total`은 0.68~0.69로 안정적이었습니다

**제안**: 문서에 이 의미를 명시하고 알림에는 실패율을 쓰라고 안내하거나, 윈도 기반의 health 메트릭을 추가합니다

## 7. metrics: default label cardinality on request counters

`litellm_proxy_total_requests_metric_total`에는 기본으로 `client_ip`, `user_agent`, `hashed_api_key`, `api_key_alias`, `user`, `user_email`, `end_user` 라벨이 붙습니다. Kubernetes에서는 `client_ip`가 파드나 노드 IP라서 스케일링할 때마다 바뀌고, 키와 사용자 수만큼 시계열이 곱해집니다. `litellm_settings.prometheus_metrics_config`로 메트릭과 라벨을 allowlist로 제한할 수 있지만, 이 설정은 적은 메트릭만 내보내는 방식이라 필요한 메트릭을 전부 나열해야 합니다

**제안**: Helm 차트나 Kubernetes 배포 문서에 카디널리티를 제한한 `prometheus_metrics_config` 예시를 넣습니다

## 8. chart: no first-class salt key value

`LITELLM_SALT_KEY`는 DB에 저장하는 provider 자격 증명을 암호화합니다. 없으면 master key로 대신 암호화하기 때문에 master key를 교체하면 저장된 자격 증명을 읽을 수 없게 됩니다. 차트에는 `masterKey.secretName`은 있지만 salt key 값은 없어서, 이 저장소는 `gateway.envSecrets`/`backend.envSecrets`로 넣고 있습니다

**제안**: `saltKey.secretName` / `saltKey.secretKey` 값을 추가하고 gateway와 backend에 같은 Secret을 주입합니다

## 9. valkey-helm: exporter sidecar has no securityContext

Valkey 공식 차트 `0.12.0`의 `metrics.exporter.securityContext` 기본값이 `{}`입니다. 네임스페이스에 `pod-security.kubernetes.io/enforce=restricted`가 걸려 있으면 Valkey 파드가 거부됩니다. Valkey 컨테이너 자체는 차트가 이미 강화해 두었습니다. 이 저장소는 `values/valkey.yaml`에서 명시적으로 지정했습니다

**기여 방식**: [valkey-io/valkey-helm](https://github.com/valkey-io/valkey-helm)에 Valkey 컨테이너와 같은 기본값을 exporter에도 넣는 PR을 보냅니다

---

## 검증 로그: PR #41925

2026-09-22, LiteLLM v1.102.0 UI 이미지, Traefik 뒤의 componentized 배포에서 확인했습니다. v1.102.0의 `ui/nginx.conf`에 PR의 hunk(`location ~ ^/ui/(.+)\.txt$ { try_files /$1.txt /$1/index.txt =404; }`)만 적용해서 ConfigMap으로 UI 파드의 `/etc/nginx/nginx.conf`를 덮어썼습니다. `nginx -t`는 통과했습니다

헤드리스 브라우저로 로그인한 뒤 사이드바에서 "Models + Endpoints", "Teams"를 차례로 클릭했습니다

| | v1.102.0 nginx.conf | PR #41925 적용 |
|---|---|---|
| 두 번 클릭하는 동안 문서 전체 로드 | 2회(hard navigation) | 0회(soft navigation) |
| `_rsc` 요청 404 / 200 | 39 / 0 | 0 / 44 |
| 콘솔 에러 | 39건 | 0건 |
| 없는 페이로드(`/ui/does-not-exist.txt`) | 404 | 404(HTML로 떨어지지 않음) |

```bash
# 재현: PR hunk를 적용한 nginx.conf를 UI 파드에 마운트
kubectl -n litellm create configmap ui-nginx-pr41925 --from-file=nginx.conf=./nginx.conf
kubectl -n litellm patch deployment litellm-ui --type=strategic -p '{"spec":{"template":{"spec":{
  "volumes":[{"name":"nginx-pr41925","configMap":{"name":"ui-nginx-pr41925"}}],
  "containers":[{"name":"ui","volumeMounts":[{"name":"nginx-pr41925","mountPath":"/etc/nginx/nginx.conf","subPath":"nginx.conf"}]}]}}}}'
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "http://localhost:8080/ui/api-keys/__next._tree.txt?_rsc=x"   # 200 text/plain
kubectl -n litellm rollout undo deployment/litellm-ui   # 원복
```

PR 리뷰 코멘트 초안(AGENTS.md 규칙에 맞춰 15~25단어): `Verified on a live v1.102.0 componentized deploy behind Traefik: RSC 404s went from 39 to 0 and sidebar clicks stay soft navigations`
