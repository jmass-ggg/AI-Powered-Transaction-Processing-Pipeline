import time

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db():
    """
    FastAPI dependency that yields a database session and closes it after the request.

    Usage:
        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries: int = 10, delay_seconds: int = 2) -> None:
    """
    Create all database tables defined in app/models.py.

    Retries up to `retries` times with `delay_seconds` between attempts.
    This handles the race condition where the API or worker starts before
    PostgreSQL is fully ready inside Docker Compose.

    In production, replace this with Alembic migrations.
    """
    from app import models  # noqa: F401 — import required so SQLAlchemy registers the models

    last_error = None
    for _ in range(retries):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError as exc:
            last_error = exc
            time.sleep(delay_seconds)

    if last_error:
        raise last_error
