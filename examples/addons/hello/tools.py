"""The ``hello`` tools. The schema the model sees comes from the type hints and
the docstring; ``conn`` (the user's connection) is left out of it."""

from core.addons import Connection
from addons.hello.addon import addon


@addon.tool()
def hello_greet(conn: Connection, name: str) -> str:
    """Greet someone in the user's chosen words.

    Args:
        name: Who to greet.
    """
    return f"{conn.settings.get('greeting') or 'Hello'}, {name}!"
