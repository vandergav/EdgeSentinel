"""
FastAPI application factory for the EdgeOne agent platform.

This file (and everything under app/) contains ZERO agent logic. Its only
job is to host the existing, unmodified edgeone_agents package:
  - edgeone_agents.agent.root_agent is wrapped by ag_ui_adk.ADKAgent and
    exposed at /agui so CopilotKit (via the copilot-runtime bridge) can
    drive it over the AG-UI protocol.
  - Everything else the app needs (monitoring, incidents, settings, ...)
    is a plain REST router mounted under /api/*, added here as its own
    phase lands. edgeone_agents remains the single source of truth for
    all agent behavior — routers never reimplement anything it already does.
"""
import asyncio
import contextlib
import os
from pathlib import Path

from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from edgeone_agents.agent import root_agent

from .routers import health, incidents, monitoring, settings, webhooks
from .services.alarm_ingestion import IncidentStore
from .workers.incident_worker import run_once


def _enabled(name: str) -> bool:
    return os.environ.get(name, "false").lower() in {"1", "true", "yes"}


def create_app() -> FastAPI:
    app = FastAPI(
        title="EdgeOne Agent Platform API",
        description=(
            "Hosts the EdgeOne CDN/WAF ADK agent team over AG-UI for the CopilotKit "
            "frontend, plus REST APIs for the rest of the application."
        ),
        version="0.1.0",
    )

    origins = [
        o.strip()
        for o in os.environ.get(
            "BACKEND_CORS_ORIGINS", "http://localhost:5173,http://localhost:3001"
        ).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Vite selects the next free local port when 5173 is occupied, and a
        # browser can use 127.0.0.1 instead of localhost. The incident unread
        # header causes these otherwise-safe local GETs to preflight, so exact
        # port matching turns a harmless dev-port change into a 400. This
        # regex is deliberately local-only; deployed browser origins still
        # have to be listed explicitly in BACKEND_CORS_ORIGINS above.
        allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    database_path = Path(
        os.environ.get(
            "EDGEONE_APP_DB_PATH",
            str(Path(__file__).resolve().parents[1] / "data" / "edgeone_platform.sqlite3"),
        )
    )
    app.state.incident_store = IncidentStore(database_path)

    @app.on_event("startup")
    def initialize_persistence() -> None:
        """Create the Stage 1 incident tables before accepting callbacks."""
        app.state.incident_store.initialize()

    @app.on_event("startup")
    async def start_proactive_demo_worker() -> None:
        """Optionally process queued incident jobs without browser interaction.

        This is deliberately a demo-only, single-process loop. Production
        deployment should use a supervised worker service/queue instead.
        """
        if not _enabled("INCIDENT_PROACTIVE_WORKER_ENABLED"):
            return
        poll_seconds = max(1, int(os.environ.get("INCIDENT_PROACTIVE_WORKER_POLL_SECONDS", "2")))

        async def worker_loop() -> None:
            while True:
                try:
                    # The persisted UI switch is a live stop control; queued
                    # work remains untouched while proactive mode is off.
                    if app.state.incident_store.get_proactive_mode().get("enabled"):
                        processed = await asyncio.to_thread(run_once, app.state.incident_store)
                        await asyncio.sleep(0 if processed else poll_seconds)
                    else:
                        await asyncio.sleep(poll_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A job-level error is persisted by run_once. Do not let a
                    # transient supervisor error kill the demo worker loop.
                    await asyncio.sleep(poll_seconds)

        app.state.proactive_demo_worker_task = asyncio.create_task(worker_loop())

    @app.on_event("shutdown")
    async def stop_proactive_demo_worker() -> None:
        task = getattr(app.state, "proactive_demo_worker_task", None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # --- AG-UI: the existing agent team, unmodified, exposed for CopilotKit ---
    # ADKAgent is middleware, not a rewrite — it just turns root_agent into an
    # AG-UI-speaking service. All planning/tools/state stay exactly as built
    # in edgeone_agents.
    adk_agent = ADKAgent(
        adk_agent=root_agent,
        app_name=os.environ.get("ADK_APP_NAME", "edgeone_platform"),
        user_id=os.environ.get("ADK_DEFAULT_USER_ID", "demo_user"),
        session_timeout_seconds=int(os.environ.get("ADK_SESSION_TIMEOUT_SECONDS", "3600")),
        use_in_memory_services=True,
    )
    add_adk_fastapi_endpoint(app, adk_agent, path="/agui")

    # --- REST APIs: this is where future phases plug in ---
    # health is real; monitoring/incidents/settings are intentionally
    # honest placeholders for this phase (see their docstrings and the
    # top-level README's "Future Extensibility" section).
    app.include_router(health.router, prefix="/api/health", tags=["health"])
    app.include_router(monitoring.router, prefix="/api/monitoring", tags=["monitoring"])
    app.include_router(incidents.router, prefix="/api/incidents", tags=["incidents"])
    app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
    app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])

    return app


app = create_app()
