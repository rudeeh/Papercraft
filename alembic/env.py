"""Alembic environment for Papercraft.

Wires Alembic to the app's own SQLAlchemy ``Base.metadata`` and
``settings.POSTGRES_URI`` so migrations don't need a separately-maintained
``alembic.ini`` URL (which inevitably drifts from the real config).

The metadata import has a side effect: importing ``app.db.models``
registers every ORM class on ``Base.metadata``. Without that import,
autogenerate would emit empty migrations.

Both online and offline modes honor ``compare_type=True`` so a column
type change (e.g. ``String(64)`` -> ``String(128)``) is detected as a
migration, not silently ignored.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make `app.*` importable when alembic is invoked from the repo root.
# This matters because alembic loads env.py via importlib, not via the
# package's normal entry point, so the pythonpath isn't always set up.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: E402,F401  (registers tables on Base.metadata)

# This is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata that autogenerate diffs against. Comes from the app's
# own Base, so changes to models.py are picked up automatically.
target_metadata = Base.metadata

# Inject the live database URL from app settings so alembic.ini doesn't
# need its own sqlalchemy.url (which would drift from POSTGRES_URI).
config.set_main_option("sqlalchemy.url", settings.POSTGRES_URI)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to stdout without connecting to the database. Useful for
    reviewing what a migration will do before applying it, or for
    piping to a DBA who runs it manually.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,  # SQLite-friendly: emits batch ops
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and runs the migration in a real transaction.
    This is the normal mode for `alembic upgrade head`.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,  # SQLite-friendly: emits batch ops
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
