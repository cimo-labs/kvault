"""Server-side Markdown rendering via mistune."""

from typing import cast

import mistune

_renderer = mistune.create_markdown(
    escape=True,
    plugins=["strikethrough", "table"],
)


def render_markdown(text: str) -> str:
    """Render Markdown text to HTML."""
    if not text:
        return ""
    return cast(str, _renderer(text))
