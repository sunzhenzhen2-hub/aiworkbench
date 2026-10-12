from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin,
    ai_assist,
    auth,
    dsh,
    blockers,
    personal,
    progress_reports,
    projects,
    solutions,
    stats,
    tasks,
)
from app.core.database import Base, engine
from app.core.migrations import run_migrations
from app.services.init_service import init_rbac
from app.services.project_service import backfill_null_report_projects, ensure_daily_project
from app.core.database import async_session
from app.services.notify_scheduler import start_notify_scheduler, stop_notify_scheduler
from app.websocket.notifications import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_migrations()
    async with async_session() as db:
        await init_rbac(db)
        await ensure_daily_project(db)
        stats = await backfill_null_report_projects(db)
        from app.services.embedding_settings import load_embedding_config

        await load_embedding_config(db)
        await db.commit()
        moved = stats.get("matched", 0) + stats.get("created", 0) + stats.get("daily", 0)
        if moved:
            print(f"[startup] reclassified report items: {stats}")
    start_notify_scheduler()
    try:
        yield
    finally:
        await stop_notify_scheduler()


app = FastAPI(
    title="项目支撑工作台 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(blockers.router, prefix="/api")
app.include_router(personal.router, prefix="/api")
app.include_router(progress_reports.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(ai_assist.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(solutions.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(dsh.router, prefix="/api")
app.include_router(ws_router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
