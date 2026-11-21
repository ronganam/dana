"""The user's own Telegram account, read-only (docs/telegram-account.md)."""

from integrations.telegram_account.kind import telegram_account
from integrations.telegram_account import tools  # noqa: F401 — registers the tools

__all__ = ["telegram_account"]
