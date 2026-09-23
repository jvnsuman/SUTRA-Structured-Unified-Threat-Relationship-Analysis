"""
api/main.py

FastAPI application entrypoint. Wires together auth (api/auth.py) and
the route modules (api/routes/) into one app.

Adds the alerts router (api/routes/alerts.py) for the sidebar-nav
dashboard's Recent Alerts panel, and the reports/settings routers
(api/routes/reports.py, api/routes/settings.py) for the Reports and
Settings pages. Also adds cross_case (api/routes/cross_case.py, under
the same /cases prefix as cases.router) for the cross-case entity
match panel, access_requests (api/routes/access_requests.py) for the
resulting request/approve/deny lifecycle, ledger
(api/routes/ledger.py) for the hash-chained integrity ledger's
verify/list endpoints, and users (api/routes/users.py) for the
admin-only badge-ID lookup the dashboard's Admin page uses to resolve
a badge ID into the user_id that POST /cases/{case_id}/assign
requires — everything else unchanged.

Startup also seeds the ledger's genesis entry (ledger.chain via
db.repository.ensure_genesis_entry) so a fresh deployment's chain
always has a well-defined starting point before anything else can
append to it.

Run locally with: uvicorn api.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from api import auth
from api.routes import access_requests, alerts, audit, cases, cross_case, evidence, ingestion, ledger, query, reports, users
from api.routes import settings as settings_routes
from config import get_settings
from db import repository as repo
from db.connection import SessionLocal, get_db, init_db
from schema.user import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the app starts up: creates any missing database
    tables so a fresh deployment doesn't need a separate manual
    migration step for the initial schema, then seeds the ledger's
    genesis entry if it doesn't already exist.
    """
    init_db()
    db = SessionLocal()
    try:
        repo.ensure_genesis_entry(db)
    finally:
        db.close()
    yield


def create_app() -> FastAPI:
    """App factory — instantiate the FastAPI app, configure CORS for
    the dashboard dev server, and register every route.
    """
    app = FastAPI(title="SUTRA Criminal Network Analysis API", lifespan=lifespan)
    settings = get_settings()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["health"])
    def health() -> dict:
        """Liveness check — used by orchestration/monitoring, not auth-gated."""
        return {"status": "ok"}

    @app.post("/auth/login", tags=["auth"])
    def login(credentials: dict, request: Request, db: Session = Depends(get_db)) -> dict:
        ip = request.client.host if request.client else "unknown"
        return auth.login_endpoint(credentials, db, ip_address=ip)

    @app.post("/auth/logout", tags=["auth"])
    def logout(token: str) -> dict:
        auth.logout_endpoint(token)
        return {"status": "logged_out"}

    @app.get("/auth/me", tags=["auth"])
    def me(user: User = Depends(auth.get_current_user)) -> dict:
        """Resolve the caller's own session token back into their
        name/role (same shape login returns), so the dashboard can
        restore a real session on page reload instead of the
        role-less `{ restored: true }` placeholder it previously fell
        back to (see App.jsx) — that placeholder made role-gated UI
        (e.g. the Admin nav item, the Analyst read-only banner)
        disappear or misbehave until the next full login.
        """
        return {"name": user.name, "role": user.role.value, "id": user.id, "badge_id": user.badge_id, "agency_id": user.agency_id}

    @app.post("/auth/change-password", tags=["auth"])
    def change_password(
        data: dict,
        user: User = Depends(auth.get_current_user),
        db: Session = Depends(get_db),
        authorization: str = Header(default=None),
    ) -> dict:
        current = authorization.split(" ", 1)[1].strip() if authorization and " " in authorization else None
        return auth.change_password_endpoint(user, data, db, keep_token=current)

    app.include_router(cases.router, prefix="/cases", tags=["cases"])
    app.include_router(ingestion.router, prefix="/ingest", tags=["ingestion"])
    app.include_router(query.router, prefix="/query", tags=["query"])
    app.include_router(evidence.router, prefix="/evidence", tags=["evidence"])
    app.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
    app.include_router(reports.router, prefix="/reports", tags=["reports"])
    app.include_router(settings_routes.router, prefix="/settings", tags=["settings"])
    app.include_router(cross_case.router, prefix="/cases", tags=["cross-case"])
    app.include_router(access_requests.router, prefix="/access-requests", tags=["access-requests"])
    app.include_router(ledger.router, prefix="/ledger", tags=["ledger"])
    app.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(audit.router, prefix="/audit", tags=["audit"])

    return app


app = create_app()
