"""Offline network fence and source/config-bound acceptance receipts."""

import os
import socket
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from .storage import digest

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def offline_network():
    def blocked(*args, **kwargs):
        raise RuntimeError("Outbound network is blocked in offline mode")

    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
    ):
        yield


def fingerprint(config):
    paths = list((ROOT / "src/collusion_detection").rglob("*.py"))
    paths += list((ROOT / "src/collusion_detection/prompts").rglob("*.txt"))
    paths += list((ROOT / "tests/detection").rglob("*.py"))
    # Include the reused mechanics and the small private-context hook.
    paths += list((ROOT / "src/continuous_double_auction").rglob("*.py"))
    paths += [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    return digest(
        {
            "files": {str(p.relative_to(ROOT)): digest(p.read_text()) for p in sorted(paths)},
            "config": config.model_dump(),
        }
    )


def require_acceptance(path, config):
    import json

    receipt = json.loads(Path(path).read_text())
    if receipt.get("fingerprint") != fingerprint(config) or receipt.get("status") != "passed":
        raise ValueError("Offline acceptance receipt is missing/stale for this source/config; run doctor --acceptance")
    return receipt


def offline_test_environment():
    """Read only noncredential environment fields; tests never inherit provider keys."""
    allowed = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
    result = {name: os.environ[name] for name in allowed if name in os.environ}
    result["MPLCONFIGDIR"] = str(ROOT / "detection_outputs/.matplotlib")
    return result
