import pytest

from app.core.config import settings
from app.core.database import Base, async_session, engine
from app.core.migrations import run_migrations
from app.services.init_service import init_rbac

_schema_ready = False


@pytest.fixture(autouse=True)
def _legacy_ai_off_unless_test_enables(monkeypatch):
    """单测默认按老工作台：避免本机 .env 的 AGENT_NATIVE=true 误伤旧 AI 用例。"""
    monkeypatch.setattr(settings, "agent_native", False)


@pytest.fixture(autouse=True)
async def _prepare_schema():
    global _schema_ready
    if not _schema_ready:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await run_migrations()
        async with async_session() as db:
            await init_rbac(db)
        _schema_ready = True
    yield


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    """每个测试用完连接池，避免 asyncpg 跨事件循环复用连接。"""
    yield
    await engine.dispose()
