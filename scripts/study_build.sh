#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/study_build.sh '<prompt>' [--format markdown+pdf|markdown|typst|pdf] [--quiz-access ask|none|authorized] [--max-repair-cycles N]" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$PWD/src"

python3 -m uni_agent.orchestrator study-build "$@"
