import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import asyncpg

from ledgercore.config import settings
from ledgercore.db import init_db_pool, close_db_pool, get_connection, apply_schema
from ledgercore.exceptions import LedgerCoreError
from ledgercore.services.seed_service import seed_chart_of_accounts

from ledgercore.api.routes import (
    accounts,
    transfers,
    transactions,
    audit,
    race_demo,
    health,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database pool & seed schema if needed
    pool = await init_db_pool()
    async with get_connection() as conn:
        # Check if schema exists, if not apply it
        tables = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'ledger_entries';"
        )
        if tables == 0:
            await apply_schema(conn)

        # Seed initial chart of accounts if empty
        await seed_chart_of_accounts(conn)

    yield

    # Shutdown: gracefully close connection pool
    await close_db_pool()


def create_app() -> FastAPI:
    app = FastAPI(
        title="LedgerCore API",
        description="Production-Grade ACID Double-Entry Financial Ledger Engine",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception Handlers: Stripe-style RFC 7807 error envelopes
    @app.exception_handler(LedgerCoreError)
    async def ledger_error_handler(request: Request, exc: LedgerCoreError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "status": exc.status_code,
                }
            },
        )

    @app.exception_handler(asyncpg.PostgresError)
    async def postgres_error_handler(request: Request, exc: asyncpg.PostgresError):
        # Handle specific custom SQLSTATE codes
        if getattr(exc, "sqlstate", None) == "P0001":
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "error": {
                        "code": "zero_sum_invariant_violation",
                        "message": str(exc),
                        "status": 422,
                    }
                },
            )
        elif getattr(exc, "sqlstate", None) == "P0002":
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": {
                        "code": "ledger_immutable",
                        "message": "Ledger entries are strictly append-only and immutable. Updates and deletes are forbidden.",
                        "status": 403,
                    }
                },
            )
        elif getattr(exc, "sqlstate", None) == "40P01":
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": {
                        "code": "deadlock_detected",
                        "message": "Database deadlock detected during transaction execution.",
                        "status": 500,
                    }
                },
            )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "database_error",
                    "message": str(exc),
                    "status": 500,
                }
            },
        )

    # Register API Routers
    app.include_router(health.router, prefix="/v1")
    app.include_router(accounts.router, prefix="/v1")
    app.include_router(transfers.router, prefix="/v1")
    app.include_router(transactions.router, prefix="/v1")
    app.include_router(audit.router, prefix="/v1")
    app.include_router(race_demo.router, prefix="/v1")

    # Static assets and dashboard mounting
    frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/dashboard", methods=["GET", "HEAD"], include_in_schema=False)
        async def serve_dashboard():
            return FileResponse(frontend_dir / "index.html")

    return app


app = create_app()
