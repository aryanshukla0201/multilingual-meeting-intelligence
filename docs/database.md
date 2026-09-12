# Database

Phase 1 uses SQLAlchemy 2.0 with PostgreSQL as the runtime database and Alembic for migrations. The declarative metadata is defined in `apps/api/app/models/entities.py`; database sessions are created by `apps/api/app/db/session.py`.

## Start PostgreSQL

```powershell
docker compose up -d postgres
```

## Run migrations from the host

Use `localhost` because the host cannot resolve the Compose service name `postgres`:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://meeting:meeting@localhost:5432/meeting_intelligence"
alembic upgrade head
```

Inside the API container, the Compose URL uses `postgres` as the hostname and is already configured by `docker-compose.yml`.

## Test database

Phase 1 tests use an isolated in-memory SQLite database to verify metadata creation, indexes, foreign-key persistence, and Pydantic model validation without mutating the development PostgreSQL database.

The first migration creates the complete Phase 1 metadata as one transaction. Future schema changes should use focused Alembic revisions rather than editing the initial revision.
