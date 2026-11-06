import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "infra/nixos/modules/services/auto-reconcile.sh"
MODULE = REPO / "infra/nixos/modules/services/auto-reconciler.nix"
ALLOWLIST_GO = REPO / "mcp-servers/nixos-mcp/internal/nixos/allowlist.go"
FLAKEATTR_GO = REPO / "mcp-servers/nixos-mcp/internal/nixos/flakeattr.go"
BRANCH = "eval/lab"
ATTR = "vigil-worker-1-lab-aarch64"
APPLIED = "a" * 40
NEW = "b" * 40
EXECUTABLE = 0o755
TIMER_INACTIVE_EXIT = 3
TIMER_UNREADABLE_EXIT = 1
TIMER_STATES = {
    "active": ("active", 0),
    "inactive": ("inactive", TIMER_INACTIVE_EXIT),
    "error": (
        "Failed to connect to bus: No such file or directory",
        TIMER_UNREADABLE_EXIT,
    ),
}
TIMER_QUERY = "systemctl is-active rollback-gate.timer"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text(f"#!/bin/sh\n{body}\n")
    stub.chmod(EXECUTABLE)


def _run(
    tmp_path: Path, *, timer: str = "inactive", origin: str, switch_exit: int = 0
) -> subprocess.CompletedProcess[str]:
    etc, state, bin_dir = tmp_path / "etc", tmp_path / "state", tmp_path / "bin"
    for d in (etc, state, bin_dir):
        d.mkdir()
    (etc / "branch").write_text(f"{BRANCH}\n")
    (etc / "flake-attr").write_text(f"{ATTR}\n")
    (state / "last-sha").write_text(f"{APPLIED}\n")
    log = tmp_path / "calls.log"
    output, code = TIMER_STATES[timer]
    _stub(
        bin_dir,
        "git",
        f'echo "git $*" >> "{log}"\ncase "$*" in *rev-parse*) echo "{origin}";; esac',
    )
    _stub(bin_dir, "flock", f'echo "flock $*" >> "{log}"')
    _stub(
        bin_dir,
        "nixos-rebuild",
        f'line="nixos-rebuild $*"\n'
        f'if {{ : >&9; }} 2>/dev/null; then line="$line fd9=open"; fi\n'
        f'echo "$line" >> "{log}"\nexit {switch_exit}',
    )
    _stub(
        bin_dir,
        "systemctl",
        f'echo "systemctl $*" >> "{log}"\necho "{output}"\nexit {code}',
    )
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "VIGIL_ETC_DIR": str(etc),
            "VIGIL_CONFIG_DIR": str(tmp_path / "config"),
            "VIGIL_STATE_FILE": str(state / "last-sha"),
            "VIGIL_REBUILD_LOCK": str(tmp_path / "rebuild.lock"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text().splitlines()


def _state(tmp_path: Path) -> str:
    return (tmp_path / "state" / "last-sha").read_text().strip()


@pytest.mark.parametrize("timer", ["active", "inactive", "error"])
def test_reconcile_proceeds_only_while_the_rollback_timer_is_inactive(
    tmp_path: Path, timer: str
) -> None:
    result = _run(tmp_path, timer=timer, origin=NEW)
    calls = _calls(tmp_path)
    config = tmp_path / "config"
    if timer == "inactive":
        assert result.returncode == 0
        assert f"git -C {config} reset --hard origin/{BRANCH}" in calls
        assert any(c.startswith("nixos-rebuild switch") for c in calls)
        assert _state(tmp_path) == NEW
        return
    assert calls == [f"git -C {config} fetch origin", "flock 9", TIMER_QUERY]
    assert _state(tmp_path) == APPLIED
    if timer == "active":
        assert result.returncode == 0
        assert (
            "rollback-gate.timer is active; round skipped until the staged "
            "generation is committed or reverted"
        ) in result.stdout
    else:
        assert result.returncode == TIMER_UNREADABLE_EXIT
        assert (
            "rollback-gate.timer state unreadable "
            f"({TIMER_STATES['error'][0]}, exit {TIMER_UNREADABLE_EXIT}); round skipped"
        ) in result.stdout


@pytest.mark.parametrize("origin", [NEW, APPLIED], ids=["new", "applied"])
def test_reconcile_applies_the_tracked_revision_under_the_rebuild_lock(
    tmp_path: Path, origin: str
) -> None:
    result = _run(tmp_path, origin=origin)
    config = tmp_path / "config"
    assert result.returncode == 0, result.stderr
    expected = [
        f"git -C {config} fetch origin",
        "flock 9",
        TIMER_QUERY,
        f"git -C {config} rev-parse origin/{BRANCH}",
        f"git -C {config} reset --hard origin/{BRANCH}",
    ]
    if origin == NEW:
        expected.append(f"nixos-rebuild switch --flake {config}#{ATTR} fd9=open")
    assert _calls(tmp_path) == expected
    assert _state(tmp_path) == origin
    assert (tmp_path / "rebuild.lock").exists()


def test_reconcile_keeps_the_applied_revision_when_the_switch_fails(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, origin=NEW, switch_exit=1)
    assert result.returncode != 0
    assert _state(tmp_path) == APPLIED


def _go_const(path: Path, name: str) -> str:
    match = re.search(rf'{name}\s*=\s*"([^"]+)"', path.read_text())
    assert match, name
    return match.group(1)


def _module_env(name: str) -> str:
    match = re.search(rf'{name}\s*=\s*"([^"]+)"', MODULE.read_text())
    assert match, name
    return match.group(1)


def test_reconciler_and_nixos_mcp_share_host_paths() -> None:
    assert _module_env("VIGIL_REBUILD_LOCK") == _go_const(
        ALLOWLIST_GO, "rebuildLockPath"
    )
    assert _module_env("VIGIL_STATE_FILE") == _go_const(
        FLAKEATTR_GO, "reconciledRevisionPath"
    )
