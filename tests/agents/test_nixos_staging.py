from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("LLM_MODEL_NAME", "test-model")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:1/v1")
os.environ.setdefault("OLLAMA_API_KEY", "sk-test")

from common.toolset_guards import STAGE_TOOL_NAME, StageRecordingToolset
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from remediation.agent import run_remediation
from remediation.models import RemediationDeps

from .test_untrusted import (
    _REM_FLUX_TOOLS,
    _REM_GIT_TOOLS,
    _STUB_RESULT,
    _CannedMCP,
    _rem_report,
)

_HOST = "vigil-worker-2"
_STAGE_ARGS = {"host": _HOST, "generation": 4}
_NIXOS_TOOLS = ("get_generations", "stage_generation")


async def _call(toolset: StageRecordingToolset, name: str) -> object:
    return await toolset.call_tool(name, _STAGE_ARGS, MagicMock(), MagicMock())


async def test_stage_recorder_records_nothing_on_a_transport_error() -> None:
    wrapped = AsyncMock()
    wrapped.call_tool.side_effect = RuntimeError("ssh: connection reset")
    staged: list[str] = []
    toolset = StageRecordingToolset(wrapped=wrapped, staged_hosts=staged)
    with pytest.raises(RuntimeError, match="connection reset"):
        await _call(toolset, STAGE_TOOL_NAME)
    assert staged == []


async def test_stage_recorder_ignores_other_tools() -> None:
    wrapped = AsyncMock()
    staged: list[str] = []
    toolset = StageRecordingToolset(wrapped=wrapped, staged_hosts=staged)
    assert await _call(toolset, "get_generations") is wrapped.call_tool.return_value
    assert staged == []


async def _stage(reply: object) -> list[str]:
    pending = [ToolCallPart(STAGE_TOOL_NAME, _STAGE_ARGS)]

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if pending:
            return ModelResponse(parts=[pending.pop()])
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, _STUB_RESULT)]
        )

    replies = {STAGE_TOOL_NAME: reply}
    deps = RemediationDeps(
        git_mcp=_CannedMCP(_REM_GIT_TOOLS, replies),
        flux_mcp=_CannedMCP(_REM_FLUX_TOOLS, replies),
        nixos_mcp=_CannedMCP(_NIXOS_TOOLS, replies),
    )
    staged: list[str] = []
    await run_remediation(
        deps,
        _rem_report(),
        source_branch="remediation/run-x",
        model=FunctionModel(model_fn),
        staged_hosts=staged,
    )
    return staged


async def test_run_remediation_records_a_returned_stage() -> None:
    assert await _stage("staged") == [_HOST]


async def test_run_remediation_records_no_failed_stage() -> None:
    failure = ModelRetry("StageGeneration: Process exited with status 4")
    assert await _stage(failure) == []
