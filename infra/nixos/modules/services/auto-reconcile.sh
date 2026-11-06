#!/usr/bin/env bash
set -euo pipefail

TIMER_UNIT="rollback-gate.timer"
TIMER_INACTIVE_EXIT=3
TIMER_UNREADABLE_EXIT=1

BRANCH=$(cat "$VIGIL_ETC_DIR/branch")
ATTR=$(cat "$VIGIL_ETC_DIR/flake-attr")
mkdir -p "$(dirname "$VIGIL_STATE_FILE")"
git -C "$VIGIL_CONFIG_DIR" fetch origin

exec 9>"$VIGIL_REBUILD_LOCK"
flock 9

timer_code=0
timer_state=$(systemctl is-active "$TIMER_UNIT") || timer_code=$?
if [ "$timer_code" -eq 0 ]; then
  echo "$TIMER_UNIT is active; round skipped until the staged generation is committed or reverted"
  exit 0
fi
if [ "$timer_state" != "inactive" ] || [ "$timer_code" -ne "$TIMER_INACTIVE_EXIT" ]; then
  echo "$TIMER_UNIT state unreadable ($timer_state, exit $timer_code); round skipped"
  exit "$TIMER_UNREADABLE_EXIT"
fi

NEW=$(git -C "$VIGIL_CONFIG_DIR" rev-parse "origin/$BRANCH")
git -C "$VIGIL_CONFIG_DIR" reset --hard "origin/$BRANCH"
OLD=$(cat "$VIGIL_STATE_FILE" 2>/dev/null || echo "")
if [ "$NEW" != "$OLD" ]; then
  nixos-rebuild switch --flake "$VIGIL_CONFIG_DIR#$ATTR"
  echo "$NEW" > "$VIGIL_STATE_FILE"
fi
