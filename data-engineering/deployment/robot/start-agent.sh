#!/usr/bin/env bash
source "$(dirname -- "${BASH_SOURCE[0]}")/prepare-runtime.sh"
args=(--robot-id "${SWARMLINK_ROBOT_ID:?Missing robot ID}"
      --run-id "${SWARMLINK_RUN_ID:-hotspot-live}"
      --battery-scale "${SWARMLINK_BATTERY_SCALE:-fraction}"
      --hz "${SWARMLINK_SAMPLE_HZ:-2}" --max-rays "${SWARMLINK_MAX_RAYS:-360}"
      --outbox "$SWARMLINK_ROOT/.runtime/outbox.sqlite")
if [[ -n "${SWARMLINK_COLLECTOR_URL:-}" ]]; then
    args+=(--collector "$SWARMLINK_COLLECTOR_URL")
fi
exec python3 agents/ros2_agent.py "${args[@]}"
