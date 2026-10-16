#!/usr/bin/env bash
# This script runs as the unprivileged runner user.
set -Eeuo pipefail
umask 077
cd /home/runner

: "${RUNNER_TARGET_URL:?RUNNER_TARGET_URL must be set}"
: "${RUNNER_NAME:?RUNNER_NAME must be set}"
: "${RUNNER_IMAGE_VERSION:?RUNNER_IMAGE_VERSION must be set}"
: "${RUNNER_WORK_DIR:?RUNNER_WORK_DIR must be set}"

# Persist binary auto-updates, .runner credentials and the registration across
# container recreation. A freshly built image can refresh the base distribution.
if [[ ! -f .image-version ]] || [[ "$(cat .image-version)" != "$RUNNER_IMAGE_VERSION" ]]; then
    echo "Preparing GitHub Runner distribution $RUNNER_IMAGE_VERSION"
    cp -R --no-preserve=ownership /opt/runner-dist/. /home/runner/
    printf '%s\n' "$RUNNER_IMAGE_VERSION" > .image-version
fi

profile="${RUNNER_TARGET_URL}|${RUNNER_NAME}|${RUNNER_LABELS:-}|${RUNNER_GROUP:-}|${RUNNER_WORK_DIR}"
if [[ -f .runner ]]; then
    if [[ ! -f .credentials ]]; then
        echo 'ERROR: runner is registered but .credentials is missing; repair state manually.' >&2
        exit 1
    fi
    if [[ ! -f .managed-profile ]] || [[ "$(cat .managed-profile)" != "$profile" ]]; then
        echo 'ERROR: existing runner state differs from configured target/name/labels/group.' >&2
        echo 'Retire old registration before changing runner identity. See README migration guide.' >&2
        exit 1
    fi
else
    fifo=/home/runner/.registration-token
    rm -f "$fifo"
    mkfifo -m 600 "$fifo"
    echo 'Awaiting one-time GitHub registration token from runnerctl.py up ...'
    token=''
    if ! IFS= read -r token < "$fifo"; then
        rm -f "$fifo"
        echo 'ERROR: registration token pipe was closed without a token.' >&2
        exit 1
    fi
    rm -f "$fifo"
    if [[ -z "$token" ]]; then
        echo 'ERROR: received an empty registration token.' >&2
        exit 1
    fi
    options=(--url "$RUNNER_TARGET_URL" --token "$token" --name "$RUNNER_NAME"
             --work "$RUNNER_WORK_DIR" --unattended)
    if [[ -n "${RUNNER_LABELS:-}" ]]; then
        options+=(--labels "$RUNNER_LABELS")
    fi
    if [[ -n "${RUNNER_GROUP:-}" ]]; then
        options+=(--runnergroup "$RUNNER_GROUP")
    fi
    # Record intended identity before config.sh: if registration succeeds but the
    # container dies immediately, the next startup can safely adopt that state.
    printf '%s\n' "$profile" > .managed-profile
    # Short-lived token is used only for registration; it is not exported to jobs.
    if ! ./config.sh "${options[@]}"; then
        echo 'ERROR: runner registration failed (check permissions, labels and target).' >&2
        exit 1
    fi
    unset token
fi

# Keep GitHub's default runner auto-updates enabled. Do not forward a PAT.
echo 'Starting persistent self-hosted GitHub runner.'
exec ./run.sh
