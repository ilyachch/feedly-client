"""The Markdown rules file.

The file is written and read by the agent; the utility only makes sure it exists and never parses
it. Keeping it plain Markdown means a human can edit it at any moment.
"""

from pathlib import Path

TEMPLATE = """# Feedly triage rules

Topics the user has already judged. One topic per bullet, short and specific.
The agent reads this file before triaging and appends to it after the user makes a new decision.
"Interesting" always wins over "Not interesting".

## Not interesting

### Everywhere

### Source: <feed or folder name>

## Interesting

### Everywhere
"""


def ensure_rules_file(path: Path) -> Path:
    """Create the rules file from the template when it does not exist yet."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
    return path
