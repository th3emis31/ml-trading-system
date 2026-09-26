"""Fetch every HTML page of the running app and syntax-check its inline JavaScript with node.

    python scripts\\check_page_scripts.py [--base http://127.0.0.1:5000] [--json out.json]

WHY THIS EXISTS
---------------
On 26 September 2026 the owner reported panels that would not fill. One cause was a single unescaped
apostrophe in `'- this system's own strategies only'` on the Performance page: it closed the string, and
the browser abandoned the ENTIRE 185-line script block. The page still returned HTTP 200, every Python
test still passed, and the page looked complete in the HTML - it simply did nothing.

That is the failure this catches. A JavaScript syntax error is invisible to every check this project had:
the server never sees it, the tests never load it, and the only symptom is a panel that stays on its
placeholder. So the pages are parsed by a real parser.

It reads only: GET requests to page routes, nothing posted, nothing executed beyond `node --check`.
Requires node on PATH; without it the check reports `available: false` rather than passing.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "http://127.0.0.1:5000"
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
ROUTE = re.compile(r"""@app\.route\(\s*['"]([^'"]+)['"](.*?)\)""", re.S)
SKIP_PREFIXES = ("/api/", "/static/", "/data/", "/webhook/", "/healthz", "/readyz", "/favicon")


def page_routes() -> list:
    """Every GET route that could be a page: no parameters, not an API, not POST-only."""
    text = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
    found = []
    for match in ROUTE.finditer(text):
        url, rest = match.group(1), match.group(2)
        methods = re.findall(r"['\"](GET|POST|PUT|DELETE|PATCH)['\"]", rest)
        if (methods and "GET" not in methods) or "<" in url or url.startswith(SKIP_PREFIXES):
            continue
        found.append(url)
    return sorted(set(found))


def fetch_page(base: str, url: str) -> str:
    with urllib.request.urlopen(base + url, timeout=60) as reply:
        if reply.headers.get_content_type() != "text/html":
            return ""
        return reply.read(3_000_000).decode("utf-8", "replace")


def script_faults(body: str) -> list:
    """Every inline block that does not parse, with node's own message."""
    faults = []
    for index, block in enumerate(INLINE_SCRIPT.findall(body), start=1):
        if not block.strip():
            continue
        # Wrapped in an async function: `await` and `return` are legal inside the page's handlers but not
        # at the top level of a file, and flagging those would bury the real errors in noise.
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write("(async function(){\n" + block + "\n})();")
            path = handle.name
        try:
            proc = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            faults.append({"block": index, "error": f"{type(exc).__name__}: {exc}"})
            continue
        finally:
            Path(path).unlink(missing_ok=True)
        if proc.returncode != 0:
            message = next((l.strip() for l in (proc.stderr or "").splitlines()
                            if "Error:" in l), (proc.stderr or "").strip()[:200])
            faults.append({"block": index, "lines": len(block.splitlines()), "error": message[:200]})
    return faults


def check_pages(base: str = DEFAULT_BASE) -> dict:
    if not shutil.which("node"):
        return {"available": False, "reason": "node is not on PATH, so page scripts were not parsed",
                "pages": 0, "broken": {}}
    broken, checked, unreachable = {}, 0, []
    for url in page_routes():
        try:
            body = fetch_page(base, url)
        except Exception as exc:                 # noqa: BLE001 - a page that will not load is a finding
            unreachable.append({"url": url, "error": f"{type(exc).__name__}: {exc}"[:160]})
            continue
        if not body:
            continue
        checked += 1
        faults = script_faults(body)
        if faults:
            broken[url] = faults
    return {"available": True, "reason": "", "pages": checked, "broken": broken,
            "unreachable": unreachable, "places_orders": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse the inline JavaScript of every page. Reads only.")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    out = check_pages(args.base)
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
    if not out["available"]:
        print(out["reason"])
        return 0
    print(f"{out['pages']} page(s) parsed")
    for row in out["unreachable"]:
        print(f"  unreachable {row['url']}: {row['error']}")
    for url, faults in out["broken"].items():
        print(f"  BROKEN {url}")
        for fault in faults:
            print(f"      block {fault['block']}: {fault.get('error', '')}")
    print(f"{len(out['broken'])} page(s) with a JavaScript syntax error")
    return 1 if out["broken"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
