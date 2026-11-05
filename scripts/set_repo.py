"""Point the integration at a real GitHub repository.

Usage:
    python scripts/set_repo.py <owner> [repo]

Rewrites manifest.json (documentation / issue_tracker / codeowners) and bumps
nothing else. Run it once before the first release, then commit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "anjubao" / "manifest.json"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    owner = sys.argv[1].lstrip("@")
    repo = sys.argv[2] if len(sys.argv) > 2 else "ha-anjubao"
    url = f"https://github.com/{owner}/{repo}"

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["documentation"] = url
    data["issue_tracker"] = f"{url}/issues"
    data["codeowners"] = [f"@{owner}"]

    MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{MANIFEST.relative_to(ROOT)} -> {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
