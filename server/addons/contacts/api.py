"""HTTP routes of the contacts addon: only the Google sign-in
(addons/google_account/api.py), registered here too so it is there whichever
Google addons are loaded."""

from typing import Any

from addons.google_account import api as google_api


def setup(app: Any) -> None:
    """Register the Google sign-in routes (once for every Google addon)."""
    google_api.setup(app)
