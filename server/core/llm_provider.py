"""The LLM provider chosen in the app, and its key (docs/configuration.md, "Models").

A stranger's server needs one LLM key. The admin picks a provider (Claude,
OpenAI, Gemini, or a local model through Ollama) and pastes its key in the
wizard or in Settings; the key is tested on the spot (core/llm_api.py), then
stored server-wide in ``server_settings['llm']``, encrypted with the
credentials key (core/addons/crypto.py), and never sent back: answers say only
that a key is set and its last four characters.

The choice gives every job its model (:func:`job_model`, :func:`profile_model`,
:func:`catalog`, read by core/llm.py and core/models.py) and the provider's key
(:func:`apply` puts it in the process environment under the provider's own
name, ``ANTHROPIC_API_KEY`` and so on, where LiteLLM reads it). Env stays the
override: a model variable (``MODEL``, ``BACKGROUND_MODEL``…) or a key
variable set by the operator wins over the stored choice, and the stored key
is never written over a key the operator set.
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from core import server_settings
from core.addons import crypto

logger = logging.getLogger(__name__)

SETTING = "llm"
SLOTS = ("fast", "balanced", "deep")


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    # The env name LiteLLM reads its key from ("" for one that takes none: Ollama).
    key_env: str
    # Model per profile (core/purposes.py): fast = background jobs, balanced = chat, deep.
    models: Dict[str, str]
    # Audio → text with the same key; "" keeps the default transcription model (Gemini).
    transcribe: str = ""
    # Where to get a key (shown next to the field).
    key_url: str = ""
    # Tool use is not dependable yet (a local model).
    experimental: bool = False
    # Takes a server address and a model name instead of a key (Ollama).
    local: bool = False

    def public(self) -> Dict[str, Any]:
        return {"id": self.id, "label": self.label, "key_env": self.key_env, "models": dict(self.models),
                "transcribe": self.transcribe, "key_url": self.key_url, "experimental": self.experimental,
                "local": self.local}


CLAUDE = "claude"
OLLAMA = "ollama"
OLLAMA_BASE_ENV = "OLLAMA_API_BASE"
OLLAMA_DEFAULT_BASE = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "qwen3"

PROVIDERS: Dict[str, Provider] = {
    CLAUDE: Provider(CLAUDE, "Claude", "ANTHROPIC_API_KEY",
                     {"fast": "anthropic/claude-haiku-5-5", "balanced": "anthropic/claude-sonnet-5-5",
                      "deep": "anthropic/claude-opus-5-5"},
                     key_url="https://console.anthropic.com/settings/keys"),
    "openai": Provider("openai", "OpenAI", "OPENAI_API_KEY",
                       {"fast": "openai/gpt-5.4-mini", "balanced": "openai/gpt-5.5", "deep": "openai/gpt-5.5-pro"},
                       transcribe="openai/gpt-4o-transcribe", key_url="https://platform.openai.com/api-keys"),
    "gemini": Provider("gemini", "Gemini", "GEMINI_API_KEY",
                       {"fast": "gemini/gemini-3.1-flash-lite", "balanced": "gemini/gemini-3.5-flash",
                        "deep": "gemini/gemini-3.1-pro-preview"},
                       transcribe="gemini/gemini-3.1-flash-lite", key_url="https://aistudio.google.com/apikey"),
    OLLAMA: Provider(OLLAMA, "Local model (Ollama)", "", {}, experimental=True, local=True),
}

# Env names this module put in the environment itself (so a new key may replace them).
_ours: Set[str] = set()
_cached: Optional[Dict[str, Any]] = None


class Invalid(ValueError):
    """A provider, key, address or model that cannot be used (``field``: which one)."""

    def __init__(self, field: str, detail: str) -> None:
        super().__init__(detail)
        self.field = field
        self.detail = detail


# --- The stored choice -----------------------------------------------------------------------

def _stored() -> Dict[str, Any]:
    global _cached
    if _cached is None:
        value = server_settings.get(SETTING, {})
        _cached = value if isinstance(value, dict) else {}
    return _cached


def forget_cache() -> None:
    """Read the stored choice again on next use (tests, another process changed it)."""
    global _cached
    _cached = None


def chosen() -> Optional[Provider]:
    """The provider the admin chose (None when none was chosen: the built-in Claude defaults apply)."""
    return PROVIDERS.get(str(_stored().get("provider") or ""))


def _ollama_model(name: str) -> str:
    name = (name or "").strip()
    return name if name.startswith(("ollama/", "ollama_chat/")) else f"ollama_chat/{name or OLLAMA_DEFAULT_MODEL}"


def models_of(provider: Provider, model: str = "") -> Dict[str, str]:
    """The model of each profile for ``provider`` (``model``: the local model's name, Ollama)."""
    if provider.local:
        name = _ollama_model(model)
        return {slot: name for slot in SLOTS}
    return dict(provider.models)


def profile_model(profile: str) -> Optional[str]:
    """The chosen provider's model for a profile (``fast`` / ``balanced`` / ``deep``); None without a choice."""
    p = chosen()
    if p is None:
        return None
    return models_of(p, str(_stored().get("model") or "")).get(profile)


# core/llm.py jobs → the profile whose model they use.
JOB_PROFILES = {"chat": "balanced", "search": "balanced"}


def job_model(job: str) -> Optional[str]:
    """The chosen provider's model for a core/llm.py job (None: keep the built-in default)."""
    if job == "transcribe":
        p = chosen()
        return (p.transcribe or None) if p is not None else None
    return profile_model(JOB_PROFILES.get(job, "fast"))


def catalog() -> Optional[List[Dict[str, Any]]]:
    """The chat profiles (core/models.py) for the chosen provider other than
    Claude, as ``ASSISTANT_MODELS`` entries; None for Claude or no choice (the
    built-in catalog, tuned for Claude)."""
    p = chosen()
    if p is None or p.id == CLAUDE:
        return None
    m = models_of(p, str(_stored().get("model") or ""))
    deep = {} if p.local else {"reasoning_effort": "high"}
    return [
        {"id": "fast", "label": "Fast", "model": m["fast"], "description": "Quickest replies."},
        {"id": "balanced", "label": "Balanced", "model": m["balanced"],
         "description": "Better at multi-step tasks."},
        {"id": "deep", "label": "Deep", "model": m["deep"], "description": "Slowest and most capable.", **deep},
    ]


# --- The key in the environment --------------------------------------------------------------

def _operator_set(name: str) -> bool:
    return bool(name) and bool(os.getenv(name, "").strip()) and name not in _ours


def _put_env(name: str, value: str) -> None:
    if not name or _operator_set(name):
        return
    if value:
        os.environ[name] = value
        _ours.add(name)
    elif name in _ours:
        os.environ.pop(name, None)
        _ours.discard(name)


def apply() -> None:
    """Put the stored key (and Ollama's address) in the environment under the
    provider's own name, unless the operator set that name. Run at start and
    after every change; a key the credentials key cannot read is logged and left out."""
    for name in list(_ours):
        os.environ.pop(name, None)
    _ours.clear()
    p = chosen()
    if p is None:
        return
    stored = _stored()
    if p.local:
        _put_env(OLLAMA_BASE_ENV, str(stored.get("base_url") or ""))
        return
    token = str(stored.get("key") or "")
    if not token:
        return
    try:
        key = crypto.decrypt(token).get("key", "")
    except crypto.CredentialsKeyError:
        logger.error(f"The stored {p.label} key cannot be read with this credentials key: enter it again in "
                     "Settings → The model.")
        return
    _put_env(p.key_env, key)


def status() -> Dict[str, Any]:
    """What ``GET /api/llm`` answers: the choice, whether a key is set (and
    where from) and its last four characters, never the key."""
    p = chosen()
    stored = _stored()
    key_env = p.key_env if p is not None else PROVIDERS[CLAUDE].key_env
    from_env = _operator_set(key_env)
    stored_key = bool(stored.get("key")) and p is not None and not p.local
    return {
        "provider": p.id if p is not None else None,
        "providers": [x.public() for x in PROVIDERS.values()],
        "key": {"set": from_env or stored_key, "from_env": from_env,
                "last4": "" if from_env or not stored_key else str(stored.get("last4") or "")},
        "base_url": str(stored.get("base_url") or "") if p is not None and p.local else "",
        "model": str(stored.get("model") or "") if p is not None and p.local else "",
        "models": models_of(p, str(stored.get("model") or "")) if p is not None else dict(PROVIDERS[CLAUDE].models),
    }


# --- Changing it -----------------------------------------------------------------------------

def check(provider: str, key: str, base_url: str, model: str) -> Provider:
    """The provider of a requested change, with its fields checked (not tested against the service).

    Raises:
        Invalid: an unknown provider, a malformed key, address or model name, or no key at
            all (none sent, none stored for this provider, none in env).
    """
    p = PROVIDERS.get((provider or "").strip())
    if p is None:
        raise Invalid("provider", f"One of {', '.join(PROVIDERS)}.")
    if p.local:
        if base_url and not re.match(r"^https?://[^\s/]+", base_url):
            raise Invalid("base_url", "An address like http://localhost:11434.")
        if model and not re.fullmatch(r"[\w.:/-]{1,100}", model):
            raise Invalid("model", "A model name like qwen3 or llama3.1:8b.")
        return p
    stored = _stored()
    kept = stored.get("provider") == p.id and bool(stored.get("key"))
    if not key and not kept and not _operator_set(p.key_env):
        raise Invalid("key", f"Paste your {p.label} API key.")
    if key and (len(key) < 8 or len(key) > 500 or re.search(r"\s", key)):
        raise Invalid("key", "That does not look like an API key.")
    return p


def test_target(p: Provider, key: str, base_url: str, model: str) -> Dict[str, str]:
    """What to call to test a change: ``{model, api_key?, api_base?}`` (the
    fast model of the provider, with the new key or address)."""
    target = {"model": models_of(p, model)["fast"]}
    if p.local:
        target["api_base"] = base_url or os.getenv(OLLAMA_BASE_ENV, "").strip() or OLLAMA_DEFAULT_BASE
    elif key:
        target["api_key"] = key
    return target


def save(p: Provider, key: str, base_url: str, model: str, *, by: Optional[str]) -> Dict[str, Any]:
    """Store the choice (the key encrypted; an empty key keeps the one stored
    for the same provider) and apply it. Returns :func:`status`.

    Raises:
        CredentialsKeyError: the credentials key cannot encrypt.
    """
    old = _stored()
    value: Dict[str, Any] = {"provider": p.id}
    if p.local:
        value.update({"base_url": base_url.rstrip("/"), "model": model})
    elif key:
        value.update({"key": crypto.encrypt({"key": key}), "last4": key[-4:]})
    elif old.get("provider") == p.id and old.get("key"):
        value.update({"key": old["key"], "last4": old.get("last4", "")})
    server_settings.put(SETTING, value, by=by)
    forget_cache()
    apply()
    logger.info(f"LLM provider set to {p.label}" + (" with a new key" if key else ""))
    return status()


def configured() -> bool:
    """Whether a provider was chosen, or a key for the default (Claude) is set in env."""
    return chosen() is not None or _operator_set(PROVIDERS[CLAUDE].key_env)
