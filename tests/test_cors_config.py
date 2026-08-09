"""
Tests for CORS configuration.

The CORS middleware reads origins from settings.CORS_ORIGINS, which
defaults to the Next.js and Angular dev servers. The wildcard "*" is
allowed only when AUTH_REQUIRED=False; with AUTH_REQUIRED=True it's
rejected at startup because allow_credentials=True +
allow_origins=["*"] is a browser security antipattern.

These tests don't spin up a real ASGI client — they verify the
configuration logic that runs at app-construction time, which is
where the security guarantee lives.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


def _reload_app_with_settings(**overrides):
    """Reimport app.api.main with patched settings and return the app.

    The app object is constructed at import time, so to test different
    CORS_ORIGINS / AUTH_REQUIRED combinations we have to reload the
    module after patching the settings it reads.
    """
    # Patch settings attributes before reloading main.
    with patch("app.core.config.settings") as mock_settings:
        # Copy real settings, then apply overrides.
        from app.core.config import Settings

        real = Settings()
        for k, v in vars(real).items():
            setattr(mock_settings, k, v)
        for k, v in overrides.items():
            setattr(mock_settings, k, v)
        # Reimport main so the module-level middleware setup runs again.
        import sys

        if "app.api.main" in sys.modules:
            del sys.modules["app.api.main"]
        import app.api.main as main_mod

        return main_mod.app


class TestCorsDefaults:
    def test_default_origins_include_dev_servers(self):
        """The defaults cover the Next.js (3000) and Angular (4200) dev servers."""
        from app.core.config import Settings

        s = Settings()
        assert "http://localhost:3000" in s.CORS_ORIGINS
        assert "http://localhost:4200" in s.CORS_ORIGINS
        assert "*" not in s.CORS_ORIGINS


class TestCorsWildcardGuard:
    def test_wildcard_with_auth_required_rejected_at_startup(self):
        """The combination allow_origins=['*'] + allow_credentials=True
        is a browser security antipattern: any site can issue
        authenticated requests to the API. Reject it at startup when
        AUTH_REQUIRED=True so a misconfiguration can't ship.
        """
        with pytest.raises(RuntimeError, match="CORS_ORIGINS=\\['\\*'\\] is not allowed"):
            _reload_app_with_settings(CORS_ORIGINS=["*"], AUTH_REQUIRED=True)

    def test_wildcard_with_auth_not_required_still_allowed(self):
        """In single-player mode (AUTH_REQUIRED=False), the wildcard is
        still permitted for parity with the old behaviour. A production
        deployment should set explicit origins regardless."""
        # Should NOT raise.
        app = _reload_app_with_settings(
            CORS_ORIGINS=["*"], AUTH_REQUIRED=False
        )
        assert app is not None

    def test_explicit_origins_with_auth_required_accepted(self):
        """The intended production configuration: explicit origins
        paired with AUTH_REQUIRED=True."""
        app = _reload_app_with_settings(
            CORS_ORIGINS=["https://papercraft.example.com"],
            AUTH_REQUIRED=True,
        )
        assert app is not None


class TestCorsEnvParsing:
    def test_json_array_env_var_parses(self, monkeypatch):
        """pydantic-settings v2 parses list-typed env vars as JSON."""
        monkeypatch.setenv(
            "CORS_ORIGINS",
            '["https://a.example.com","https://b.example.com"]',
        )
        from app.core.config import Settings

        s = Settings()
        assert s.CORS_ORIGINS == [
            "https://a.example.com",
            "https://b.example.com",
        ]

    def test_single_origin_env_var_parses(self, monkeypatch):
        """A single-element JSON array is valid."""
        monkeypatch.setenv("CORS_ORIGINS", '["https://only.example.com"]')
        from app.core.config import Settings

        s = Settings()
        assert s.CORS_ORIGINS == ["https://only.example.com"]
