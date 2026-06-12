"""Alembic migration environment — wired to Flask app context."""
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
import os, sys

# Make sure the project root is on sys.path so `app` is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import app, db  # noqa: E402  (must come after sys.path tweak)

config = context.config
fileConfig(config.config_file_name)
target_metadata = db.metadata

# Allow DATABASE_URL override at migration time (e.g. direct connection bypassing PgBouncer)
migration_url = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
if migration_url:
    config.set_main_option(
        "sqlalchemy.url",
        migration_url.replace("postgres://", "postgresql://", 1),
    )


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    with app.app_context():
        run_migrations_online()
