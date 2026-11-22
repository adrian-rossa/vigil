"""Mode B open-loop stepped HTTP traffic generator for operational load runs"""

from __future__ import annotations

import asyncio
import logging
import math
import socket
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)


def target_rps(
    elapsed_s: float,
    profile: Sequence[int],
    phase_s: float = 60.0,
    ramp_s: float = 10.0,
) -> float:
    """Return target requests/sec at elapsed_s, looping through the stepped profile"""
    if not profile:
        return 0.0
    if len(profile) == 1 or phase_s <= 0:
        return float(profile[0])
    cycle_s = len(profile) * phase_s + (len(profile) - 1) * max(0.0, ramp_s) 
    cycle_pos_s = elapsed_s % cycle_s
    for index, rate in enumerate(profile):
        if cycle_pos_s < phase_s:
            return float(rate)
        cycle_pos_s -= phase_s
        if index + 1 < len(profile) and ramp_s > 0:
            if cycle_pos_s < ramp_s:
                next_rate = profile[index + 1]
                return rate + (next_rate - rate) * (cycle_pos_s / ramp_s)
            cycle_pos_s -= ramp_s
    return float(profile[-1])


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile (pct in 0..100), or None when empty"""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) -1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return round(ordered[index], 2)


@dataclass
class TrafficStats:
    total: int = 0
    ok: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def to_summary(self, duration_s: float, profile: Sequence[int]) -> dict[str, Any]:
        error_rate = round(self.errors / self.total, 4) if self.total else 0.0
        return {
            "profile": list(profile),
            "duration_s": round(duration_s, 3),
            "total_requests": self.total,
            "ok_2xx": self.ok,
            "errors": self.errors,
            "error_rate": error_rate,
            "p50_ms": percentile(self.latencies_ms, 50),
            "p95_ms": percentile(self.latencies_ms, 95),
            "p99_ms": percentile(self.latencies_ms, 99),
        }


def _get_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(
    port: int, proc: subprocess.Popen[str], timeout_s: float = 15.0
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read().strip() if proc.stderr else ""
            raise RuntimeError(f"kubectl port-forward exited early: {err}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"kubectl port-forward did not open port {port} in {timeout_s}s")


class SteppedTrafficRunner:
    """Background open-loop HTTP driver targeting vigil-app via port-forward"""

    def __init__(
        self,
        profile: Sequence[int] = (50, 200, 50),
        ramp_s: float = 10.0,
        phase_s: float = 60.0,
        *,
        target_url: str | None = None,
        service: str = "svc/vigil-app",
        namespace: str = "default",
        remote_port: int = 80,
        kubeconfig: str | None = None,
        max_in_flight: int = 200,
        request_timeout_s: float = 2.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.profile = list(profile)
        self.ramp_s = ramp_s
        self.phase_s = phase_s
        self.target_url = target_url
        self.service = service
        self.namespace = namespace
        self.remote_port = remote_port
        self.kubeconfig = kubeconfig
        self.max_in_flight = max_in_flight
        self.request_timeout_s = request_timeout_s
        self.transport = transport

        self.stats = TrafficStats()
        self._stop_event = asyncio.Event()
        self._proc: subprocess.Popen[str] | None = None
        self._task: asyncio.Task[None] | None = None
        self._in_flight: set[asyncio.Task[None]] = set()
        self._started_at = 0.0

    def _start_port_forward(self) -> str:
        local_port = _get_free_local_port()
        cmd = ["kubectl"]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        cmd += [
            "port-forward",
            "-n",
            self.namespace,
            self.service,
            f"{local_port}:{self.remote_port}",
        ]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        _wait_for_port(local_port, self._proc)
        return f"http://127.0.0.1:{local_port}/"

    async def start(self) -> None:
        url = self.target_url or self._start_port_forward()
        self._stop_event.clear()
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._run_loop(url))

    async def _send_one(
        self, client: httpx.AsyncClient, url: str, scheduled_t: float
    ) -> None:
        self.stats.total += 1
        try:
            resp = await client.get(url, headers={"Connection": "close"})
            elapsed_ms = (time.monotonic() - scheduled_t) * 1000.0
            self.stats.latencies_ms.append(elapsed_ms)
            if 200 <= resp.status_code < 300:
                self.stats.ok += 1
            else:
                self.stats.errors += 1
        except httpx.HTTPError:
            self.stats.errors += 1
    
    async def _run_loop(self, url: str) -> None:
        limits = httpx.Limits(
            max_connections = self.max_in_flight,
            max_keepalive_connections=0,
        )
        async with httpx.AsyncClient(
            timeout=self.request_timeout_s,
            limits = limits,
            transport = self.transport,
        ) as client:
            start = time.monotonic()
            next_due = start
            while not self._stop_event.is_set():
                now = time.monotonic()
                rps = target_rps(now - start, self.profile, self.phase_s, self.ramp_s)
                if rps <= 0:
                    await asyncio.sleep(0.1)
                    continue
                interval = 1.0 / rps
                if now < next_due:
                    await asyncio.sleep(min(next_due - now, 0.05))
                    continue
                scheduled_t = next_due
                next_due = max(next_due + interval, now - interval)
                if len(self._in_flight) >= self.max_in_flight:
                    self.stats.total += 1
                    self.stats.errors += 1
                    continue
                task = asyncio.create_task(self._send_one(client, url, scheduled_t))
                self._in_flight.add(task)
                task.add_done_callback(self._in_flight.discard)
            if self._in_flight:
                await asyncio.wait(self._in_flight, timeout=self.request_timeout_s)

    async def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(
                    self._task, timeout=self.request_timeout_s + 2.0
                )
            except TimeoutError:
                self._task.cancel()
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        duration_s = (
            max(0.0, time.monotonic() - self._started_at) if self._started_at else 0.0
        )
        return self.stats.to_summary(duration_s, self.profile)