#!/usr/bin/env python3
"""Output consuming repository paths, one per line."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure ci_shared is in path and CI_SHARED_ROOT is set
ci_shared_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ci_shared_root))
os.environ.setdefault("CI_SHARED_ROOT", str(ci_shared_root))

from ci_tools.utils.consumers import load_consuming_repos

repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else ci_shared_root

for repo in load_consuming_repos(repo_root):
    print(repo.path)
