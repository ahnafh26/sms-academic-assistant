"""Alembic environment: the app's own settings and models are the source of truth."""

from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (register tables)
from app.config import get_settings
from app.db import Base, UTCDateTime, make_engine, normalize_url

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)


def render_item(type_, obj, autogen_context):
    """UTCDateTime is plain timestamptz in the database; migrations shouldn't import app code for it."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


OPTIONS = dict(target_metadata=Base.metadata, render_item=render_item, compare_type=True)


def run_offline() -> None:
    context.configure(url=normalize_url(get_settings().database_url), literal_binds=True, **OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    with make_engine().connect() as connection:
        context.configure(connection=connection, **OPTIONS)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
