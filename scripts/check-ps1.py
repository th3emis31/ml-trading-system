#!/usr/bin/env python3
"""Guard against the encoding bug that broke install.ps1 on 27 September 2026.

PowerShell 5.1 reads a file without a byte-order mark as ANSI. A UTF-8 em dash then
becomes three characters, one of which (0x94) is a curly closing quote that the parser
treats as a string delimiter. The quotes unbalance and the error surfaces far away, as
"Missing closing '}' in statement block" pointing at an innocent line.

So: every .ps1 in this kit must be pure ASCII and must carry a BOM.

    python scripts/check-ps1.py        # exits non-zero when a file would break
"""

import pathlib
import sys

BOM = b"\xef\xbb\xbf"


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    problems = []
    for path in sorted(root.rglob("*.ps1")):
        data = path.read_bytes()
        rel = path.relative_to(root)
        if not data.startswith(BOM):
            problems.append("%s: no UTF-8 BOM, so PowerShell 5.1 will read it as ANSI" % rel)
        text = data.decode("utf-8-sig", errors="replace")
        for n, line in enumerate(text.split("\n"), 1):
            bad = sorted({c for c in line if ord(c) > 127})
            if bad:
                problems.append("%s:%d: non-ASCII %s in %r"
                                % (rel, n, [hex(ord(c)) for c in bad], line.strip()[:60]))
    if problems:
        print("PowerShell files that could fail to parse:")
        for p in problems:
            print("  " + p)
        return 1
    print("all .ps1 files are ASCII with a BOM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
