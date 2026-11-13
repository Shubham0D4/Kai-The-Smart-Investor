"""
Database configuration using SQLAlchemy (SQLite by default, KAI_DB_URL to override).

There is no module-level engine: each app instance builds its own (gateway.app.create_app), so
importing the gateway has no side effects and tests can point an app at a temporary database.
"""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def make_engine(db_url: str) -> Engine:
    # check_same_thread=False: debates run in worker threads and use their own sessions.
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
