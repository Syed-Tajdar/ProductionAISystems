import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app

Settings.model_config["env_file"] = None  # the tests never read a local .env


@pytest.fixture
def client():
    # Ignore any local .env so tests never depend on real keys.
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, app_env="test")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Tests never read keys or settings from the shell environment."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fresh_rate_limiter():
    from app import auth

    auth.limiter = auth.RateLimiter()
    auth.public_reads = auth.RateLimiter(window=60)
    yield
