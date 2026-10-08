"""Check that every relative markdown link in a wiki folder resolves to a file.

Usage: check_wiki_links.py <dir> [--allow-missing-prefix PREFIX ...]

Walks every `*.md` under <dir> (skipping `.obsidian/`) and fails on:
- a relative link whose target file does not exist (`/x.md` resolves from <dir>);
- a page that links to itself (a `#section` link to the same page is fine);
- a target listed twice in one page's `## Related` section.

Links with a scheme (`https:`, `mailto:`) or a bare `#anchor` are not checked.
`--allow-missing-prefix data/notes/` tolerates missing targets under that prefix,
for checking the hcloud wiki folder, where note files live in another repo.
Exit 0 when clean, 1 with one line per problem otherwise.
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"\[[^\]]*\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")  # [text](target "title")


def _related_section(text: str) -> str:
    match = re.search(r"^## Related\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return match.group(1) if match else ""


def check(root: Path, allow_missing: list[str]) -> list[str]:
    problems = []
    for page in sorted(root.rglob("*.md")):
        if ".obsidian" in page.relative_to(root).parts:
            continue
        text = page.read_text(encoding="utf-8")
        rel_page = page.relative_to(root)
        for raw in LINK.findall(text):
            if ":" in raw or raw.startswith("#"):
                continue
            target = unquote(raw.split("#", 1)[0])
            resolved = (
                (root / target.lstrip("/")) if target.startswith("/") else (page.parent / target)
            )
            if resolved.resolve() == page.resolve() and "#" not in raw:
                problems.append(f"{rel_page}: links to itself ({raw})")
            elif not resolved.exists() and not any(target.startswith(p) for p in allow_missing):
                problems.append(f"{rel_page}: broken link {raw}")
        related = LINK.findall(_related_section(text))
        for dup in sorted({t for t in related if related.count(t) > 1}):
            problems.append(f"{rel_page}: {dup} listed twice under ## Related")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--allow-missing-prefix", action="append", default=[])
    args = parser.parse_args()
    problems = check(args.root, args.allow_missing_prefix)
    for line in problems:
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
