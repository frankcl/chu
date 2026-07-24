"""Shared fixtures for Web API endpoint tests."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web_api import auth


async def _empty_async_gen(*args, **kwargs):
    """Async generator that yields nothing (simulates a finished stream)."""
    return
    yield


@pytest.fixture()
def mock_react_agent():
    agent = MagicMock()
    agent.astream = _empty_async_gen
    return agent


@pytest.fixture()
def mock_plan_execute_agent():
    agent = MagicMock()
    agent.astream = _empty_async_gen
    return agent


@pytest.fixture()
def client(mock_react_agent, mock_plan_execute_agent):
    """Authenticated TestClient with real LLM construction disabled."""
    with (
        patch("web_api.runtime.create_agent", return_value=mock_react_agent),
        patch(
            "web_api.runtime.create_plan_execute_agent",
            return_value=mock_plan_execute_agent,
        ),
    ):
        import server

        with patch.object(auth.hylian_client, "get_user", return_value=MagicMock()):
            with TestClient(
                server.app,
                raise_server_exceptions=False,
                headers={"Authorization": "Bearer test-token"},
            ) as test_client:
                yield test_client

