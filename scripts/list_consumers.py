#!/usr/bin/env python3
"""Output consuming repository paths, one per line."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ci_tools.utils.consumers import load_consuming_repos

CI_SHARED_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    """Print consuming repository paths."""
    os.environ.setdefault("CI_SHARED_ROOT", str(CI_SHARED_ROOT))
    repo_root = Path(argv[0]) if argv else CI_SHARED_ROOT
    for repo in load_consuming_repos(repo_root):
        print(repo.path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
