import logging
import io
import json
import html
import hashlib
import traceback
import datetime
from collections import OrderedDict
from typing import Dict, List, Any, Optional, Set, Tuple
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton
from core.agent import AgentOrchestrator
from core import llm
from core.voice import summarize_text
from core import debug_env, notify, session_runner, users
from core.text import escape_markdown
from core.session import (build_session_debug_payload, clear_history, get_pending, save_session_debug,
                          waiting_for_someone_else)
from core.session_runner import start_agent_task, start_resume_task, start_resume_selection_task
from core.users import User
from core.runs import SessionBusyError, live_undos
from core import undo
from core.images import save_image
from core.addons import registry, store
from addons.about import legacy as about_legacy
from addons.money import otp as money_otp
from addons.whatsapp import config as whatsapp_config
import asyncio
import os

logger = logging.getLogger(__name__)

# WhatsApp voice transcripts are short; keep the Telegram copy readable and
# well under Telegram's 4096-char message limit.
_WA_VOICE_MAX_TEXT = 3000
_WA_VOICE_CACHE_MAX = 200


def report_whatsapp_voice_chats() -> None:
    """Log, per user with WhatsApp, where their voice transcripts go (their
    ``voice_telegram_chat_id`` setting) — loudly when nowhere. Startup only."""
    for uid in store.users_with(whatsapp_config.ADDON):
        user = users.get(uid)
        conn = store.get(user, whatsapp_config.ADDON) if user else None
        if conn is None:
            continue
        chat_id = whatsapp_config.voice_chat_id(conn)
        if chat_id is None:
            logger.warning(f"WhatsApp voice transcripts of user {uid} will NOT be sent to Telegram: their "
                           f"{whatsapp_config.ADDON} setting {whatsapp_config.VOICE_CHAT} is not a chat id.")
        else:
            logger.info(f"WhatsApp voice transcripts of user {uid} go to Telegram chat {chat_id}")


UNAUTHORIZED_TEXT = "⛔ You are not authorized to use this bot."


class AuthMiddleware:
    """Lets in Telegram users that resolve to a user (core/users.py), passes
    that user on to handlers as ``user`` and runs the handler as that user
    (``users.acting_as``: runs, tools, undo and usage see it). While no user
    has a Telegram id the bot is open and everyone is the first admin
    (core.users.telegram_open); otherwise unknown ids are refused."""

    async def __call__(self, handler, event, data):
        if users.telegram_open():
            user = users.first_admin()
            if user is None:
                return await handler(event, data)
            return await self._as(user, handler, event, data)

        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return

        user = users.by_telegram_id(user_id)
        if user is None:
            logger.warning(f"Unauthorized access denied for user ID {user_id}")
            if isinstance(event, types.CallbackQuery):
                await event.answer(UNAUTHORIZED_TEXT, show_alert=True)
            else:
                await event.reply(UNAUTHORIZED_TEXT)
            return

        return await self._as(user, handler, event, data)

    @staticmethod
    async def _as(user: User, handler, event, data):
        data["user"] = user
        with users.acting_as(user):
            return await handler(event, data)


# The /memory menu runs the About-you tools (addons/about); without that
# addon it answers with NO_MEMORY_TEXT instead of the registry's refusal.
MEMORY_KIND = "about"
NO_MEMORY_TEXT = "🧠 Memory isn't set up for you: the About you addon is not connected."

def api_session() -> Optional[AiohttpSession]:
    """The Bot API server from ``TELEGRAM_API_BASE`` (e.g. a self-hosted
    ``telegram-bot-api``, or the local test environment's fake,
    docs/local-testing.md); None = aiogram's default, api.telegram.org."""
    base = (os.getenv("TELEGRAM_API_BASE") or "").strip().rstrip("/")
    if not base:
        return None
    logger.info(f"Telegram Bot API server: {base}")
    return AiohttpSession(api=TelegramAPIServer.from_base(base))


