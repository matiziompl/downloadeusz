"""Render the application's allowlisted documentation without executing raw HTML."""

from pathlib import Path

from markdown_it import MarkdownIt


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def render_document(section: str, version: str) -> str:
    filename = {
        "readme": "README.md",
        "latest": f"docs/v{version}.md",
        "instructions": "docs/INSTRUCTION.md",
    }[section]
    source = (PROJECT_ROOT / filename).read_text(encoding="utf-8")
    # Raw HTML is escaped; MarkdownIt's URL validation also blocks javascript URLs.
    return MarkdownIt("commonmark", {"html": False}).enable("table").render(source)
