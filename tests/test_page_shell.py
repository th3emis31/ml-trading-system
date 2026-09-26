"""The shared page shell: every page must actually get the nav bar, styled.

The bar is built by `inject_main_nav` as a bare run of `<span class='nav-group'>` elements preceded by
`_MAIN_NAV_STYLE`, and EVERY rule in that stylesheet is written as `.nav .nav-group ...`. So a page that
drops `{{ main_nav }}` in without an ancestor carrying `class='nav'` gets the markup and none of the
styling: the whole bar renders as raw overlapping blue links, and the owner reported exactly that on the
build map page ("why is not button for the build map").

It is invisible in review because the page LOOKS complete in the HTML - the links are all there - and only
the rendered page shows it. Hence a test on the source.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
SOURCE = APP.read_text(encoding="utf-8", errors="replace")
LINES = SOURCE.split("\n")

NAV_TOKEN = "{{ main_nav }}"
WRAPPER = re.compile(r"""class=['"]nav['"]""")


def _unwrapped() -> list:
    """Line numbers where the nav is placed with no `.nav` ancestor within the three lines above."""
    out = []
    for index, line in enumerate(LINES):
        if NAV_TOKEN not in line:
            continue
        window = "\n".join(LINES[max(0, index - 3):index + 1])
        if not WRAPPER.search(window):
            out.append(index + 1)
    return out


def test_every_page_wraps_the_nav_so_its_stylesheet_applies():
    bare = _unwrapped()
    assert not bare, (
        "these lines place {{ main_nav }} with no class='nav' ancestor, so the bar renders unstyled: "
        + ", ".join(f"app.py:{n}" for n in bare))


def test_the_nav_stylesheet_really_does_require_that_ancestor():
    """If the stylesheet is ever rewritten to stand alone, the test above becomes pointless - so pin the
    reason rather than the rule."""
    style = re.search(r"_MAIN_NAV_STYLE\s*=\s*\((.*?)\n\)", SOURCE, re.S)
    assert style, "the nav stylesheet moved; check whether the wrapper is still required"
    body = style.group(1)
    assert ".nav .nav-group" in body, "the group rules are no longer scoped to .nav"


def test_the_build_map_page_carries_the_nav():
    """The page the owner was looking at. It renders with render_template_string, so it only gets the bar
    if the template asks for it."""
    template = re.search(r"I40_BUILD_MAP_TEMPLATE\s*=\s*\"\"\"(.*?)\"\"\"", SOURCE, re.S)
    assert template, "I40_BUILD_MAP_TEMPLATE not found"
    page = template.group(1)
    assert NAV_TOKEN in page
    line = next(l for l in page.split("\n") if NAV_TOKEN in l)
    assert WRAPPER.search(line), f"the build map places the nav unwrapped: {line.strip()!r}"


def test_the_pages_in_the_nav_are_routes_that_exist():
    """A link in the bar that 404s is worse than no link: it reads as a broken system."""
    groups = re.search(r"MAIN_NAV_GROUPS\s*=\s*\[(.*?)\n\]", SOURCE, re.S)
    assert groups, "MAIN_NAV_GROUPS not found"
    hrefs = {h for h in re.findall(r"\('(/[^']*)',\s*'[^']*'\)", groups.group(1))}
    routes = set(re.findall(r"@app\.route\(\s*'([^']+)'", SOURCE))
    routes |= set(re.findall(r'@app\.route\(\s*"([^"]+)"', SOURCE))
    missing = sorted(h for h in hrefs if h not in routes)
    assert not missing, f"the nav links to routes app.py does not define: {missing}"
