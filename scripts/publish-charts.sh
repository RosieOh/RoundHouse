#!/usr/bin/env bash
# Packages every chart under charts/ and pushes it to GHCR as an OCI artifact.
# Log in first with a token that has write:packages:
#   helm registry login ghcr.io -u <github-user> --password-stdin
# Each chart's `sources` entry is what links the GHCR package back to this
# repository. Pushing a version that already exists overwrites it, so bump
# `version` in Chart.yaml for every change you publish.
set -euo pipefail

registry="${CHART_REGISTRY:-oci://ghcr.io/rosieoh/roundhouse}"
out="$(mktemp -d)"
trap 'rm -rf "${out}"' EXIT

for chart in charts/*/; do
  helm package "${chart}" --destination "${out}" >/dev/null
done
for pkg in "${out}"/*.tgz; do
  helm push "${pkg}" "${registry}"
done
