import pytest


def test_get_settings_reads_from_env(settings_env) -> None:
    from bot.config import get_settings

    settings = get_settings()
    assert settings.bot_token == "123456:ABCDEF_fake_but_valid_shape"
    assert settings.poll_interval_minutes == 5
    assert settings.database_url.endswith("test.db")
    assert settings.log_level == "INFO"
    assert settings.environment == "production"
    assert settings.sentry_dsn == ""
    assert settings.catalog_base_url == "https://registrar.nu.edu.kz"
    assert settings.catalog_term_id == "824"
    assert settings.scrape_min_interval_seconds == 180
    assert settings.catalog_ignore_tls_errors is True


def test_get_settings_sentry_dsn_optional(
    settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.config import get_settings

    monkeypatch.setenv(
        "SENTRY_DSN",
        "https://examplePublicKey@o0.ingest.sentry.io/0",
    )
    get_settings.cache_clear()

    settings = get_settings()
    assert "ingest.sentry.io" in settings.sentry_dsn


def test_get_settings_is_cached(settings_env) -> None:
    from bot.config import get_settings

    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2

