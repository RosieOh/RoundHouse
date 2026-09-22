#!/usr/bin/env bash
# Preflight: tools on PATH and enough memory in the Docker VM.
set -uo pipefail

status=0
need() {
  if command -v "$1" >/dev/null 2>&1; then
    printf '  ok    %-10s %s\n' "$1" "$("$@" 2>/dev/null | head -1)"
  else
    printf '  MISS  %-10s install with: brew install %s\n' "$1" "$1"
    status=1
  fi
}

echo "tools"
need docker --version
need kind version
need kubectl version --client
need helm version --short
need helmfile --version
need jq --version
need openssl version

echo "docker"
if ! docker info >/dev/null 2>&1; then
  echo "  FAIL  docker daemon is not running (start Docker Desktop)"
  exit 1
fi
mem_bytes="$(docker info --format '{{.MemTotal}}')"
mem_gib=$(( mem_bytes / 1024 / 1024 / 1024 ))
if (( mem_bytes < 7 * 1024 * 1024 * 1024 )); then
  printf '  WARN  %s GiB for the Docker VM, 8 GiB or more recommended\n' "${mem_gib}"
else
  printf '  ok    %s GiB for the Docker VM\n' "${mem_gib}"
fi

exit "${status}"
