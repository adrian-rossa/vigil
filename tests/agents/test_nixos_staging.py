from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("LLM_MODEL_NAME", "test-model")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:1/v1")
os.environ.setdefault("OLLAMA_API_KEY", "sk-test")

import orchestrator.agent as orch_mod
import watchdog.agent as wd_mod
from common.toolset_guards import (
    STAGE_TOOL_NAME,
    UNCONFIRMED_STAGE_HOST,
    StageRecordingToolset,
)
from diagnosis.context import DiagnosisContext
from orchestrator.agent import run_orchestration
from orchestrator.models import FaultEvent, RunRecord
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage
from remediation.agent import run_remediation
from remediation.models import RemediationDeps

from .test_orchestrator import _canned_baseline, _canned_report_with_action
from .test_untrusted import (
    _REM_FLUX_TOOLS,
    _REM_GIT_TOOLS,
    _STUB_RESULT,
    _CannedMCP,
    _rem_report,
)

SCALE = 1 / 200
WINDOW_S = 300
POLL_S = 5
TIMER_MIN_S = 180
BOOT_S = 30
COMMITTED = 5
STAGED = 4
HOST = "vigil-worker-1"
OTHER_HOST = "vigil-worker-2"
BASELINE_BRANCH = "chore/eval-cluster-baseline"
COMMIT_TOOL_NAME = "commit_generation"
COMMIT_FAILED = "commit_generation_failed"
_HOST_TOOLS = (
    "get_generations",
    STAGE_TOOL_NAME,
    COMMIT_TOOL_NAME,
    "get_systemd_status",
)
_STAGE_ARGS = {"host": OTHER_HOST, "generation": STAGED}
_NIXOS_TOOLS = ("get_generations", "stage_generation")


async def _call(toolset: StageRecordingToolset, name: str) -> object:
    return await toolset.call_tool(name, _STAGE_ARGS, MagicMock(), MagicMock())


async def test_stage_recorder_marks_a_transport_error_unconfirmed() -> None:
    wrapped = AsyncMock()
    wrapped.call_tool.side_effect = RuntimeError("ssh: connection reset")
    staged: list[str] = []
    toolset = StageRecordingToolset(wrapped=wrapped, staged_hosts=staged)
    with pytest.raises(RuntimeError, match="connection reset"):
        await _call(toolset, STAGE_TOOL_NAME)
    assert staged == [UNCONFIRMED_STAGE_HOST]


async def test_stage_recorder_waits_for_a_held_stage_lock() -> None:
    wrapped = AsyncMock()
    lock = asyncio.Lock()
    toolset = StageRecordingToolset(wrapped=wrapped, staged_hosts=[], stage_lock=lock)
    await lock.acquire()
    call = asyncio.create_task(_call(toolset, STAGE_TOOL_NAME))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    wrapped.call_tool.assert_not_called()
    lock.release()
    await call
    wrapped.call_tool.assert_awaited_once()
    assert not lock.locked()


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
    assert await _stage("staged") == [OTHER_HOST]


async def test_run_remediation_records_no_failed_stage() -> None:
    failure = ModelRetry("StageGeneration: Process exited with status 4")
    assert await _stage(failure) == []


def sim(seconds: float) -> float:
    return seconds * SCALE


