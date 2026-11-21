"""Small text helpers shared by the Telegram bot, tools and notifications."""


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram's legacy Markdown mode."""
    if not text:
        return ""
    # Characters to escape in legacy Markdown: \, _, *, `, [
    chars = ['\\', '_', '*', '`', '[']
    for c in chars:
        text = text.replace(c, f"\\{c}")
    return text