class TelegramBot:
    """The Telegram channel adapter: turns updates into runs of the conversation
    core (core/session_runner.py) for the user they come from, renders results,
    and is the sender behind core.notify (registered here)."""

    def __init__(self, token: str):
        """Initialize the Telegram Bot interface.

        Args:
            token: Telegram bot token.

        Who may use the bot is decided per update by :class:`AuthMiddleware`.
        Histories, pending approvals and the agent live in the core.
        """
        self.bot = Bot(token=token, session=api_session())
        self.dp = Dispatcher(storage=MemoryStorage())

        # Register auth middleware
        auth = AuthMiddleware()
        self.dp.message.middleware(auth)
        self.dp.callback_query.middleware(auth)
        
        # State machine for interactive /memory command prompts
        self.user_memory_states: Dict[int, str] = {}

        # Multi-select user-selection prompts, per (user id, session id): the
        # toggled option indices and the prompt message they belong to (two
        # people in one group each have their own prompt and choices).
        self.selection_toggles: Dict[Tuple[str, str], Set[int]] = {}
        self.selection_messages: Dict[Tuple[str, str], int] = {}

        # Where each user's inbound WhatsApp voice transcripts go (their
        # whatsapp setting, read per message; reported here once).
        report_whatsapp_voice_chats()

        # Recent inbound WhatsApp voice transcripts with the user they belong to,
        # keyed by a short hash used as the callback_data of the "Summarize"
        # button (Telegram caps callback_data at 64 bytes, so the raw message id
        # won't fit). In-memory: a restart just invalidates old buttons, which
        # the handler reports gracefully.
        self.wa_voice_transcripts: "OrderedDict[str, Dict[str, str]]" = OrderedDict()

        # Register handlers
        self.dp.message.register(self.handle_start, Command("start"))
        self.dp.message.register(self.handle_clear, Command("clear"))
        self.dp.message.register(self.handle_debug, Command("debug"))
        self.dp.message.register(self.handle_memory, Command("memory"))
        self.dp.message.register(self.handle_session, Command("session"))
        self.dp.message.register(self.handle_chatid, Command("chatid"))
        self.dp.message.register(self.handle_message)
        # Must be registered before the catch-all approval callback handler.
        self.dp.callback_query.register(self.handle_whatsapp_summary_callback, F.data.startswith("wa_sum:"))
        self.dp.callback_query.register(self.handle_undo_callback, F.data.startswith("undo:"))
        self.dp.callback_query.register(self.handle_approval_callback)

        notify.register_telegram(self)

    async def send_text(self, chat_id: int, text: str, parse_mode: Optional[str] = None) -> None:
        """Send a plain message (core.notify's channel)."""
        await self.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)

    async def handle_chatid(self, message: types.Message):
        """Reply with this chat's ID (setup helper for chat-id settings)."""
        ctype = getattr(message.chat, "type", "?")
        await message.reply(
            f"🆔 Chat ID: `{message.chat.id}`\nType: {ctype}\n\n"
            f"Use this value for the WhatsApp connection's voice_telegram_chat_id setting.",
            parse_mode="Markdown",
        )

    async def handle_session(self, message: types.Message):
        """Handle the /session command to display the session ID."""
        session_id = f"tg_{message.chat.id}"
        await message.reply(
            f"📱 *Current Session ID:*\n`{session_id}`\n\n"
            f"Copy this ID and paste it in the Dana Android app or Web UI to view or continue this conversation!",
            parse_mode="Markdown"
        )

    async def handle_start(self, message: types.Message, user: User):
        """Handle the /start command."""
        if not message.from_user:
            return
        session_id = f"tg_{message.chat.id}"
        clear_history(user.id, session_id)
        logger.info(f"User {message.from_user.id} started the bot in chat {message.chat.id}. History cleared.")
        
        welcome_text = (
            "🤖 Welcome! I am your modular personal AI assistant.\n\n"
            "I have access to several skills including calendar management, "
            "HomeAssistant control, task list/todos, and web search.\n\n"
            "Here are some commands you can use:\n"
            "• /start - Restart the bot and clear history\n"
            "• /clear - Clear your conversation history\n"
            "• /session - View your current Session ID to use on Web/Android\n"
            "• /debug - Get a diagnostic session file for troubleshooting\n\n"
            "How can I help you today?"
        )
        await message.reply(welcome_text, reply_markup=self.get_keyboard())

    async def handle_clear(self, message: types.Message, user: User):
        """Handle the /clear command to reset memory."""
        if not message.from_user:
            return
        session_id = f"tg_{message.chat.id}"
        clear_history(user.id, session_id)
        logger.info(f"User {message.from_user.id} cleared their chat history in chat {message.chat.id}.")
        await message.reply("🧹 Conversation history cleared!", reply_markup=self.get_keyboard())

    async def handle_debug(self, message: types.Message, user: User):
        """Handle the /debug command to manually send a debug session file."""
        if not message.from_user:
            return
        user_id = message.from_user.id
        logger.info(f"User {user_id} requested manual debug dump.")
        await message.reply("⏳ Generating debug session file...", reply_markup=self.get_keyboard())
        await self._send_debug_file(user, message.chat.id, user_id, error="Manual debug trigger")

    async def handle_memory(self, message: types.Message, user: User):
        """Handle the /memory command for interactive memory management."""
        if not message.from_user:
            return
        if not store.is_connected(user, MEMORY_KIND):
            await message.reply(NO_MEMORY_TEXT, reply_markup=self.get_keyboard())
            return
        user_id = message.from_user.id
        text = message.text or ""
        logger.info(f"User {user_id} triggered /memory with text: '{text}'")
        
        # Clear any ongoing memory input state
        self.user_memory_states.pop(user_id, None)
        
        # Split command arguments
        # e.g., "/memory remember gf_id 12345@c.us" -> ["/memory", "remember", "gf_id", "12345@c.us"]
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            # Show interactive menu with inline buttons
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [
                    types.InlineKeyboardButton(text="📋 List Memories", callback_data="mem_list_cats"),
                    types.InlineKeyboardButton(text="🔍 Search Memory", callback_data="mem_search_prompt")
                ],
                [
                    types.InlineKeyboardButton(text="➕ Add Memory", callback_data="mem_add_prompt"),
                    types.InlineKeyboardButton(text="❌ Forget Memory", callback_data="mem_forget_select")
                ]
            ])
            welcome_text = (
                "🧠 *Interactive Memory Manager*\n\n"
                "Use the buttons below to interactively view, search, add, or delete memories from my persistent storage."
            )
            await message.reply(welcome_text, reply_markup=keyboard, parse_mode="Markdown")
            return

        subcommand = parts[1].lower().strip()
        
        if subcommand == "list":
            category = None
            if len(parts) == 3:
                category = parts[2].strip()
            
            try:
                res = await registry.execute(users.current(), "list_memories", {"category": category})
                await message.reply(res, reply_markup=self.get_keyboard(), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error listing memories in bot command: {e}", exc_info=True)
                await message.reply(f"❌ Error listing memories: {e}", reply_markup=self.get_keyboard())

        elif subcommand == "recall":
            if len(parts) < 3:
                await message.reply("⚠️ *Usage:* `/memory recall <query>`", reply_markup=self.get_keyboard(), parse_mode="Markdown")
                return
            
            query = parts[2].strip()
            try:
                res = await registry.execute(users.current(), "recall_info", {"query": query})
                await message.reply(res, reply_markup=self.get_keyboard(), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error recalling memory in bot command: {e}", exc_info=True)
                await message.reply(f"❌ Error recalling memory: {e}", reply_markup=self.get_keyboard())

        elif subcommand == "forget":
            if len(parts) < 3:
                await message.reply("⚠️ *Usage:* `/memory forget <key>`", reply_markup=self.get_keyboard(), parse_mode="Markdown")
                return
            
            key = parts[2].strip()
            try:
                res = await registry.execute(users.current(), "forget_info", {"key": key})
                await message.reply(res, reply_markup=self.get_keyboard(), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error forgetting memory in bot command: {e}", exc_info=True)
                await message.reply(f"❌ Error forgetting memory: {e}", reply_markup=self.get_keyboard())

        elif subcommand == "remember":
            if len(parts) < 3:
                await message.reply("⚠️ *Usage:* `/memory remember <key> <value> [-c <category>]`", reply_markup=self.get_keyboard(), parse_mode="Markdown")
                return
            
            # Sub-parse the key and value from parts[2]
            # E.g. "girlfriend_chat_id 972500000301@c.us -c contact"
            rem_parts = parts[2].split(maxsplit=1)
            if len(rem_parts) < 2:
                await message.reply("⚠️ *Usage:* `/memory remember <key> <value> [-c <category>]`", reply_markup=self.get_keyboard(), parse_mode="Markdown")
                return
            
            key = rem_parts[0].strip()
            rest = rem_parts[1].strip()
            
            # Check for category flag: "-c" or "--category"
            category = "general"
            value = rest
            
            for flag in (" -c ", " --category "):
                if flag in rest:
                    val_part, cat_part = rest.split(flag, 1)
                    value = val_part.strip()
                    category = cat_part.strip()
                    break
                    
            try:
                res = await registry.execute(users.current(), "remember_info", {"key": key, "value": value, "category": category})
                await message.reply(res, reply_markup=self.get_keyboard(), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error saving memory in bot command: {e}", exc_info=True)
                await message.reply(f"❌ Error saving memory: {e}", reply_markup=self.get_keyboard())

        else:
            await message.reply(f"⚠️ Unknown memory subcommand: `{subcommand}`\nUse `/memory` to see available commands.", reply_markup=self.get_keyboard(), parse_mode="Markdown")

    async def _transcribe_voice(self, voice: types.Voice) -> str:
        """Download and transcribe a Telegram voice message."""
        try:
            # 1. Get file details from Telegram
            file_id = voice.file_id
            file = await self.bot.get_file(file_id)
            
            # 2. Download the file from Telegram to an in-memory buffer
            voice_buffer = io.BytesIO()
            await self.bot.download(file, destination=voice_buffer)
            voice_bytes = voice_buffer.getvalue()
            
            if not voice_bytes:
                raise ValueError("Downloaded voice file is empty")
                
            return await llm.transcribe(voice_bytes, "audio/ogg", source="telegram")

        except Exception as e:
            logger.error(f"Error transcribing voice message: {e}", exc_info=True)
            raise e

    async def handle_message(self, message: types.Message, user: User):
        """Process incoming text/voice/photo messages via the ReAct agent loop, as ``user``."""
        if not message.from_user:
            return
        user_id = message.from_user.id
        user_text = message.text or ""
        # A bank's SMS sign-in code a money sync waits for: handed over, never
        # logged, never shown to the agent or kept in history.
        code_reply = money_otp.telegram_reply(user, user_text) if user_text else None
        if code_reply is not None:
            await message.reply(code_reply)
            return

        is_voice = False
        status_msg = None
        images = []
        
        if message.photo:
            logger.info(f"Received photo from user {user_id}")
            await self.bot.send_chat_action(chat_id=message.chat.id, action="typing")
            status_msg = await message.reply("🖼️ *Processing image...*", parse_mode="Markdown")
            try:
                photo = message.photo[-1]
                file = await self.bot.get_file(photo.file_id)
                buf = io.BytesIO()
                await self.bot.download_file(file.file_path, buf)
                image_url = save_image(buf.getvalue(), "image/jpeg", user)
                images.append(image_url)
                user_text = message.caption or "Please analyze this image."
                logger.info(f"Saved photo from user {user_id}: {image_url}")
            except Exception as e:
                logger.error(f"Failed to process photo: {e}", exc_info=True)
                await status_msg.edit_text("⚠️ *Failed to process image.*", parse_mode="Markdown")
                return
        
        elif message.voice:
            is_voice = True
            logger.info(f"Received voice message from user {user_id}")
            # Send typing action to show we are working
            await self.bot.send_chat_action(chat_id=message.chat.id, action="typing")
            
            # Send a temporary status message
            status_msg = await message.reply("🎙️ *Transcribing voice note...*", parse_mode="Markdown")
            
            try:
                user_text = await self._transcribe_voice(message.voice)
                logger.info(f"Transcribed voice message from user {user_id}: '{user_text}'")
                if not user_text:
                    await status_msg.edit_text("⚠️ *No speech detected in the voice note.*", parse_mode="Markdown")
                    return
            except Exception as e:
                logger.error(f"Failed to transcribe voice note: {e}", exc_info=True)
                await status_msg.edit_text("⚠️ *Failed to transcribe voice note.*", parse_mode="Markdown")
                await self._send_debug_file(user, message.chat.id, user_id, e)
                return
        
        if not user_text:
            # Skip messages with no text content (images, stickers, etc.)
            return
            
        logger.info(f"Received message from user {user_id}: '{user_text}'")
        
        # Check if the user is in an interactive memory input state
        if user_id in self.user_memory_states:
            state = self.user_memory_states.pop(user_id)
            if not store.is_connected(user, MEMORY_KIND):
                await message.reply(NO_MEMORY_TEXT, reply_markup=self.get_keyboard())
                return
            if state == "awaiting_search_query":
                try:
                    res = await registry.execute(user, "recall_info", {"query": user_text})
                    await message.reply(res, reply_markup=self.get_keyboard(), parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Error recalling memory in user message state: {e}", exc_info=True)
                    await message.reply(f"❌ Error recalling memory: {e}", reply_markup=self.get_keyboard())
                return
                
            elif state == "awaiting_remember_input":
                if ":" not in user_text:
                    await message.reply(
                        "⚠️ *Invalid Format!*\n\nPlease write the memory as `key: value` or `key: value | category`.\n\n"
                        "To try again, click *➕ Add Memory* in `/memory` menu.",
                        reply_markup=self.get_keyboard(),
                        parse_mode="Markdown"
                    )
                    return
                parts = user_text.split(":", 1)
                key = parts[0].strip()
                rest = parts[1].strip()
                
                category = "general"
                value = rest
                if "|" in rest:
                    val_part, cat_part = rest.split("|", 1)
                    value = val_part.strip()
                    category = cat_part.strip()
                    
                try:
                    res = await registry.execute(user, "remember_info", {"key": key, "value": value, "category": category})
                    await message.reply(res, reply_markup=self.get_keyboard(), parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Error saving memory in user message state: {e}", exc_info=True)
                    await message.reply(f"❌ Error saving memory: {e}", reply_markup=self.get_keyboard())
                return
        
        # Send a typing indicator to Telegram to show background activity
        await self.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        
        session_id = f"tg_{message.chat.id}"

        # Start background agent task
        try:
            run = start_agent_task(user, session_id, user_text, is_voice=is_voice, images=images or None,
                                   device="telegram")
            task = run.task
        except SessionBusyError:
            await message.answer("⏳ Still working on your previous request — please wait for it to finish.")
            return
        
        try:
            # Periodically send typing action while waiting for the task
            while not task.done():
                await self.bot.send_chat_action(chat_id=message.chat.id, action="typing")
                await asyncio.sleep(4)
            result = await task
            
            await self._handle_agent_result(
                result=result,
                user=user,
                user_id=user_id,
                chat_id=message.chat.id,
                status_msg=status_msg,
                is_voice=is_voice,
                user_text=user_text,
                reply_to_message_id=message.message_id,
                run=run,
            )
            save_session_debug(user.id, session_id, agent=session_runner.agent())
            
        except Exception as e:
            logger.error(f"Error handling user message: {e}", exc_info=True)
            error_text = "⚠️ Sorry, I encountered an unexpected error processing your request."
            if status_msg:
                try:
                    await status_msg.edit_text(error_text)
                except Exception:
                    pass
            else:
                await message.reply(error_text, reply_markup=self.get_keyboard())
            await self._send_debug_file(user, message.chat.id, user_id, e)
            save_session_debug(user.id, session_id, error_context=traceback.format_exc(), agent=session_runner.agent())

    @staticmethod
    def _wa_voice_keyboard(key: str) -> types.InlineKeyboardMarkup:
        return types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="✨ Summarize", callback_data=f"wa_sum:{key}")
        ]])

    @staticmethod
    def _wa_voice_key(user: User, message_id: str) -> str:
        """Short, callback_data-safe key for a user's WhatsApp message id (64-byte limit)."""
        return hashlib.sha1(f"{user.id}:{message_id or ''}".encode("utf-8")).hexdigest()[:12]

    async def notify_whatsapp_voice(self, user: User, chat_id: Optional[int], sender: str, transcript: str,
                                    message_id: str) -> bool:
        """Send ``user``'s inbound WhatsApp voice transcript to ``chat_id`` (their
        ``voice_telegram_chat_id`` setting) with a Summarize button for them."""
        if not chat_id:
            logger.error(
                f"Cannot send the WhatsApp voice transcript of user {user.id} to Telegram: their whatsapp "
                "setting voice_telegram_chat_id is not set (or not a valid chat id)."
            )
            return False

        body = (transcript or "").strip()
        has_speech = bool(body)
        body = body or "(no speech detected)"
        if len(body) > _WA_VOICE_MAX_TEXT:
            body = body[:_WA_VOICE_MAX_TEXT].rstrip() + "… (truncated)"

        key = self._wa_voice_key(user, message_id)
        text = f"🎙️ <b>WhatsApp voice — {html.escape(sender or 'Unknown')}</b>\n\n{html.escape(body)}"
        self.wa_voice_transcripts[key] = {"sender": sender or "Unknown", "transcript": body, "text": text,
                                          "user_id": user.id}
        self.wa_voice_transcripts.move_to_end(key)
        while len(self.wa_voice_transcripts) > _WA_VOICE_CACHE_MAX:
            self.wa_voice_transcripts.popitem(last=False)

        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                # Nothing to summarize when the note had no speech.
                reply_markup=self._wa_voice_keyboard(key) if has_speech else None,
            )
        except Exception as e:
            logger.error(f"Failed to send WhatsApp voice transcript to Telegram: {e}")
            return False
        logger.info(f"Sent WhatsApp voice transcript to Telegram chat {chat_id}")
        return True

    async def handle_whatsapp_summary_callback(self, callback_query: types.CallbackQuery, user: User):
        """Summarize an inbound WhatsApp voice transcript on demand (✨ button),
        only for the user the transcript belongs to (as that user: usage theirs)."""
        data = callback_query.data or ""
        key = data.split(":", 1)[1] if ":" in data else ""
        entry = self.wa_voice_transcripts.get(key)
        if not entry:
            await callback_query.answer(
                "⚠️ Transcript no longer available (the assistant may have restarted).",
                show_alert=True,
            )
            return
        if entry.get("user_id") != user.id:
            await callback_query.answer("⛔ This transcript belongs to someone else.", show_alert=True)
            return

        message = callback_query.message
        if message is None or not hasattr(message, "edit_text"):
            await callback_query.answer("⚠️ This message can no longer be updated.", show_alert=True)
            return

        if not entry.get("transcript") or entry["transcript"] == "(no speech detected)":
            await callback_query.answer("Nothing to summarize in this voice note.", show_alert=True)
            return

        await callback_query.answer("✨ Summarizing…")

        chat_id = getattr(getattr(message, "chat", None), "id", None)
        if chat_id is not None:
            try:
                await self.bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass

        try:
            summary = await summarize_text(entry["transcript"])
        except Exception as e:
            logger.error(f"Failed to summarize WhatsApp voice transcript: {e}", exc_info=True)
            summary = ""

        if not summary:
            try:
                await message.edit_text(
                    f"{entry['text']}\n\n⚠️ <i>Could not summarize. Tap the button to try again.</i>",
                    parse_mode="HTML",
                    reply_markup=self._wa_voice_keyboard(key),
                )
            except Exception:
                pass
            return

        if len(summary) > 3500:
            summary = summary[:3500].rstrip() + "…"

        try:
            await message.edit_text(
                f"{entry['text']}\n\n<b>✨ Summary</b>\n{html.escape(summary)}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Failed to edit WhatsApp voice message with summary: {e}")

    async def handle_approval_callback(self, callback_query: types.CallbackQuery, user: User):
        """Handle inline button actions for approving/denying non-read-only tool executions.

        The buttons belong to the user whose run is waiting: anyone else pressing
        them (a shared group) gets an alert and the message is left alone."""
        user_id = callback_query.from_user.id
        action = callback_query.data
        message = callback_query.message

        if (action and not action.startswith("mem_") and isinstance(message, types.Message)
                and (waiting_for_someone_else(user.id, f"tg_{message.chat.id}")
                     or not self._own_selection_prompt(user, message, action))):
            await callback_query.answer("⛔ This is waiting for someone else.", show_alert=True)
            return

        await callback_query.answer()
        
        if not action:
            return
            
        if action.startswith("mem_"):
            await self.handle_memory_callback(callback_query, user)
            return
            
        if not message or not isinstance(message, types.Message):
            return
        
        session_id = f"tg_{message.chat.id}"
        pending = get_pending(user.id, session_id)
        if pending is None:
            pending = get_pending(user.id, str(user_id))
            if pending is None:
                await message.edit_text("⚠️ *No pending action found or it has expired.*", parse_mode="Markdown")
                return
            session_id = str(user_id)

        # Stale approval buttons pressed against an iteration-limit suspension
        if action in ("approve_action", "deny_action") and pending.get("status") == "iterations_exceeded":
            await message.edit_text(
                "⚠️ *This approval is no longer valid.* Use the extend/stop buttons on the step-limit message instead.",
                parse_mode="Markdown"
            )
            return

        # User-selection prompt buttons (single pick / multi-select toggle+done)
        if action.startswith("sel_pick:") or action.startswith("sel_toggle:") or action == "sel_done":
            await self.handle_selection_callback(callback_query, user, message, session_id, pending, action)
            return
        
        # Edit confirmation message to indicate processing or rejection state
        if action == "approve_action":
            try:
                await message.edit_text(
                    f"⏳ *Executing approved action:* `{pending['tool_name']}`...", 
                    parse_mode="Markdown"
                )
            except Exception:
                await message.edit_text(
                    f"⏳ Executing approved action: {pending['tool_name']}..."
                )
            action_val = "approve"
        elif action == "extend_iterations":
            try:
                await message.edit_text("🔁 *Extending step limit, continuing...*", parse_mode="Markdown")
            except Exception:
                await message.edit_text("🔁 Extending step limit, continuing...")
            action_val = "extend_iterations"
        elif action == "decline_iterations":
            try:
                await message.edit_text("✋ *Stopping, waiting for your input...*", parse_mode="Markdown")
            except Exception:
                await message.edit_text("✋ Stopping, waiting for your input...")
            action_val = "decline_iterations"
        else:
            await message.edit_text("❌ *Action denied by user.*", parse_mode="Markdown")
            action_val = "deny"
            
        # Start background resume task
        try:
            run = start_resume_task(user, session_id, action_val)
            task = run.task
        except SessionBusyError:
            return
        
        try:
            while not task.done():
                try:
                    await self.bot.send_chat_action(chat_id=pending["chat_id"], action="typing")
                except Exception:
                    pass
                await asyncio.sleep(4)
            result = await task
            
            # Handle the agent execution result
            await self._handle_agent_result(
                result=result,
                user=user,
                user_id=user_id,
                chat_id=pending["chat_id"],
                status_msg=None, # Already handled in initial run
                is_voice=pending.get("is_voice", False),
                user_text=pending.get("user_text", ""),
                reply_to_message_id=None,
                edit_message=message,
                run=run,
            )
            save_session_debug(user.id, session_id, agent=session_runner.agent())
            
        except Exception as e:
            logger.error(f"Error resuming agent loop: {e}", exc_info=True)
            try:
                await self.bot.send_message(
                    chat_id=pending["chat_id"],
                    text="⚠️ Sorry, I encountered an error while executing the approved action.",
                    reply_markup=self.get_keyboard()
                )
                await self._send_debug_file(user, pending["chat_id"], user_id, e)
            except Exception:
                pass
            save_session_debug(user.id, str(user_id), error_context=traceback.format_exc(), agent=session_runner.agent())

    def _own_selection_prompt(self, user: User, message: types.Message, action: str) -> bool:
        """For a selection button: the message is ``user``'s own prompt (not
        another user's in the same group). Unknown (sent before a restart):
        their pending selection decides, as before."""
        if not (action.startswith("sel_pick:") or action.startswith("sel_toggle:") or action == "sel_done"):
            return True
        sent = self.selection_messages.get((user.id, f"tg_{message.chat.id}"))
        return sent is None or sent == message.message_id

    async def handle_selection_callback(self, callback_query: types.CallbackQuery, user: User, message: types.Message,
                                        session_id: str, pending: Dict[str, Any], action: str):
        """Handle inline buttons of an interactive user-selection prompt.

        Single-select picks resume immediately; multi-select toggles update the
        keyboard until ``sel_done`` is pressed.
        """
        user_id = callback_query.from_user.id
        options = pending.get("options") or []
        multiple = pending.get("multiple", False)

        # Stale selection buttons (session moved past the selection prompt)
        if pending.get("status") != "awaiting_selection":
            try:
                await message.edit_text("⚠️ *This selection is no longer valid.*", parse_mode="Markdown")
            except Exception:
                await message.edit_text("⚠️ This selection is no longer valid.")
            return

        # Multi-select: toggle an option and re-render the keyboard
        if action.startswith("sel_toggle:"):
            try:
                idx = int(action.split(":", 1)[1])
            except ValueError:
                return
            if not (0 <= idx < len(options)):
                return
            toggled = self.selection_toggles.setdefault((user.id, session_id), set())
            if idx in toggled:
                toggled.discard(idx)
            else:
                toggled.add(idx)
            keyboard = self._selection_keyboard((user.id, session_id), options, multiple)
            try:
                await message.edit_reply_markup(reply_markup=keyboard)
            except Exception as e:
                logger.warning(f"Failed to update selection keyboard: {e}")
            return

        # Resolve the selected option indices
        if action.startswith("sel_pick:"):
            try:
                idx = int(action.split(":", 1)[1])
            except ValueError:
                return
            if not (0 <= idx < len(options)):
                return
            label = options[idx].get("label") or options[idx].get("value") or f"Option {idx + 1}"
            self._forget_selection(user, session_id)
            resume_selection = [idx]
            try:
                await message.edit_text(f"✅ *Selected:* {escape_markdown(str(label))}", parse_mode="Markdown")
            except Exception:
                await message.edit_text(f"✅ Selected: {label}")
        elif action == "sel_done":
            resume_selection = sorted(self.selection_toggles.get((user.id, session_id), set()))
            self._forget_selection(user, session_id)
            try:
                await message.edit_text("✅ *Selection received, continuing...*", parse_mode="Markdown")
            except Exception:
                await message.edit_text("✅ Selection received, continuing...")
        else:
            return

        # Start background resume task
        try:
            task = start_resume_selection_task(user, session_id, resume_selection).task
        except SessionBusyError:
            return

        try:
            while not task.done():
                try:
                    await self.bot.send_chat_action(chat_id=pending["chat_id"], action="typing")
                except Exception:
                    pass
                await asyncio.sleep(4)
            result = await task

            # Handle the agent execution result
            await self._handle_agent_result(
                result=result,
                user=user,
                user_id=user_id,
                chat_id=pending["chat_id"],
                status_msg=None,
                is_voice=pending.get("is_voice", False),
                user_text=pending.get("user_text", ""),
                reply_to_message_id=None,
                edit_message=message
            )
            save_session_debug(user.id, session_id, agent=session_runner.agent())

        except Exception as e:
            logger.error(f"Error resuming selection loop: {e}", exc_info=True)
            try:
                await self.bot.send_message(
                    chat_id=pending["chat_id"],
                    text="⚠️ Sorry, I encountered an error while processing your selection.",
                    reply_markup=self.get_keyboard()
                )
                await self._send_debug_file(user, pending["chat_id"], user_id, e)
            except Exception:
                pass
            save_session_debug(user.id, str(user_id), error_context=traceback.format_exc(), agent=session_runner.agent())

    def _selection_caption(self, pending: Dict[str, Any]) -> str:
        """Render the caption of a user-selection prompt message."""
        prompt = escape_markdown(str(pending.get("prompt") or "Please choose:"))
        multiple = pending.get("multiple", False)
        context = pending.get("context") or ""
        text = f"❓ *{prompt}*\n\n"
        text += "Pick *one or more* options, then press *Done*:\n" if multiple else "Pick an option:\n"
        if context:
            text += f"\n_{escape_markdown(str(context))}_\n"
        return text

    def _forget_selection(self, user: User, session_id: str) -> None:
        self.selection_toggles.pop((user.id, session_id), None)
        self.selection_messages.pop((user.id, session_id), None)

    def _selection_keyboard(self, key: Tuple[str, str], options: List[Dict[str, Any]],
                            multiple: bool) -> types.InlineKeyboardMarkup:
        """Build the inline keyboard for a user-selection prompt (``key``: user id, session id)."""
        toggled = self.selection_toggles.get(key, set())
        inline_keyboard = []
        for i, opt in enumerate(options):
            label = opt.get("label") or opt.get("value") or f"Option {i + 1}"
            label = str(label)
            if len(label) > 40:
                label = f"{label[:37]}..."
            if multiple:
                mark = "☑️ " if i in toggled else "⬜️ "
                inline_keyboard.append(
                    [types.InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"sel_toggle:{i}")]
                )
            else:
                inline_keyboard.append(
                    [types.InlineKeyboardButton(text=label, callback_data=f"sel_pick:{i}")]
                )
        if multiple:
            inline_keyboard.append([types.InlineKeyboardButton(text="✅ Done", callback_data="sel_done")])
        inline_keyboard.append([types.InlineKeyboardButton(text="❌ Cancel", callback_data="deny_action")])
        return types.InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

    async def _send_selection_prompt(self, chat_id: int, pending: Dict[str, Any], user: User):
        """Send ``user``'s interactive user-selection prompt to a Telegram chat
        (its message id is remembered: only its buttons change their choice)."""
        session_id = f"tg_{chat_id}"
        self._forget_selection(user, session_id)
        options = pending.get("options") or []
        multiple = pending.get("multiple", False)
        text = self._selection_caption(pending)
        keyboard = self._selection_keyboard((user.id, session_id), options, multiple)
        try:
            sent = await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Failed to send selection prompt with Markdown: {e}")
            sent = await self.bot.send_message(
                chat_id=chat_id,
                text=text.replace("*", "").replace("_", ""),
                reply_markup=keyboard
            )
        message_id = getattr(sent, "message_id", None)
        if isinstance(message_id, int):
            self.selection_messages[(user.id, session_id)] = message_id

    async def handle_memory_callback(self, callback_query: types.CallbackQuery, user: User):
        """Handle callback queries for the interactive memory manager."""
        user_id = callback_query.from_user.id
        action = callback_query.data
        message = callback_query.message
        
        await callback_query.answer()
        
        if not action or not message or not isinstance(message, types.Message):
            return
        if not store.is_connected(user, MEMORY_KIND):
            self.user_memory_states.pop(user_id, None)
            await message.edit_text(NO_MEMORY_TEXT)
            return
            
        # Clear state on any new button press unless it's a specific prompt
        if action not in ("mem_search_prompt", "mem_add_prompt"):
            self.user_memory_states.pop(user_id, None)
            
        if action == "mem_menu":
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [
                    types.InlineKeyboardButton(text="📋 List Memories", callback_data="mem_list_cats"),
                    types.InlineKeyboardButton(text="🔍 Search Memory", callback_data="mem_search_prompt")
                ],
                [
                    types.InlineKeyboardButton(text="➕ Add Memory", callback_data="mem_add_prompt"),
                    types.InlineKeyboardButton(text="❌ Forget Memory", callback_data="mem_forget_select")
                ]
            ])
            await message.edit_text(
                "🧠 *Interactive Memory Manager*\n\n"
                "Use the buttons below to interactively view, search, add, or delete memories.",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        elif action == "mem_list_cats":
            categories = ["all", "general", "contact", "credential", "preference"]
            try:
                db_cats = [c for c in about_legacy.memory_categories(users.current()) if c not in categories]
                categories.extend(db_cats)
            except Exception as e:
                logger.warning(f"Failed to fetch dynamic categories: {e}")
                
            inline_keyboard = []
            row = []
            for cat in categories:
                label = "All Categories" if cat == "all" else cat.capitalize()
                row.append(types.InlineKeyboardButton(text=label, callback_data=f"mem_list_cat:{cat}"))
                if len(row) == 2:
                    inline_keyboard.append(row)
                    row = []
            if row:
                inline_keyboard.append(row)
                
            inline_keyboard.append([types.InlineKeyboardButton(text="🔙 Back to Menu", callback_data="mem_menu")])
            
            await message.edit_text(
                "📋 *List Memories*\n\nSelect a category to view:",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=inline_keyboard),
                parse_mode="Markdown"
            )
            
        elif action.startswith("mem_list_cat:"):
            category = action.split(":", 1)[1]
            cat_arg = None if category == "all" else category
            
            try:
                res = await registry.execute(users.current(), "list_memories", {"category": cat_arg})
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🔙 Back to Categories", callback_data="mem_list_cats")]
                ])
                await message.edit_text(res, reply_markup=keyboard, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error in memory callback list: {e}", exc_info=True)
                await message.edit_text(f"❌ Error: {e}", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🔙 Back", callback_data="mem_menu")]
                ]))
 
        elif action == "mem_search_prompt":
            self.user_memory_states[user_id] = "awaiting_search_query"
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="❌ Cancel", callback_data="mem_cancel")]
            ])
            await message.edit_text(
                "🔍 *Search Memory*\n\nPlease type the search query/term you want to look up (or click Cancel):",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
 
        elif action == "mem_add_prompt":
            self.user_memory_states[user_id] = "awaiting_remember_input"
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="❌ Cancel", callback_data="mem_cancel")]
            ])
            await message.edit_text(
                "➕ *Add Memory*\n\nPlease type the information you want to save using the format:\n"
                "`key: value`   or   `key: value | category`\n\n"
                "*Example:*\n"
                "`girlfriend_chat_id: 972500000301@c.us`  or\n"
                "`girlfriend_chat_id: 972500000301@c.us | contact`",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
 
        elif action == "mem_forget_select":
            try:
                keys = about_legacy.memory_keys(users.current())
            except Exception as e:
                logger.error(f"Error fetching keys to forget: {e}", exc_info=True)
                await message.edit_text(f"❌ Error fetching keys: {e}", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🔙 Back", callback_data="mem_menu")]
                ]))
                return
                
            if not keys:
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🔙 Back to Menu", callback_data="mem_menu")]
                ])
                await message.edit_text("❌ You have no saved memories to delete.", reply_markup=keyboard, parse_mode="Markdown")
                return
                
            inline_keyboard = []
            for key in keys:
                btn_text = key if len(key) <= 30 else f"{key[:27]}..."
                inline_keyboard.append([types.InlineKeyboardButton(text=f"🗑️ {btn_text}", callback_data=f"mem_forget_key:{key}")])
                
            inline_keyboard.append([types.InlineKeyboardButton(text="🔙 Cancel / Back", callback_data="mem_menu")])
            
            await message.edit_text(
                "❌ *Forget Memory*\n\nSelect the memory key you want me to forget/delete:",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=inline_keyboard),
                parse_mode="Markdown"
            )
 
        elif action.startswith("mem_forget_key:"):
            key = action.split(":", 1)[1]
            try:
                res = await registry.execute(users.current(), "forget_info", {"key": key})
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                    [
                        types.InlineKeyboardButton(text="🔙 Back to Delete List", callback_data="mem_forget_select"),
                        types.InlineKeyboardButton(text="🏠 Main Menu", callback_data="mem_menu")
                    ]
                ])
                await message.edit_text(res, reply_markup=keyboard, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error deleting key in callback: {e}", exc_info=True)
                await message.edit_text(f"❌ Error: {e}", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🔙 Back", callback_data="mem_menu")]
                ]))
 
        elif action == "mem_cancel":
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Main Menu", callback_data="mem_menu")]
            ])
            await message.edit_text("❌ Action cancelled.", reply_markup=keyboard)

    async def _handle_agent_result(
        self,
        result: Any,
        user: User,
        user_id: int,
        chat_id: int,
        status_msg: Optional[types.Message],
        is_voice: bool,
        user_text: str,
        reply_to_message_id: Optional[int],
        edit_message: Optional[types.Message] = None,
        run: Any = None,
    ):
        """Process the agent run or resume result.
        Note: history management is handled by session_runner.handle_agent_result.
        This method only handles Telegram UI updates.
        """
        session_id = f"tg_{chat_id}"
        
        if isinstance(result, dict) and result.get("status") == "pending_approval":
            # If it's a voice message, we update the status message to show the transcription
            if is_voice and status_msg:
                try:
                    await status_msg.edit_text(f"🎙️ *You said:* \"_{user_text}_\"", parse_mode="Markdown")
                except Exception:
                    await status_msg.edit_text(f"🎙️ You said: \"{user_text}\"")
            
            # If we had a previous edit_message, mark it as successfully executed
            if edit_message:
                try:
                    await edit_message.edit_text("✅ *Action executed successfully.*", parse_mode="Markdown")
                except Exception:
                    pass
            
            # Format confirmation message and keyboard
            confirm_text = self._format_confirmation_msg(result["tool_name"], result["tool_args"])
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [
                    types.InlineKeyboardButton(text="✅ Approve", callback_data="approve_action"),
                    types.InlineKeyboardButton(text="❌ Deny", callback_data="deny_action")
                ]
            ])
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=confirm_text,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Failed to send confirmation with parse_mode='Markdown', retrying as plain text: {e}")
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=confirm_text,
                    reply_markup=keyboard
                )
        elif isinstance(result, dict) and result.get("status") == "iterations_exceeded":
            # Agent hit the iteration limit — offer to extend or stop & refine.
            if is_voice and status_msg:
                try:
                    await status_msg.edit_text(f"🎙️ *You said:* \"_{user_text}_\"", parse_mode="Markdown")
                except Exception:
                    await status_msg.edit_text(f"🎙️ You said: \"{user_text}\"")

            if edit_message:
                try:
                    await edit_message.edit_text("🔁 *I reached the step limit.*", parse_mode="Markdown")
                except Exception:
                    pass

            summary = (
                f"{AgentOrchestrator.summarize_state(result)}\n\n"
                "*How would you like to proceed?*"
            )
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [
                    types.InlineKeyboardButton(text="🔁 Extend limit & continue", callback_data="extend_iterations")
                ],
                [
                    types.InlineKeyboardButton(text="✋ Stop & let me refine", callback_data="decline_iterations")
                ]
            ])
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=summary,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except Exception:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=summary,
                    reply_markup=keyboard
                )
        elif isinstance(result, dict) and result.get("status") == "awaiting_selection":
            # The agent asked the user to pick from options — render an
            # interactive single/multi-select prompt.
            if is_voice and status_msg:
                try:
                    await status_msg.edit_text(f"🎙️ *You said:* \"_{user_text}_\"", parse_mode="Markdown")
                except Exception:
                    await status_msg.edit_text(f"🎙️ You said: \"{user_text}\"")

            if edit_message:
                try:
                    await edit_message.edit_text("✅ *Selection received.*", parse_mode="Markdown")
                except Exception:
                    await edit_message.edit_text("✅ Selection received.")

            await self._send_selection_prompt(chat_id, result, user)
        else:
            try:
                response_text, new_entries = result
            except (ValueError, TypeError) as e:
                logger.error(f"Failed to unpack agent result: {result} - error: {e}", exc_info=True)
                response_text = "I encountered an error processing the assistant response."
                new_entries = []
                
            # Changes made at once get an Undo button under the reply.
            undo_kb = self._undo_keyboard(live_undos(run))

            # Reply to the user
            if edit_message:
                try:
                    # Edit the confirmation/processing message with the final response
                    await edit_message.edit_text(response_text, reply_markup=undo_kb)
                except Exception:
                    # Fallback to sending a new message if edit fails (e.g. invalid markdown tags in LLM output)
                    await self.bot.send_message(chat_id=chat_id, text=response_text, reply_markup=undo_kb or self.get_keyboard())
            elif is_voice and status_msg:
                final_response = f"🎙️ *You said:* \"_{user_text}_\"\n\n{response_text}"
                try:
                    await status_msg.edit_text(final_response, parse_mode="Markdown", reply_markup=undo_kb)
                except Exception:
                    await status_msg.edit_text(f"🎙️ You said: \"{user_text}\"\n\n{response_text}", reply_markup=undo_kb)
            else:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=response_text,
                    reply_to_message_id=reply_to_message_id,
                    reply_markup=undo_kb or self.get_keyboard()
                )

            # If the response indicates an LLM error, trigger the debug attachment
            if response_text.startswith("Sorry, I encountered an error communicating with the AI model:"):
                await self._send_debug_file(user, chat_id, user_id, response_text)

    def _format_confirmation_msg(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """The Approve / Deny message for a tool call waiting for the user's OK.
        Worded in core/approval_text.py, shared with the Now screen's approval card."""
        from core import approval_text
        return approval_text.telegram(tool_name, tool_args)

    def get_keyboard(self) -> ReplyKeyboardMarkup:
        """Create a persistent keyboard containing the bot commands."""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text="/start"),
                    KeyboardButton(text="/clear"),
                    KeyboardButton(text="/memory"),
                    KeyboardButton(text="/debug")
                ]
            ],
            resize_keyboard=True
        )
        return keyboard

    @staticmethod
    def _undo_keyboard(undos: List[Dict[str, str]]) -> Optional[types.InlineKeyboardMarkup]:
        """One "Undo" button per change of the run (callback ``undo:<id>``)."""
        if not undos:
            return None
        rows = []
        for u in undos[:4]:
            label = u["label"] if len(undos) > 1 else "Undo"
            text = f"↩️ {label}"
            rows.append([types.InlineKeyboardButton(text=text[:60], callback_data=f"undo:{u['id']}")])
        return types.InlineKeyboardMarkup(inline_keyboard=rows)

    async def handle_undo_callback(self, callback_query: types.CallbackQuery):
        """Undo button under a reply: reverse that change (core/undo.py)."""
        undo_id = (callback_query.data or "").split(":", 1)[-1]
        try:
            label = await undo.run(undo_id)
            note, alert = f"↩️ Undone: {label}", False
        except KeyError:
            note, alert = "This can no longer be undone.", True
        except Exception as e:
            logger.warning(f"Telegram undo {undo_id} failed: {e}")
            note, alert = f"Not undone: {e}", True
        await callback_query.answer(note[:190], show_alert=alert)
        message = callback_query.message
        if isinstance(message, types.Message) and not alert:
            # Drop the used button, keep any others.
            kb = message.reply_markup
            rows = [[b for b in row if b.callback_data != callback_query.data]
                    for row in (kb.inline_keyboard if kb else [])]
            rows = [r for r in rows if r]
            try:
                await message.edit_text(f"{message.text or ''}\n\n{note}",
                                        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
            except Exception:
                pass

    async def _send_debug_file(self, user: User, chat_id: int, user_id: int, error: Any):
        """Generate and send a debug file containing ``user``'s session state for this chat."""
        try:
            session_id = f"tg_{chat_id}"

            # Format the error message or traceback
            if isinstance(error, Exception):
                error_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            else:
                error_str = str(error)

            debug_info = (build_session_debug_payload(user.id, session_id, debug_env.environment_for(user))
                          or {"session_id": session_id})
            debug_info.update({
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "user_id": user_id,
                "chat_id": chat_id,
                "agent_model": getattr(session_runner.agent(), "model", "unknown"),
                "error_context": error_str,
            })

            # Serialize to JSON
            json_str = json.dumps(debug_info, indent=2, default=str)
            file_bytes = json_str.encode("utf-8")

            # Create BufferedInputFile
            filename = f"debug_session_{user_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            document = BufferedInputFile(file_bytes, filename=filename)

            # Send document
            await self.bot.send_document(
                chat_id=chat_id,
                document=document,
                caption="🛠️ Here is the debug session information file.",
                reply_markup=self.get_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to generate or send debug file: {e}", exc_info=True)
            await self.bot.send_message(
                chat_id=chat_id,
                text="❌ Failed to generate or send the debug session file.",
                reply_markup=self.get_keyboard()
            )

    async def start(self):
        """Start polling incoming updates from Telegram."""
        logger.info("Starting Telegram Bot long polling...")
        # Skip updates that occurred while the bot was offline
        await self.bot.delete_webhook(drop_pending_updates=True)
        await self.dp.start_polling(self.bot)
