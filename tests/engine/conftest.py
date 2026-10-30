import pytest
from pytest_socket import disable_socket, enable_socket


@pytest.fixture(autouse=True)
def _no_network():
    """Engine tests run with the network blocked: patient data never needs a socket."""
    disable_socket(allow_unix_socket=True)
    yield
    enable_socket()


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    from ehds import audit, workspace
    monkeypatch.setattr(audit, "LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(workspace, "ROOT", tmp_path / "ws")


@pytest.fixture(autouse=True)
def _fresh_memory():
    from ehds import ai_check, evidence, memory
    memory.reset()
    ai_check._cache.clear()
    evidence._cache.clear()
