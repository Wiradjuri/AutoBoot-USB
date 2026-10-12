"""Turn unittest failures in a saved log into GitHub Actions error annotations.

Annotations appear on the run summary and through the checks API, so a failure
can be read without downloading the full job log. Used only by CI.
"""

import sys
from pathlib import Path


def main() -> int:
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    blocks = text.split("=" * 70)
    reports = [b.strip() for b in blocks if b.strip().startswith(("FAIL:", "ERROR:"))]
    for report in reports[:9]:
        # The last lines of a traceback carry the assertion and its message.
        lines = report.splitlines()
        body = "\n".join(lines[:1] + lines[-25:])[:3500]
        encoded = body.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title={lines[0][:120]}::{encoded}")
    summary = [line for line in text.splitlines() if line.startswith(("Ran ", "FAILED", "OK"))]
    if summary:
        print("::notice title=unittest summary::" + " | ".join(summary[-2:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
