"""Explicit-live-only local credentials. Importing this module reads no files."""

import os
from pathlib import Path

from dotenv import dotenv_values

from .providers import LiveClient
from .safety import ROOT


def load_live_credentials(roles, path: Path | None = None):
    """Load configured provider key variables from the project's .env only.

    Called after explicit live authorization and preflight gates, never in
    offline/dry-run paths. Existing environment variables take precedence.
    File interpolation and unrelated settings/global model overrides are disabled.
    """
    names = {
        role.api_key_env or LiveClient.DEFAULT_KEY_ENVS[role.provider] for role in roles if role.provider != "fake"
    }
    if not names:
        return
    source = path if path is not None else ROOT / ".env"
    if not source.is_file():
        return
    values = dotenv_values(source, interpolate=False)
    for name in names:
        value = values.get(name)
        if name not in os.environ and value:
            os.environ[name] = value
