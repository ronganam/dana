"""Provider-neutral LLM calls (LiteLLM) and which model each job uses.

Every model is a LiteLLM id (``provider/model``, e.g. ``anthropic/claude-haiku-5-5``,
``gemini/gemini-3.1-flash-lite``, ``openai/gpt-5-mini``), so switching provider is an
env change: set the id and that provider's API key (``ANTHROPIC_API_KEY``,
``GEMINI_API_KEY``, ``OPENAI_API_KEY``…).

Jobs and their env vars (each falls back to the next one in its chain, then to the
job's default in ``_DEFAULTS``: Claude Sonnet for chat and search, Claude Haiku for
the background jobs, Gemini for transcription since Claude does not take audio):

  chat           ``MODEL``                                   Telegram, triggers, small helpers
  calls          ``CALL_ANALYSIS_MODEL`` → ``BACKGROUND_MODEL``  call analysis
  notifications  ``NOTIFICATION_MODEL``  → ``BACKGROUND_MODEL``  suggestion extraction + Inbox summaries
  briefs         ``BRIEFING_MODEL``      → ``BACKGROUND_MODEL``  people briefs, wake briefing, weekly review
  shopping       ``SHOPPING_MODEL``      → ``BACKGROUND_MODEL``  shopping-list aisles
  money          ``MONEY_MODEL``         → ``BACKGROUND_MODEL``  transaction categories and merchant names
  search         ``SEARCH_MODEL``        → ``MODEL``             web-grounded answers (money price checks)
  transcribe     ``TRANSCRIBE_MODEL``                        audio → text

The web/app chat picks its model from the profile catalog in ``core/models.py``.

Purposes (core/purposes.py): every call names what it is for (``purpose=``,
core's own or one an addon declares in its ``llm_jobs``; an unknown one raises
``purposes.UnknownPurpose``); usage is tracked under it in ``core/usage.py``
(tokens, price, latency) with ``ref`` (``call:12``, ``person:abc``…) and
``source``. The model of a call: ``MODEL_<PURPOSE>`` from env when set, else
the ``job``'s chain above when the caller names a job, else the chain of the
purpose's profile (:data:`_PROFILE_CHAINS`: ``fast`` → ``BACKGROUND_MODEL`` →
Haiku, ``balanced`` → ``MODEL`` → Sonnet, ``deep`` → ``DEEP_MODEL`` → Opus).
Leave ``purpose`` out and the job name is the purpose.

``search`` needs a model with web search through LiteLLM's ``web_search_options``
(Claude's web search tool, Gemini's Google Search grounding, OpenAI's search models); :func:`search` returns
the answer with the pages it was grounded on and raises :class:`NoSearch` when
the model answered without searching.

``transcribe`` needs a model that hears audio: a chat model with audio input
(Gemini) or a speech-to-text model (``openai/gpt-4o-transcribe``, ``deepgram/nova-3``…).

Claude thinks before it answers (always on for the 5.5 models; its thinking tokens
count against ``max_tokens``): :func:`claude_kwargs` sets how hard per job
(``output_config.effort``) and keeps a tiny ``max_tokens`` from cutting the answer off.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import litellm

from core import purposes, usage, user_locale, users

logger = logging.getLogger(__name__)

HAIKU = "anthropic/claude-haiku-5-5"
SONNET = "anthropic/claude-sonnet-5-5"
OPUS = "anthropic/claude-opus-5-5"

# job → model when none of its env vars is set (HAIKU for the rest)
_DEFAULTS: Dict[str, str] = {
    "chat": SONNET,
    "search": SONNET,
    # Claude has no audio input: transcription stays on a model that hears audio.
    "transcribe": "gemini/gemini-3.1-flash-lite",
}

# job → Claude effort (output_config.effort) when the caller sets none; a job not
# listed gets the model's own default (medium on Haiku 5.5). The chat job's
# helpers (task emoji/group, name matching, summaries) are short and simple.
_CLAUDE_EFFORT: Dict[str, str] = {"chat": "low", "search": "medium"}
# Thinking shares max_tokens with the answer: a 5-token cap would leave no answer.
_CLAUDE_MIN_TOKENS = 2048

# job → env vars tried in order, then _DEFAULTS
_CHAINS: Dict[str, tuple] = {
    "chat": ("MODEL",),
    "calls": ("CALL_ANALYSIS_MODEL", "BACKGROUND_MODEL"),
    "notifications": ("NOTIFICATION_MODEL", "BACKGROUND_MODEL"),
    "briefs": ("BRIEFING_MODEL", "BACKGROUND_MODEL"),
    "shopping": ("SHOPPING_MODEL", "BACKGROUND_MODEL"),
    "money": ("MONEY_MODEL", "BACKGROUND_MODEL"),
    "telegram": ("TELEGRAM_SUMMARY_MODEL", "BACKGROUND_MODEL"),
    "search": ("SEARCH_MODEL", "MODEL"),
    "transcribe": ("TRANSCRIBE_MODEL",),
}

# profile (core/purposes.py) → env vars tried in order, then its default model
_PROFILE_CHAINS: Dict[str, tuple] = {
    "fast": (("BACKGROUND_MODEL",), HAIKU),
    "balanced": (("MODEL",), SONNET),
    "deep": (("DEEP_MODEL",), OPUS),
}

# Languages a transcription model tends to drift into: named as unintended unless the user speaks them.
_DRIFT_LANGUAGES = ("ar", "tr", "ru", "fa", "es")


def transcription_prompt(langs: Tuple[str, ...]) -> str:
    """The transcription instructions for a speaker of ``langs`` (the acting
    user's languages, core/user_locale.py)."""
    names = tuple(user_locale.language_name(c) for c in langs)
    plain = tuple(user_locale.language_name(c, native=False) for c in langs)
    if len(langs) == 1:
        speaks, which = f"The speaker primarily speaks {names[0]}. ", ""
    else:
        mix = "a mix of both" if len(langs) == 2 else "a mix of them"
        speaks = f"The speaker primarily speaks {user_locale.join_names(names, 'or')}, or {mix} (code-switching). "
        which = (f"Accurately identify whether the speech is in {plain[0]} or {plain[1]}. " if len(langs) == 2
                 else f"Accurately identify which of {user_locale.join_names(plain, 'or')} the speech is in. ")
    drift = tuple(user_locale.language_name(c, native=False) for c in _DRIFT_LANGUAGES if c not in langs)
    scripts = " ".join(f"If in {p}, transcribe in {user_locale.SCRIPTS.get(c.split('-')[0], p)}."
                       for c, p in zip(langs, plain))
    return ("Transcribe the following audio file accurately. " + speaks + which
            + "Do NOT hallucinate or transcribe into unintended foreign languages"
            + (f" (such as {', '.join(drift)}, etc.)" if drift else "") + " unless unmistakably spoken. "
            + scripts + " "
            "Output ONLY the transcribed text. Do not add any introduction, explanation, notes, or punctuation "
            "commentary. Just output the exact words spoken. If there is no speech or only silence/noise, "
            "reply with empty string.")


def model(job: str) -> str:
    """The LiteLLM model id configured for ``job`` (read on every call)."""
    for var in _CHAINS[job]:
        value = os.getenv(var, "").strip()
        if value:
            return value
    return _DEFAULTS.get(job, HAIKU)


def model_for(purpose: str, job: Optional[str] = None) -> str:
    """The model of a call for ``purpose``: ``MODEL_<PURPOSE>`` from env, else
    ``job``'s model when given, else the model of the purpose's profile.

    Raises:
        purposes.UnknownPurpose: neither core nor a loaded addon declares ``purpose``.
    """
    p = purposes.get(purpose)
    value = os.getenv(f"MODEL_{purpose.upper()}", "").strip()
    if value:
        return value
    if job:
        return model(job)
    chain, default = _PROFILE_CHAINS[p.profile]
    for var in chain:
        value = os.getenv(var, "").strip()
        if value:
            return value
    return default


def _call(purpose: str, job: Optional[str]) -> tuple:
    """(what usage files the call under, its model): a named purpose must be
    declared (:func:`model_for`); a call that names none is filed under its
    job (nothing untagged) and gets the job's model."""
    if purpose:
        return purpose, model_for(purpose, job)
    return job or "chat", model(job or "chat")


def is_claude(name: str) -> bool:
    return "claude" in name.lower()


def claude_kwargs(name: str, job: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """``kwargs`` for a call to ``name``, with Claude's thinking accounted for: the
    job's effort unless the caller set one, and room for thinking in ``max_tokens``.
    Other providers get ``kwargs`` unchanged."""
    if not is_claude(name):
        return kwargs
    kwargs = dict(kwargs)
    effort = _CLAUDE_EFFORT.get(job)
    if effort and "output_config" not in kwargs and "reasoning_effort" not in kwargs:
        kwargs["output_config"] = {"effort": effort}
    if kwargs.get("max_tokens") and kwargs["max_tokens"] < _CLAUDE_MIN_TOKENS:
        kwargs["max_tokens"] = _CLAUDE_MIN_TOKENS
    return kwargs


# The API key env names of the common providers, any one of which is enough. Checked
# here directly: LiteLLM's validate_environment counts a key set to "" (an empty
# line in .env) as present.
PROVIDER_KEYS: Dict[str, tuple] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "deepgram": ("DEEPGRAM_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "xai": ("XAI_API_KEY",),
}


def provider(name: str) -> str:
    """The LiteLLM provider of a model id ("" when LiteLLM does not know it)."""
    try:
        return str(litellm.get_llm_provider(name)[1] or "")
    except Exception:
        return name.split("/", 1)[0] if "/" in name else ""


def _missing_keys(name: str) -> List[str]:
    """The env names of the key ``name``'s provider needs, when none of them holds a value ([] when set)."""
    names = PROVIDER_KEYS.get(provider(name))
    if names:
        return [] if any(os.getenv(n, "").strip() for n in names) else list(names[:1])
    try:
        env = litellm.validate_environment(name)
    except Exception:
        return ["API key"]
    missing = list(env.get("missing_keys") or [])
    empty = [k for k in missing if not os.getenv(k, "").strip()]
    return [] if env.get("keys_in_environment") and not empty else (empty or missing or ["API key"])


def missing_key_message(name: str) -> str:
    """One short line for a model whose provider has no API key, or refused the one set
    (what the agent says instead of retrying an authentication error)."""
    who = provider(name) or name
    missing = _missing_keys(name)
    if missing:
        return f"No API key for {who}: set {missing[0]} in server/.env"
    keys = PROVIDER_KEYS.get(provider(name)) or ("its API key",)
    return f"The {who} API key was refused: check {keys[0]} in server/.env"


def configured(job: str) -> bool:
    """Whether the API key for ``job``'s provider is set."""
    return not _missing_keys(model(job))


def log_configuration() -> None:
    """Log the model each job uses; warn about jobs whose provider has no API key."""
    for job in _CHAINS:
        name = model(job)
        missing = _missing_keys(name)
        if job == "transcribe" and is_claude(name):
            logger.warning(f"LLM {job}: {name} — Claude does not take audio, transcription will fail")
        elif missing:
            logger.warning(f"LLM {job}: {name} — {', '.join(missing)} not set, these calls will fail")
        else:
            logger.info(f"LLM {job}: {name}")


def status_code(e: BaseException) -> Optional[int]:
    """HTTP status of a provider error (LiteLLM exceptions carry ``status_code``)."""
    code = getattr(e, "status_code", None)
    return code if isinstance(code, int) else None


def retryable(e: BaseException) -> bool:
    """Network errors, timeouts, 5xx and 429 are worth retrying; other 4xx are not."""
    code = status_code(e)
    return code is None or code == 429 or code >= 500


def _error_text(e: BaseException) -> str:
    return str(e) or type(e).__name__


def _text(response: Any) -> str:
    choice = response.choices[0]
    return (choice.message.content or "").strip()


async def complete(messages: List[Dict[str, Any]], *, job: Optional[str] = None, purpose: str = "", ref: str = "",
                   source: str = "", **kwargs: Any) -> str:
    """One plain completion; returns the reply text (model: :func:`model_for`).
    Raises on API failure and for an unknown purpose."""
    purpose, name = _call(purpose, job)
    response = await litellm.acompletion(model=name, messages=messages, drop_params=True,
                                         litellm_metadata=usage.meta(purpose, ref, source),
                                         **claude_kwargs(name, job or "", kwargs))
    return _text(response)


async def complete_json(prompt: str, schema: Dict[str, Any], *, job: Optional[str] = None, temperature: float = 0.2,
                        timeout: float = 60.0, attempts: int = 3, purpose: str = "", ref: str = "",
                        source: str = "", effort: str = "") -> Dict[str, Any]:
    """Run ``prompt`` with structured output constrained to ``schema`` (standard JSON
    Schema) and return the parsed object. Retries network errors, 5xx/429, and empty or
    unparsable replies (a little warmer each time, so a stuck reply doesn't repeat).
    ``effort``: Claude's ``output_config.effort`` for this call (default: the job's;
    ignored by other providers).
    Raises RuntimeError when every attempt failed, ``purposes.UnknownPurpose``
    for an unknown purpose (model: :func:`model_for`)."""
    purpose, name = _call(purpose, job)
    response_format = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}
    tag = usage.meta(purpose, ref, source)
    extra = claude_kwargs(name, job or "", {"output_config": {"effort": effort}} if effort and is_claude(name) else {})
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            response = await litellm.acompletion(
                model=name, messages=[{"role": "user", "content": prompt}],
                response_format=response_format, temperature=temperature + 0.2 * attempt,
                timeout=timeout, drop_params=True, litellm_metadata=tag, **extra)
            text = _text(response)
            if not text:
                raise ValueError(f"empty response (finish_reason={response.choices[0].finish_reason})")
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("response is not a JSON object")
            return data
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last = e
            if not retryable(e):
                break
            if attempt < attempts - 1:
                logger.warning(f"{purpose} ({name}) attempt {attempt + 1} failed: {_error_text(e)}")
                await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{name} failed: {_error_text(last) if last else 'unknown'}")


class NoSearch(RuntimeError):
    """The search model answered without searching the web (no sources came back)."""


def can_search() -> bool:
    """Whether the ``search`` job's model has its API key and, per LiteLLM's model map, web search."""
    name = model("search")
    if _missing_keys(name):
        return False
    try:
        return bool(litellm.supports_web_search(model=name))
    except Exception:
        return False


def _plain(value: Any) -> Dict[str, Any]:
    """A LiteLLM part (dict or pydantic object) as a dict ({} for anything else)."""
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            data = dump()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _grounding(response: Any) -> List[Any]:
    """Gemini's grounding metadata of a response: one dict per candidate ([] for other providers)."""
    hidden = getattr(response, "_hidden_params", None)
    grounding = getattr(response, "vertex_ai_grounding_metadata", None) or (
        hidden.get("vertex_ai_grounding_metadata") if isinstance(hidden, dict) else None) or []
    if isinstance(grounding, dict):
        return [grounding]
    return list(grounding) if isinstance(grounding, (list, tuple)) else []


def _cite_passages(text: str, response: Any, sources: List[Dict[str, str]]) -> str:
    """``text`` with `` [n]`` after each passage Gemini's grounding ties to source n (1-based in ``sources``)."""
    number = {s["url"]: n for n, s in enumerate(sources, 1)}
    marks: Dict[int, List[int]] = {}
    for meta in _grounding(response):
        meta = _plain(meta)
        urls = [str(_plain(_plain(c).get("web")).get("uri") or "").strip() for c in meta.get("groundingChunks") or []]
        cursor = 0
        for support in meta.get("groundingSupports") or []:
            support = _plain(support)
            passage = str(_plain(support.get("segment")).get("text") or "")
            at = text.find(passage, cursor) if passage else -1
            if at < 0:
                continue
            cursor = end = at + len(passage)
            for i in support.get("groundingChunkIndices") or []:
                n = number.get(urls[i]) if isinstance(i, int) and 0 <= i < len(urls) else None
                if n and n not in marks.setdefault(end, []):
                    marks[end].append(n)
    for end in sorted(marks, reverse=True):
        if marks[end]:
            text = text[:end] + "".join(f" [{n}]" for n in marks[end]) + text[end:]
    return text


def search_sources(response: Any) -> List[Dict[str, str]]:
    """The pages a web-grounded reply was based on, in order, without repeats: ``[{title, url}]``.

    Gemini: ``vertex_ai_grounding_metadata`` (on the response or in its hidden
    params), one dict per candidate with ``groundingChunks: [{web: {uri, title}}]``.
    OpenAI (and Gemini's own copy): ``message.annotations`` of type ``url_citation``.
    Anthropic: ``message.provider_specific_fields["citations"]``, a list per text
    block of ``{url, title, …}``."""
    found: List[Dict[str, str]] = []
    seen: set = set()

    def add(url: Any, title: Any) -> None:
        url = str(url or "").strip()
        if url and url not in seen:
            seen.add(url)
            found.append({"title": re.sub(r"\s+", " ", str(title or "")).strip() or url, "url": url})

    for meta in _grounding(response):
        for chunk in _plain(meta).get("groundingChunks") or []:
            web = _plain(_plain(chunk).get("web"))
            add(web.get("uri"), web.get("title"))
    choices = getattr(response, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    for annotation in getattr(message, "annotations", None) or []:
        annotation = _plain(annotation)
        if annotation.get("type") == "url_citation":
            cite = _plain(annotation.get("url_citation"))
            add(cite.get("url"), cite.get("title"))
    extra = _plain(getattr(message, "provider_specific_fields", None))
    for block in extra.get("citations") or []:
        for cite in block if isinstance(block, (list, tuple)) else [block]:
            cite = _plain(cite)
            add(cite.get("url"), cite.get("title"))
    return found


async def search(prompt: str, *, purpose: str, ref: str = "", source: str = "",
                 timeout: float = 90.0) -> Dict[str, Any]:
    """Answer ``prompt`` with the ``search`` model searching the web; returns
    ``{"text", "sources": [{"title", "url"}]}``, the text with `` [n]`` after a
    passage the provider tied to source n (Gemini; OpenAI links inline). Retries
    a network error, 5xx or 429 once. Raises :class:`NoSearch` when no source
    came back (the model did not search, or the provider dropped the option),
    RuntimeError when the call failed."""
    name = model_for(purpose, "search")
    tag = usage.meta(purpose, ref, source)
    response: Any = None
    for attempt in range(2):
        try:
            response = await litellm.acompletion(
                model=name, messages=[{"role": "user", "content": prompt}],
                web_search_options={"search_context_size": "medium"}, timeout=timeout, drop_params=True,
                litellm_metadata=tag, **claude_kwargs(name, "search", {}))
            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt or not retryable(e):
                raise RuntimeError(f"{name} failed: {_error_text(e)}") from e
            logger.warning(f"search ({name}) attempt 1 failed: {_error_text(e)}")
            await asyncio.sleep(1.5)
    sources = search_sources(response)
    if not sources:
        raise NoSearch(f"{name} answered without searching the web")
    return {"text": _cite_passages(_text(response), response, sources), "sources": sources}


def _is_speech_to_text(name: str) -> bool:
    try:
        return litellm.get_model_info(name).get("mode") == "audio_transcription"
    except Exception:
        return False


async def transcribe(audio: bytes, mime_type: str, *, timeout: float = 30.0, note: str = "",
                     purpose: str = "voice_transcript", ref: str = "", source: str = "") -> str:
    """Audio bytes → transcript ("" for silence). Sent inline, so keep it under ~15 MB.

    ``timeout`` defaults to the voice-turn budget; long recordings pass more.
    ``note`` is appended to the prompt (e.g. "This is part 2 of 5 of a longer call").
    The prompt names the acting user's languages (:func:`transcription_prompt`).
    Raises on API failure (``status_code`` tells whether a retry can help).
    """
    name = model_for(purpose, "transcribe")
    prompt = transcription_prompt(user_locale.languages(users.current())) + (f" {note}" if note else "")
    started = time.perf_counter()
    tag = usage.meta(purpose, ref, source, kind="transcribe")
    if _is_speech_to_text(name):
        # Speech-to-text endpoints take no instructions (their `prompt` is
        # vocabulary/preceding text), so the prompt and note apply to chat models only.
        ext = mime_type.split("/")[-1].split(";")[0] or "bin"
        result = await litellm.atranscription(model=name, file=(f"audio.{ext}", audio, mime_type),
                                              timeout=timeout, litellm_metadata=tag)
        transcript = (result.text or "").strip()
    else:
        data_uri = f"data:{mime_type};base64,{base64.b64encode(audio).decode()}"
        response = await litellm.acompletion(
            model=name, timeout=timeout, litellm_metadata=tag,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "file", "file": {"file_data": data_uri}},
            ]}])
        transcript = _text(response)
    logger.info("Transcribed %d bytes of %s with %s in %.2fs", len(audio), mime_type, name,
                time.perf_counter() - started)
    return transcript


def cacheable_tools(tools: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    """Mark the tool list as a cacheable prompt prefix where the provider needs it.

    The tool schemas are the biggest fixed part of every agent call (~14k tokens).
    Claude only caches up to an explicit breakpoint (cache reads cost a tenth of
    input); Gemini and OpenAI cache repeated prefixes on their own, so their
    tools stay untouched. The system prompt that follows holds the current time
    (to the second), so the tools are the prefix that actually repeats."""
    if not tools or not is_claude(name):
        return tools
    return [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]


def clean_points(items: Any, limit: int, chars: int = 160) -> List[str]:
    """Model bullet list → trimmed strings without bullet characters or trailing periods."""
    if isinstance(items, str):
        items = items.split("\n")
    out = []
    for p in items or []:
        s = re.sub(r"\s+", " ", str(p)).strip().lstrip("-•*· ").rstrip(".").strip()
        if s:
            out.append(s[:chars])
    return out[:limit]


def nullable(schema: Dict[str, Any]) -> Dict[str, Any]:
    """``schema`` or null, in the form every provider's structured output accepts."""
    return {"anyOf": [schema, {"type": "null"}]}
