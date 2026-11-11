"""Engine and session management (synchronous SQLAlchemy 2)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _sqlite_pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def build_engine(url: str, *, echo: bool = False) -> Engine:
    if url.startswith("sqlite"):
        in_memory = url in ("sqlite://", "sqlite:///:memory:")
        if not in_memory:
            db_path = url.split("///", 1)[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if in_memory else None,
        )
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
    return create_engine(url, echo=echo, pool_pre_ping=True)


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine = build_engine(url, echo=echo)
        self._sessionmaker = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    def dispose(self) -> None:
        self.engine.dispose()
