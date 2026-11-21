"""Tool schemas from Python signatures (OpenAI-compatible, also used by LiteLLM).

A tool's schema comes from its type hints (Pydantic) and its Google-style
docstring. The first parameter, ``conn`` (the user's
:class:`~integrations.framework.store.Connection`), is never part of it.
"""

import inspect
import logging
from typing import Any, Callable, Dict, List, Tuple

from pydantic import TypeAdapter

logger = logging.getLogger(__name__)

# Parameters that never appear in a schema (besides the leading ``conn``).
_SKIPPED = ("self", "args", "kwargs")


def parse_docstring(doc: str) -> Tuple[str, Dict[str, str]]:
    """Parses a docstring into a main description and parameter descriptions.

    Supports Google and Sphinx style docstrings.
    """
    if not doc:
        return "", {}

    lines = doc.split("\n")
    main_desc_lines: List[str] = []
    param_descs: Dict[str, str] = {}

    in_args_section = False

    for line in lines:
        stripped = line.strip()

        # Detect start of parameter sections
        if stripped.lower() in ("args:", "parameters:", "arguments:"):
            in_args_section = True
            continue
        # Detect start of other sections to exit parameter parsing
        elif stripped.lower() in ("returns:", "raises:", "example:", "examples:", "yields:"):
            in_args_section = False
            continue

        if not in_args_section:
            if not stripped:
                continue
            main_desc_lines.append(stripped)
        else:
            # Parse parameters: e.g. "param_name (type): description" or "param_name: description"
            if ":" in stripped:
                name_part, desc_part = stripped.split(":", 1)
                name_part = name_part.strip()
                # Remove common bullet points
                if name_part.startswith(("- ", "* ", "• ")):
                    name_part = name_part[2:]
                elif name_part.startswith(("-", "*")):
                    name_part = name_part[1:]

                # Strip type annotations in parentheses, e.g. "param (str)" -> "param"
                name_part = name_part.split("(")[0].strip()

                if name_part.isidentifier():
                    param_descs[name_part] = desc_part.strip()
            elif stripped and param_descs:
                # Append to last parsed parameter description if it's a multiline block
                last_param = list(param_descs.keys())[-1]
                param_descs[last_param] += " " + stripped

    main_desc = " ".join(main_desc_lines).strip()
    return main_desc, param_descs


def tool_parameters(func: Callable[..., Any]) -> List[inspect.Parameter]:
    """The parameters the model fills: every one after ``conn``.

    Raises:
        TypeError: the first parameter is not ``conn``.
    """
    params = list(inspect.signature(func).parameters.values())
    if not params or params[0].name != "conn" or params[0].kind not in (
            inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
        raise TypeError(f"Tool {func.__name__}: the first parameter must be the connection, named 'conn'.")
    return params[1:]


def generate_schema(name: str, func: Callable[..., Any]) -> Dict[str, Any]:
    """The function-calling schema of a tool."""
    doc = inspect.getdoc(func) or ""
    main_desc, param_descs = parse_docstring(doc)

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param in tool_parameters(func):
        param_name = param.name
        if param_name in _SKIPPED:
            continue

        annotation = param.annotation

        # Generate type schema using Pydantic
        if annotation == inspect.Parameter.empty:
            param_schema: Dict[str, Any] = {"type": "string"}
        else:
            try:
                param_schema = TypeAdapter(annotation).json_schema()
                # Clean up Pydantic-specific metadata not required by LLM schemas
                param_schema.pop("title", None)
            except Exception as e:
                logger.warning(f"Failed to generate JSON schema for param {param_name} of type {annotation}: {e}. "
                               "Falling back to string.")
                param_schema = {"type": "string"}

        # Inject description from docstring if available
        if param_name in param_descs:
            param_schema["description"] = param_descs[param_name]

        properties[param_name] = param_schema

        # If the parameter has no default value, it is required
        if param.default == inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": main_desc or f"Execute {name}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }
    }


def flatten_for_realtime(schema: Dict[str, Any]) -> Dict[str, Any]:
    """A chat tool schema in the flat shape of the OpenAI Realtime API."""
    if schema.get("type") == "function" and "function" in schema:
        fn = schema["function"]
        return {
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        }
    return schema
