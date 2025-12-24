#!/usr/bin/env bash
# Usage: scripts/ci.sh ["Commit message"]
set -euo pipefail

export CI_SHARED_ROOT="${CI_SHARED_ROOT:-${HOME}/projects/ci_shared}"
export COMMON_ROOT="${COMMON_ROOT:-${HOME}/projects/common}"
SHARED_SCRIPT="${CI_SHARED_ROOT}/ci_tools/scripts/ci.sh"

if [[ ! -x "${SHARED_SCRIPT}" ]]; then
  echo "Shared CI runner not found at ${SHARED_SCRIPT}." >&2
  echo "Set CI_SHARED_ROOT or clone ci_shared to ${HOME}/ci_shared." >&2
  exit 1
fi

if [[ ! -d "${COMMON_ROOT}/src/common" ]]; then
  echo "Common library not found at ${COMMON_ROOT}/src/common." >&2
  echo "Set COMMON_ROOT or clone common to ${HOME}/common." >&2
  exit 1
fi

export PYTHONPATH="${COMMON_ROOT}/src:${CI_SHARED_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONDONTWRITEBYTECODE=1

# Wrapper convenience: allow skipping git commit/push.
# Default behavior is to stage/commit/push after checks (handled by shared runner).
if [[ "${1-}" == "--no-commit" ]]; then
  export CI_AUTOMATION=1
  shift
fi

exec "${SHARED_SCRIPT}" "$@"
