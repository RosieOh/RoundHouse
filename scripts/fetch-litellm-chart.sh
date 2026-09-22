#!/usr/bin/env bash
# Vendors the componentized LiteLLM chart (helm/litellm) at a release tag.
#
# The chart is not published to a Helm or OCI registry, so it is taken from
# the git tag that matches the image tag. A local clone (LITELLM_REPO,
# default ../litellm) is used when it has the tag, otherwise a shallow sparse
# clone of just helm/litellm. Set LITELLM_CHART_DIR to deploy a working copy
# instead, e.g. while preparing an upstream chart fix.
set -euo pipefail

version="${1:?usage: fetch-litellm-chart.sh <tag>}"
repo="${LITELLM_REPO:-../litellm}"
dest=".cache/charts/litellm-${version}"

if [[ -f "${dest}/Chart.yaml" ]]; then
  echo "litellm chart ${version} already vendored at ${dest}"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

if git -C "${repo}" rev-parse -q --verify "refs/tags/${version}" >/dev/null 2>&1; then
  echo "vendoring litellm chart ${version} from local clone ${repo}"
  git -C "${repo}" archive "${version}" helm/litellm | tar -x -C "${tmp}"
else
  echo "vendoring litellm chart ${version} from github.com/BerriAI/litellm"
  git clone --quiet --depth 1 --branch "${version}" --filter=blob:none --sparse \
    https://github.com/BerriAI/litellm.git "${tmp}/repo"
  git -C "${tmp}/repo" sparse-checkout set helm/litellm
  mv "${tmp}/repo/helm" "${tmp}/helm"
fi

mkdir -p "$(dirname "${dest}")"
mv "${tmp}/helm/litellm" "${dest}"
echo "vendored to ${dest}"
