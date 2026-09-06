"""LiteLLM orchestrator routing tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prompt_matrix.llm.orchestrator import model_for_node_type, orchestrate_node_compilation


@pytest.mark.parametrize(
    ("node_type", "expected"),
    [
        ("table", "table-parsing"),
        ("financial_grid", "table-parsing"),
        ("image", "vision-analysis"),
        ("chart", "vision-analysis"),
        ("paragraph", "text-reasoning"),
    ],
)
def test_model_for_node_type(node_type, expected):
    assert model_for_node_type(node_type) == expected


@pytest.mark.asyncio
async def test_orchestrate_node_compilation_selects_model():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Draft text"))]

    mock_router = MagicMock()
    mock_router.acompletion = AsyncMock(return_value=mock_response)

    with patch("prompt_matrix.llm.orchestrator.get_router", return_value=mock_router):
        out = await orchestrate_node_compilation(
            "table",
            [{"role": "user", "content": "Parse this grid"}],
        )

    assert out == "Draft text"
    mock_router.acompletion.assert_awaited_once()
    assert mock_router.acompletion.await_args.kwargs["model"] == "table-parsing"
