"""Acceptance tests are air-gapped, including all subprocess CLI offline tests."""

import os
import socket
from pathlib import Path

os.environ["MPLCONFIGDIR"] = str(Path(__file__).resolve().parents[2] / "detection_outputs/.matplotlib")
import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Offline acceptance attempted outbound network access")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
