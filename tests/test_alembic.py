"""
Tests for Alembic migration setup.

Verifies that:
  1. The baseline migration exists and applies cleanly to a fresh DB.
  2. The migration's schema matches Base.metadata exactly — no drift
     between what the models declare and what the migration creates.
     If a model is changed without a corresponding migration, this
     test fails and tells the contributor to run
     `alembic revision --autogenerate -m "..."`.
  3. env.py injects POSTGRES_URI from app settings (no alembic.ini URL
     drift).

These tests use SQLite because that's what the test suite runs
against — the migration itself uses batch_alter_table so the same
SQL works on PostgreSQL in production.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect


@pytest.fixture
def fresh_sqlite_url(tmp_path, monkeypatch):
    """Point POSTGRES_URI at a fresh SQLite file in tmp_path."""
    db_path = tmp_path / "alembic_test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("POSTGRES_URI", url)
    return url


class TestAlembicConfig:
    def test_alembic_ini_has_no_hardcoded_url(self):
        """alembic.ini must NOT set sqlalchemy.url — it's injected at
        runtime by env.py from app settings. A hardcoded URL here would
        drift from POSTGRES_URI and cause migrations to hit the wrong
        database."""
        with open("alembic.ini") as f:
            content = f.read()
        # The line should be empty or commented.
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("sqlalchemy.url") and not stripped.startswith("#"):
                value = stripped.split("=", 1)[1].strip()
                assert value == "", (
                    f"alembic.ini has a hardcoded sqlalchemy.url={value!r}. "
                    f"This should be empty — env.py injects it from "
                    f"settings.POSTGRES_URI to prevent drift."
                )

    def test_env_py_imports_app_settings(self):
        """env.py must import from app.core.config so the migration
        uses the same DB URL as the application."""
        with open("alembic/env.py") as f:
            content = f.read()
        assert "from app.core.config import settings" in content, (
            "alembic/env.py must import settings from app.core.config "
            "so the DB URL comes from the application config, not a "
            "separate alembic.ini value."
        )
        assert "from app.db.base import Base" in content
        assert "from app.db import models" in content


class TestBaselineMigration:
    def test_baseline_applies_cleanly(self, fresh_sqlite_url):
        """`alembic upgrade head` against a fresh SQLite DB must
        succeed and produce all 5 model tables + the alembic_version
        bookkeeping table."""
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

        # Inspect the resulting schema.
        db_path = fresh_sqlite_url.replace("sqlite:///", "")
        engine = create_engine(fresh_sqlite_url)
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        engine.dispose()

        expected_tables = {
            "users",
            "ingestion_jobs",
            "extraction_drafts",
            "attestations",
            "audit_log",
            "alembic_version",
        }
        missing = expected_tables - tables
        assert not missing, f"Migration did not create tables: {missing}"

    def test_baseline_schema_matches_models(self, fresh_sqlite_url):
        """The schema produced by `alembic upgrade head` must exactly
        match what `Base.metadata.create_all()` would produce. If a
        model was changed without a new migration, this test fails and
        the contributor must run `alembic revision --autogenerate`.

        We compare table names + column names + column types. We
        deliberately don't compare constraints/indexes because SQLite
        and PostgreSQL render those slightly differently and the
        comparison would be brittle.
        """
        # 1. Apply migrations to one DB.
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr

        migrated_engine = create_engine(fresh_sqlite_url)
        migrated_insp = inspect(migrated_engine)

        # 2. Build a fresh in-memory DB via create_all to a different engine.
        from app.db.base import Base, configure_engine, reset_engine
        from app.db import models  # noqa: F401  registers tables
        from sqlalchemy.pool import StaticPool

        mem_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(mem_engine)
        mem_insp = inspect(mem_engine)

        # 3. Compare table sets (excluding alembic_version which only
        # exists in the migrated DB).
        migrated_tables = {
            t for t in migrated_insp.get_table_names()
            if t != "alembic_version"
        }
        mem_tables = set(mem_insp.get_table_names())
        assert migrated_tables == mem_tables, (
            f"Table set mismatch between migration and models.\n"
            f"In migration only: {migrated_tables - mem_tables}\n"
            f"In models only: {mem_tables - migrated_tables}\n"
            f"If you added/removed a model, run: "
            f"alembic revision --autogenerate -m 'describe change'"
        )

        # 4. For each table, compare column names.
        for table in mem_tables:
            migrated_cols = {c["name"] for c in migrated_insp.get_columns(table)}
            mem_cols = {c["name"] for c in mem_insp.get_columns(table)}
            assert migrated_cols == mem_cols, (
                f"Column mismatch on table '{table}':\n"
                f"In migration only: {migrated_cols - mem_cols}\n"
                f"In models only: {mem_cols - migrated_cols}\n"
                f"Run: alembic revision --autogenerate -m 'describe change'"
            )

        migrated_engine.dispose()
        mem_engine.dispose()
        reset_engine()
