"""Catalog of LLM profiles a chat client can pick from.

A profile is a LiteLLM model id plus the settings that shape how it answers
(e.g. how hard it thinks). Clients only ever see and send the profile ``id``,
so the underlying model can change server-side without a client release.

The built-in catalog is Claude's; when the admin chose another provider in
the app (core/llm_provider.py) the catalog is that provider's models.

Configuration (both optional, above the provider chosen in the app):
  * ``ASSISTANT_MODELS``        — JSON list overriding the built-in catalog, e.g.
        [{"id": "fast", "label": "Fast", "model": "anthropic/claude-haiku-5-5",
          "output_config": {"effort": "low"}},
         {"id": "deep", "label": "Deep", "model": "anthropic/claude-opus-5-5",
          "reasoning_effort": "high", "description": "Slow, careful"}]
        (other keys go to the completion call as they are).
  * ``ASSISTANT_DEFAULT_MODEL`` — profile id used when a request names none
        (defaults to the first profile, ``fast`` in the built-in catalog).

Each access level (core/access.py) may use only some profiles: the catalog a
client gets, the default and the fallback for an id it may not use are per
level (:func:`catalog_payload`, :func:`resolve_profile`).

Telegram and background triggers do not go through this catalog; they keep
using the ``MODEL`` env var the orchestrator was built with.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core import llm_provider
from core.access import AccessLevel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    model: str
    description: str = ""
    # LiteLLM `reasoning_effort` ("low" | "medium" | "high"). When set, the
    # model also streams a summary of its thinking, which clients show live
    # (for Claude: adaptive thinking with a summarized display, and that effort).
    reasoning_effort: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def completion_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"model": self.model, **self.extra}
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        return kwargs

    def public(self) -> Dict[str, Any]:
        """What clients see: enough to render a picker, nothing secret."""
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "model": self.model,
            "shows_reasoning": bool(self.reasoning_effort),
        }


_BUILTIN_CATALOG: List[ModelProfile] = [
    ModelProfile(
        id="fast",
        label="Fast",
        model="anthropic/claude-haiku-5-5",
        description="Quickest replies. Best for commands and simple lookups.",
        # Haiku thinks by default (hidden here); low effort keeps it quick.
        extra={"output_config": {"effort": "low"}},
    ),
    ModelProfile(
        id="balanced",
        label="Balanced",
        model="anthropic/claude-sonnet-5-5",
        description="Thinks before acting and shows its reasoning. Better at multi-step tasks.",
        # Sonnet at medium often answers without thinking, so nothing would show.
        reasoning_effort="high",
    ),
    ModelProfile(
        id="deep",
        label="Deep",
        model="anthropic/claude-opus-5-5",
        description="Slowest and most capable. For research and tricky requests.",
        reasoning_effort="high",
    ),
]


def _profiles(items: Any) -> List[ModelProfile]:
    """``ASSISTANT_MODELS``-style entries as profiles.

    Raises:
        Exception: a malformed entry, or none.
    """
    profiles = []
    for item in items:
        known = {"id", "label", "model", "description", "reasoning_effort"}
        profiles.append(ModelProfile(
            id=str(item["id"]),
            label=str(item.get("label") or item["id"]),
            model=str(item["model"]),
            description=str(item.get("description") or ""),
            reasoning_effort=item.get("reasoning_effort") or None,
            extra={k: v for k, v in item.items() if k not in known},
        ))
    if not profiles:
        raise ValueError("empty list")
    return profiles


def _env_catalog() -> Optional[List[ModelProfile]]:
    raw = os.getenv("ASSISTANT_MODELS", "").strip()
    if not raw:
        return None
    try:
        return _profiles(json.loads(raw))
    except Exception as e:
        logger.error("Ignoring malformed ASSISTANT_MODELS (%s); using the built-in catalog", e)
        return None


_ENV_CATALOG: Optional[List[ModelProfile]] = _env_catalog()


def _catalog() -> List[ModelProfile]:
    """The catalog now: ``ASSISTANT_MODELS``, else the chosen provider's, else the built-in (Claude) one."""
    if _ENV_CATALOG is not None:
        return list(_ENV_CATALOG)
    chosen = llm_provider.catalog()
    return _profiles(chosen) if chosen else list(_BUILTIN_CATALOG)


def list_profiles() -> List[ModelProfile]:
    return _catalog()


def default_profile() -> ModelProfile:
    catalog = _catalog()
    wanted = os.getenv("ASSISTANT_DEFAULT_MODEL", "").strip()
    for p in catalog:
        if p.id == wanted:
            return p
    if wanted:
        logger.warning("ASSISTANT_DEFAULT_MODEL=%r is not in the catalog; using %r", wanted, catalog[0].id)
    return catalog[0]


def profiles_for(level: AccessLevel) -> List[ModelProfile]:
    """The profiles ``level`` may use, in catalog order. A catalog with none
    of them (a custom ``ASSISTANT_MODELS``) leaves the level the default
    profile alone, so chat keeps working."""
    allowed = [p for p in _catalog() if level.may_use_profile(p.id)]
    if not allowed:
        logger.warning("Access level %r may use no profile of the catalog; giving it the default", level.name)
        return [default_profile()]
    return allowed


def default_profile_for(level: AccessLevel) -> ModelProfile:
    """The default profile when ``level`` may use it, else the first one it may use."""
    default = default_profile()
    allowed = profiles_for(level)
    return default if default in allowed else allowed[0]


def resolve_profile(model_id: Optional[str], level: AccessLevel) -> ModelProfile:
    """Map a client-supplied profile id to a profile ``level`` may use:
    unknown ids, and ids of profiles the level may not use, get the level's
    default."""
    allowed = profiles_for(level)
    for p in allowed:
        if p.id == model_id:
            return p
    if model_id:
        known = any(p.id == model_id for p in _catalog())
        why = f"Not allowed for access level {level.name!r}:" if known else "Unknown"
        logger.warning("%s model profile %r requested; using the default", why, model_id)
    return default_profile_for(level)


def catalog_payload(level: AccessLevel) -> Dict[str, Any]:
    """The model picker for ``level``: its default and the profiles it may use."""
    return {
        "default": default_profile_for(level).id,
        "models": [p.public() for p in profiles_for(level)],
    }
