#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$PWD/src"

if [[ $# -gt 0 ]]; then
  python3 -m uni_agent.orchestrator snapshot "$1"
else
  python3 -m uni_agent.orchestrator snapshot
fi
