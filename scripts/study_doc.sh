#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/study_doc.sh '<prompt>' [--mode auto|exam-study-guide|summary|cheat-sheet|assignment-brief|generic] [--format markdown+pdf|markdown|typst|pdf] [--style academic-study-guide]" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$PWD/src"

python3 -m uni_agent.orchestrator study-doc "$@"
