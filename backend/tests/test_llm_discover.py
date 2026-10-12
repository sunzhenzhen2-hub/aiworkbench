"""远端模型列表发现。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm_client import list_openai_compatible_models


@pytest.mark.asyncio
async def test_list_models_ok():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [{"id": "b-model"}, {"id": "a-model"}, {"id": "a-model"}]
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.llm_client.httpx.AsyncClient", return_value=mock_client):
        ok, models, msg = await list_openai_compatible_models(
            "https://api.example.com/v1", "sk-test"
        )
    assert ok is True
    assert models == ["a-model", "b-model"]
    assert "2" in msg
    mock_client.get.assert_awaited()
    args, kwargs = mock_client.get.await_args
    assert args[0] == "https://api.example.com/v1/models"


@pytest.mark.asyncio
async def test_list_models_missing_key():
    ok, models, msg = await list_openai_compatible_models("https://api.example.com/v1", "")
    assert ok is False
    assert models == []
    assert "API Key" in msg


@pytest.mark.asyncio
async def test_list_models_http_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "unauthorized"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.llm_client.httpx.AsyncClient", return_value=mock_client):
        ok, models, msg = await list_openai_compatible_models(
            "https://api.example.com/v1", "sk-bad"
        )
    assert ok is False
    assert models == []
    assert "401" in msg
