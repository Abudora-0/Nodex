import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Keep state, history and backups out of the real ~/.nodex."""
    home = tmp_path_factory.mktemp("nodex-home")
    monkeypatch.setenv("NODEX_HOME", str(home))
    monkeypatch.delenv("NODEX_VAULT", raising=False)
    return home
