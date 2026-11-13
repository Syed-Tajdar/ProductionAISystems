def test_health_ok(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["env"] == "test"
    assert body["llm_provider"] in {"anthropic", "openai", "gemini"}


def test_health_hides_secrets(client):
    body = client.get("/health").json()
    assert set(body["configured"]) == {"supabase", "deepgram", "anthropic", "openai", "gemini"}
    assert all(isinstance(v, bool) for v in body["configured"].values())


def test_provider_switch_accepts_gemini_and_rejects_unknown():
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    settings = Settings(_env_file=None, llm_provider="gemini", gemini_api_key="x")
    assert settings.llm_provider == "gemini" and settings.gemini_model == "gemini-3.8-flash"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="mistral")


def test_any_non_empty_key_counts_as_configured():
    from fastapi.testclient import TestClient

    from app.config import Settings, get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, app_env="test", deepgram_api_key="any-non-empty-key", gemini_api_key="any-non-empty-key")
    try:
        configured = TestClient(app).get("/health").json()["configured"]
    finally:
        app.dependency_overrides.clear()
    assert configured["deepgram"] is True and configured["gemini"] is True
    assert configured["anthropic"] is False
