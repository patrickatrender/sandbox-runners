#!/bin/bash
# Start Docker, then run one job with the JIT config the controller wrote.
set -euo pipefail

config=/run/runner/jitconfig
jit=$(cat "$config")
rm -f "$config"

if ! docker info >/dev/null 2>&1; then
  dockerd >/var/log/dockerd.log 2>&1 &
  for _ in $(seq 60); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  docker info >/dev/null 2>&1 || { echo "dockerd did not start" >&2; tail -50 /var/log/dockerd.log >&2; exit 1; }
fi

cd /home/runner
exec runuser -u runner -- ./run.sh --jitconfig "$jit"
