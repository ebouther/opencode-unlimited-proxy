#!/bin/sh
# CONNECT proxy supervisor with a drain gate.
#
# The dedupe arbiter (stack/dedupe.py) writes /markers/<NODE_ID> when this node
# cannot be given an egress address distinct from its peers. Draining means
# withholding the proxy: the listening socket closes, so the engine's proxy
# health check fails immediately and drops the node from the pool instead of
# spending requests on a bucket a peer is already using.
#
# Draining is reversible. Remove the marker and the proxy is back within one
# poll interval:
#
#     docker exec ofu-dedupe rm /markers/b && docker logs -f ofu-proxy-b

MARKER_DIR=/markers
POLL=10

: "${NODE_ID:?NODE_ID must be set}"
marker="$MARKER_DIR/$NODE_ID"

child=""

stop_child() {
    [ -n "$child" ] || return 0
    kill "$child" 2>/dev/null
    wait "$child" 2>/dev/null
    child=""
}

start_child() {
    # -d keeps tinyproxy in the foreground so we can supervise it.
    tinyproxy -d &
    child=$!
}

on_signal() {
    trap - TERM INT
    stop_child
    exit 0
}
trap on_signal TERM INT

echo "gate: node=$NODE_ID marker=$marker"
start_child

while :; do
    sleep "$POLL"
    if [ -f "$marker" ]; then
        if [ -n "$child" ]; then
            echo "gate: drained -- stopping CONNECT proxy"
            stop_child
        fi
    elif [ -z "$child" ]; then
        echo "gate: marker cleared -- starting CONNECT proxy"
        start_child
    fi
done