class FakeHost(_CannedMCP):
    """One NixOS host whose rollback timer reboots into the boot default."""

    def __init__(
        self,
        *,
        good: set[int],
        drift: bool = False,
        fail: str | None = None,
        stage_s: float = 0,
    ) -> None:
        super().__init__(_HOST_TOOLS, {})
        self.good, self.drift, self.fail = good, drift, fail
        self.stage_s = stage_s
        self.in_flight = 0
        self.profile = self.boot_default = self.running = self.booted = COMMITTED
        self.armed: asyncio.TimerHandle | None = None
        self.down = False
        self.reboots = 0
        self.t0: float | None = None
        self.log: list[tuple[float, str, dict[str, object]]] = []
        self.polled = asyncio.Event()

    async def next_poll(self) -> None:
        self.polled.clear()
        await asyncio.wait_for(self.polled.wait(), sim(WINDOW_S))

    def now(self) -> float:
        loop = asyncio.get_running_loop()
        self.t0 = self.t0 if self.t0 is not None else loop.time()
        return round((loop.time() - self.t0) / SCALE)

    def _reboot(self) -> None:
        self.armed = None
        self.reboots += 1
        self.down = True
        self.running = self.booted = self.boot_default
        self.drift = False
        asyncio.get_running_loop().call_later(
            sim(BOOT_S), lambda: setattr(self, "down", False)
        )

    async def direct_call_tool(
        self, name: str, args: dict[str, object], metadata: object = None
    ) -> object:
        self.log.append((self.now(), name, args))
        if self.down:
            raise ModelRetry("ssh dial: connection refused")
        if name == STAGE_TOOL_NAME:
            self.in_flight += 1
            try:
                await asyncio.sleep(sim(self.stage_s))
            finally:
                self.in_flight -= 1
            if self.armed is None:
                self.armed = asyncio.get_running_loop().call_later(
                    sim(TIMER_MIN_S), self._reboot
                )
            if self.fail == "nix_env":
                raise ModelRetry("StageGeneration: Process exited with status 1")
            self.profile = self.running = args["generation"]
            if self.fail == "exit_4":
                raise ModelRetry("StageGeneration: Process exited with status 4")
            if self.fail == "transport":
                raise RuntimeError("ssh: connection reset")
            return "stage ok"
        if name == COMMIT_TOOL_NAME:
            if self.in_flight > 0:
                raise AssertionError("commit during stage")
            if self.fail == "commit":
                raise ModelRetry("CommitGeneration: Process exited with status 1")
            if self.armed is None:
                raise ModelRetry("CommitGeneration: Process exited with status 3")
            self.profile = self.boot_default = self.running
            self.armed.cancel()
            self.armed = None
            return "commit ok"
        if name == "get_systemd_status":
            self.polled.set()
            healthy = self.running in self.good and not self.drift
            return f"Active: {'active (running)' if healthy else 'failed'}"
        if name == "get_generations":
            return f"{self.profile} (current)"
        raise AssertionError(name)

    def tools(self) -> list[str]:
        return [name for _, name, _ in self.log]

    def calls(self, name: str) -> list[dict[str, object]]:
        return [args for _, called, args in self.log if called == name]

    def times(self, name: str) -> list[float]:
        return [at for at, called, _ in self.log if called == name]

    def polls_after_stage(self) -> int:
        tools = self.tools()
        last_stage = len(tools) - 1 - tools[::-1].index(STAGE_TOOL_NAME)
        return tools.index(COMMIT_TOOL_NAME) - last_stage - 1


def staging_model(
    stage_at: float,
    *stages: tuple[str, int],
    success: bool = True,
    between: Callable[[], Awaitable[None]] | None = None,
    answer_after: float = 0,
) -> FunctionModel:
    pending = [{"host": host, "generation": generation} for host, generation in stages]

    async def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if pending:
            if len(pending) == len(stages):
                await asyncio.sleep(sim(stage_at))
            elif between is not None:
                await between()
            args = pending.pop(0)
            return ModelResponse(parts=[ToolCallPart(STAGE_TOOL_NAME, args)])
        await asyncio.sleep(sim(answer_after))
        output = {
            "success": success,
            "actions_taken": [STAGE_TOOL_NAME] * len(stages),
            "tool_calls_count": len(stages),
            "mutation_attempted": True,
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])

    return FunctionModel(model_fn)


