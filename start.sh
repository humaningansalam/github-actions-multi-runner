#!/bin/bash

GITHUB_URL=$GITHUB_URL
GITHUB_TOKEN=$GITHUB_TOKEN
RUNNER_NAME=${RUNNER_NAME:-$(hostname)}
RUNNER_WORK_DIR=${RUNNER_WORK_DIR:-_work}
RUNNER_LABELS=${RUNNER_LABELS:-default}

echo "Changing ownership of /home/runner/${RUNNER_WORK_DIR} to runner..."
sudo chown -R runner:runner "/home/runner/${RUNNER_WORK_DIR}"
echo "Ownership change complete."

# If the runner has already been configured, skip the config step
if [ ! -f ".runner" ]; then
    # Configure the runner
    ./config.sh \
        --url "${GITHUB_URL}" \
        --token "${GITHUB_TOKEN}" \
        --name "${RUNNER_NAME}" \
        --work "${RUNNER_WORK_DIR}" \
        --labels "${RUNNER_LABELS}" \
        --unattended \
        --replace
fi

# Cleanup any existing _diag directory
rm -rf "_diag" || true

# Start the runner
exec ./run.sh