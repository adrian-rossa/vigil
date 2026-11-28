from __future__ import annotations

import asyncio

import httpx
from eval.traffic import SteppedTrafficRunner, TrafficStats, percentile, target_rps


def test_target_rps_steps_ramps_and_wraps() -> None:
    profile = [50, 200, 50]
    assert target_rps(0.0, profile, phase_s=60.0, ramp_s=10.0) == 50.0
    assert target_rps(65.0, profile, phase_s=60.0, ramp_s=10.0) == 125.0
    assert target_rps(70.0, profile, phase_s=60.0, ramp_s=10.0) == 200.0
    assert target_rps(135.0, profile, phase_s=60.0, ramp_s=10.0) == 125.0
    assert target_rps(150.0, profile, phase_s=60.0, ramp_s=10.0) == 50.0
    assert target_rps(200.0, profile, phase_s=60.0, ramp_s=10.0) == 50.0
    assert target_rps(0.0, []) == 0.0


def test_percentile_and_summary_metrics() -> None:
    assert percentile([], 95) is None
    stats = TrafficStats(
        total=4, ok=3, errors=1, latencies_ms=[10.0, 20.0, 30.0, 40.0]
    )
    summary = stats.to_summary(duration_s=2.0, profile=[50, 200, 50])
    assert summary["error_rate"] == 0.25
    assert summary["p50_ms"] == 20.0
    assert summary["p95_ms"] == 40.0
    assert summary["p99_ms"] == 40.0


async def test_stepped_runner_counts_ok_and_error_responses() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = 502 if calls % 3 == 0 else 200
        return httpx.Response(status, text="ok" if status == 200 else "bad")

    runner = SteppedTrafficRunner(
        profile=[40],
        target_url="http://vigil-app.local/",
        transport=httpx.MockTransport(handler),
    )
    await runner.start()
    await asyncio.sleep(0.15)
    summary = await runner.stop()
    assert summary["total_requests"] >= 3
    assert summary["ok_2xx"] >= 2
    assert summary["errors"] >= 1
    assert summary["http_errors"] >= 1
    assert summary["transport_errors"] == 0
    assert summary["last_error"] == "HTTP 502"
    assert summary["p99_ms"] is not None


async def test_stepped_runner_tracks_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    runner = SteppedTrafficRunner(
        profile=[30],
        target_url="http://vigil-app.local/",
        transport=httpx.MockTransport(handler),
    )
    await runner.start()
    await asyncio.sleep(0.12)
    summary = await runner.stop()
    assert summary["errors"] >= 1
    assert summary["transport_errors"] >= 1
    assert summary["http_errors"] == 0
    assert summary["last_error"] is not None
    assert "ConnectError" in summary["last_error"]