@pytest.fixture
def drive(tmp_path, monkeypatch, mock_kubectl_mcp):
    monkeypatch.setenv("EVAL_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(wd_mod, "WINDOW_S", sim(WINDOW_S))
    monkeypatch.setattr(wd_mod, "POLL_INTERVAL_S", sim(POLL_S))
    monkeypatch.setattr(orch_mod, "ROLLBACK_VERIFY_WINDOW_S", sim(TIMER_MIN_S))
    for module in (orch_mod, wd_mod):
        monkeypatch.setattr(
            module,
            "capture_health_snapshot",
            AsyncMock(return_value=_canned_baseline()),
        )

    async def _drive(
        host: FakeHost,
        event: FaultEvent,
        model: FunctionModel,
        *,
        target_host: str | None = HOST,
        source_branch: str = BASELINE_BRANCH,
    ) -> RunRecord:
        report = _canned_report_with_action("nixos_rebuild", target_host=target_host)
        usage = RunUsage(input_tokens=1, output_tokens=1)
        monkeypatch.setattr(
            orch_mod, "run_diagnosis", AsyncMock(return_value=(report, usage, []))
        )
        monkeypatch.setattr(orch_mod, "build_model", lambda *_: model)
        context = DiagnosisContext(
            source_branch=source_branch,
            manifest_path=None,
            live_yaml="",
            declared_yaml="",
            diff="",
        )
        monkeypatch.setattr(
            orch_mod, "build_diagnosis_context", AsyncMock(return_value=context)
        )
        host.now()
        return await run_orchestration(
            event,
            kubectl_mcp=mock_kubectl_mcp,
            flux_mcp=_CannedMCP(_REM_FLUX_TOOLS, {}),
            nixos_mcp=host,
            git_mcp=_CannedMCP(_REM_GIT_TOOLS, {}),
        )

    return _drive


async def test_confirmed_stage_commits_the_running_system_before_the_timer(
    drive, systemd_event
):
    host = FakeHost(good={STAGED})
    record = await drive(host, systemd_event, staging_model(10, (HOST, STAGED)))
    assert record.outcome == "success"
    assert host.calls(COMMIT_TOOL_NAME) == [{"host": HOST}]
    assert host.profile == host.boot_default == host.running == STAGED
    assert host.armed is None
    assert host.reboots == 0
    assert host.polls_after_stage() >= wd_mod.HEALTHY_STREAK_K


async def test_reboot_healed_fault_keeps_the_prior_generation_durable(
    drive, systemd_event
):
    host = FakeHost(good={COMMITTED}, drift=True)
    record = await drive(host, systemd_event, staging_model(10, (HOST, STAGED)))
    assert host.reboots == 1
    assert host.boot_default == COMMITTED
    assert record.outcome == "flux_degraded"


@pytest.mark.parametrize("fail", ["exit_4", "nix_env"])
@pytest.mark.parametrize("claim", [True, False])
async def test_failed_stage_never_reaches_commit(drive, systemd_event, fail, claim):
    host = FakeHost(good={COMMITTED, STAGED}, fail=fail)
    model = staging_model(10, (HOST, STAGED), success=claim)
    record = await drive(host, systemd_event, model)
    assert COMMIT_TOOL_NAME not in host.tools()
    assert record.outcome == COMMIT_FAILED


async def test_preflight_refusal_never_reaches_commit(drive, systemd_event):
    host = FakeHost(good={COMMITTED})
    model = staging_model(10, (HOST, STAGED))
    record = await drive(host, systemd_event, model, source_branch="main")
    assert STAGE_TOOL_NAME not in host.tools()
    assert COMMIT_TOOL_NAME not in host.tools()
    assert record.outcome == COMMIT_FAILED


async def test_stage_transport_error_never_reaches_commit(drive, systemd_event):
    host = FakeHost(good={COMMITTED, STAGED}, fail="transport")
    record = await drive(host, systemd_event, staging_model(10, (HOST, STAGED)))
    assert COMMIT_TOOL_NAME not in host.tools()
    assert record.outcome == "abort"


async def test_verdict_reached_before_the_stage_never_commits(drive, systemd_event):
    host = FakeHost(good={COMMITTED})
    record = await drive(host, systemd_event, staging_model(60, (HOST, STAGED)))
    assert COMMIT_TOOL_NAME not in host.tools()
    assert record.outcome == "flux_degraded"


async def test_second_stage_restarts_the_streak(drive, systemd_event):
    host = FakeHost(good={STAGED})
    model = staging_model(10, (HOST, STAGED), (HOST, STAGED), between=host.next_poll)
    record = await drive(host, systemd_event, model)
    assert record.outcome == "success"
    assert host.polls_after_stage() >= wd_mod.HEALTHY_STREAK_K


@pytest.mark.parametrize(
    ("target_host", "stages"),
    [
        pytest.param(HOST, [(OTHER_HOST, STAGED)], id="other_host"),
        pytest.param(HOST, [(HOST, STAGED), (OTHER_HOST, STAGED)], id="mixed_hosts"),
        pytest.param(None, [(HOST, STAGED)], id="no_probed_host"),
    ],
)
async def test_commit_requires_every_stage_on_the_probed_host(
    drive, systemd_event, target_host, stages
):
    host = FakeHost(good={STAGED})
    model = staging_model(10, *stages, between=host.next_poll)
    record = await drive(host, systemd_event, model, target_host=target_host)
    assert COMMIT_TOOL_NAME not in host.tools()
    assert record.outcome == COMMIT_FAILED


async def test_stage_after_the_first_watch_window_commits_once_confirmed(
    drive, systemd_event
):
    host = FakeHost(good={STAGED})
    record = await drive(
        host, systemd_event, staging_model(WINDOW_S + 20, (HOST, STAGED))
    )
    assert record.outcome == "success"
    assert host.armed is None


async def test_nixos_rebuild_without_a_resolvable_check_never_commits(
    drive, sample_fault_event
):
    host = FakeHost(good={COMMITTED, STAGED})
    record = await drive(host, sample_fault_event, staging_model(10, (HOST, STAGED)))
    assert record.outcome == "flux_degraded"
    assert COMMIT_TOOL_NAME not in host.tools()
    assert record.retry_queued is False


async def test_commit_tool_error_reports_commit_generation_failed(drive, systemd_event):
    host = FakeHost(good={STAGED}, fail="commit")
    record = await drive(host, systemd_event, staging_model(10, (HOST, STAGED)))
    assert record.outcome == COMMIT_FAILED
    assert record.success_rate is False


async def test_slow_answer_after_a_confirmed_stage_still_commits(drive, systemd_event):
    host = FakeHost(good={STAGED})
    model = staging_model(10, (HOST, STAGED), answer_after=TIMER_MIN_S + 60)
    record = await drive(host, systemd_event, model)
    assert record.outcome == "success"
    assert host.reboots == 0
    assert host.times(COMMIT_TOOL_NAME)[0] < 10 + TIMER_MIN_S


async def test_stage_after_an_early_healthy_verdict_is_committed(drive, systemd_event):
    host = FakeHost(good={COMMITTED, STAGED})
    record = await drive(host, systemd_event, staging_model(60, (HOST, STAGED)))
    assert record.outcome == "success"
    assert host.times(COMMIT_TOOL_NAME)[0] > host.times(STAGE_TOOL_NAME)[0]
    assert host.polls_after_stage() >= wd_mod.HEALTHY_STREAK_K


async def test_no_commit_while_a_stage_call_is_in_flight(drive, systemd_event):
    slow_stage_s = 20
    host = FakeHost(good={STAGED})

    async def slow_second_stage() -> None:
        await host.next_poll()
        host.stage_s = slow_stage_s

    model = staging_model(10, (HOST, STAGED), (HOST, STAGED), between=slow_second_stage)
    record = await drive(host, systemd_event, model)
    assert record.outcome == "success"
    assert len(host.calls(COMMIT_TOOL_NAME)) == 1
    second_stage_returned = host.times(STAGE_TOOL_NAME)[1] + slow_stage_s
    assert host.times(COMMIT_TOOL_NAME)[0] >= second_stage_returned


async def test_abort_after_a_stage_still_commits_once_confirmed(
    drive, systemd_event, monkeypatch
):
    remediation_timeout_s = 60
    monkeypatch.setattr(orch_mod, "REMEDIATION_TIMEOUT_S", sim(remediation_timeout_s))
    host = FakeHost(good={STAGED})
    model = staging_model(50, (HOST, STAGED), answer_after=600)
    record = await drive(host, systemd_event, model)
    assert record.outcome == "abort"
    assert host.times(COMMIT_TOOL_NAME)[0] > remediation_timeout_s
    assert host.profile == host.boot_default == STAGED
    assert host.reboots == 0


async def test_cancelled_run_leaves_the_stage_to_the_timer(drive, systemd_event):
    host = FakeHost(good={STAGED})
    run = asyncio.create_task(
        drive(host, systemd_event, staging_model(10, (HOST, STAGED)))
    )
    while STAGE_TOOL_NAME not in host.tools():
        await host.next_poll()
    run.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await run
    assert COMMIT_TOOL_NAME not in host.tools()
    await asyncio.sleep(sim(TIMER_MIN_S + 10))
    assert host.booted == host.boot_default == COMMITTED


async def test_stage_cut_by_the_remediation_timeout_is_never_committed(
    drive, systemd_event, monkeypatch
):
    monkeypatch.setattr(orch_mod, "REMEDIATION_TIMEOUT_S", sim(40))
    host = FakeHost(good={STAGED})

    async def slow_second_stage() -> None:
        host.stage_s = 100

    model = staging_model(10, (HOST, STAGED), (HOST, STAGED), between=slow_second_stage)
    record = await drive(host, systemd_event, model)
    assert record.outcome == "abort"
    assert COMMIT_TOOL_NAME not in host.tools()


async def test_failing_watch_cancels_remediation(drive, systemd_event, monkeypatch):
    remediation_timeout_s = 600
    monkeypatch.setattr(orch_mod, "REMEDIATION_TIMEOUT_S", sim(remediation_timeout_s))
    monkeypatch.setattr(
        wd_mod, "capture_health_snapshot", AsyncMock(side_effect=TimeoutError())
    )
    host = FakeHost(good={STAGED})
    model = staging_model(0, answer_after=remediation_timeout_s * 2)
    record = await drive(host, systemd_event, model, target_host=None)
    assert record.outcome == "abort"
    assert record.setup_error != "remediation_timeout"
    assert host.now() < remediation_timeout_s
