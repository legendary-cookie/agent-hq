#!/bin/bash
pidfile="$1"; shift
mkdir -p /tmp/hq-runs
exec setsid --wait bash -c 'echo $$ > "$0"; exec "$@"' "$pidfile" "$@"
