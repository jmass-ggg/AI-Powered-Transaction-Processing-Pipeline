from fastapi import FastAPI

from app.api import router
from app.database import init_db

app = FastAPI(
    title="Transaction Processing Pipeline",
    description="Accepts a CSV of raw financial transactions, processes them asynchronously, detects anomalies, classifies categories via LLM, and returns structured results.",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup():
    # Creates all database tables on first boot.
    # In production this should be replaced with Alembic migrations.
    init_db()


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(router)
