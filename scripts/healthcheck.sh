#!/usr/bin/env sh
set -eu
# A registered runner must have its listener process; an initial unregistered
# bootstrap remains unhealthy until runnerctl supplies the one-time token.
test -f /home/runner/.runner || exit 1
pgrep -f '/home/runner/bin/Runner.Listener|Runner.Listener run' >/dev/null 2>&1
