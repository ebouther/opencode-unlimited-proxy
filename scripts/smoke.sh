#!/usr/bin/env bash
# Smoke test for the opencode-unlimited-proxy stack.
#
#   ./scripts/smoke.sh [base_url] [api_key] [model]
#
# Defaults match stack/config.json (local dev key, host-network engine on 8080).
# Exits non-zero on the first failure, so it can be used in CI or after an upgrade.

set -uo pipefail

BASE_URL="${1:-http://127.0.0.1:8080}"
API_KEY="${2:-local-test-key}"
MODEL="${3:-deepseek-v4.1-flash-free}"
WEBUI="${WEBUI:-http://127.0.0.1:8081}"

fail=0
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
check() { if [ "$1" -eq 0 ]; then echo "PASS: $2"; else echo "FAIL: $2"; fail=1; fi; }

# Record a test result in `rc` rather than relying on the status of the `[ ... ]`
# line that produced it. A bare `check $?` after a condition reads fine but is
# only correct while nothing sits between the two; one added line and the check
# silently grades the wrong thing.

step "1/6  engine reachable on $BASE_URL"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE_URL/health" 2>/dev/null)
rc=1
if [ "$code" = "200" ] || [ "$code" = "404" ]; then rc=0; fi
check $rc "engine answers on /health or / (got $code)"

step "2/6  model catalogue"
models=$(curl -s -m 15 "$BASE_URL/v1/models" -H "Authorization: Bearer $API_KEY")
count=$(printf '%s' "$models" | python3 -c 'import json,sys
try:
    print(len(json.load(sys.stdin).get("data", [])))
except Exception:
    print(0)' 2>/dev/null)
rc=1
if [ "${count:-0}" -gt 0 ]; then rc=0; fi
check $rc "v1/models returned $count models"

step "3/6  egress pool"
QUOTA_EXHAUSTED=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE=$(python3 -c "
import json
cfg = json.load(open('${SCRIPT_DIR}/../stack/config.json'))
print('warp' if cfg.get('warp', {}).get('enabled') else 'external')
" 2>/dev/null)

if [ "$MODE" = "warp" ]; then
  # The engine owns registration and rotation. Ready means at least one
  # active exit; cooling and standby are normal states, not faults.
  health=$(curl -s -m 10 "$BASE_URL/healthz")
  status=$(printf '%s' "$health" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("warp",{}).get("status",""))' 2>/dev/null)
  active=$(printf '%s' "$health" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("warp",{}).get("active",0))' 2>/dev/null)
  echo "  warp.status=${status:-unknown} active=${active:-0}"
  cooling=$(printf '%s' "$health" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("warp",{}).get("cooling",0))' 2>/dev/null)
  if [ "$status" = "ready" ] && [ "${active:-0}" -ge 1 ]; then
    check 0 "engine-managed WARP pool has an active exit"
  elif [ "${cooling:-0}" -gt 0 ]; then
    QUOTA_EXHAUSTED=1
    echo "SKIP: every WARP exit is cooling down (upstream daily quota). Retry after 00:00 UTC."
  else
    check 1 "engine-managed WARP pool has an active exit (status=${status:-unknown})"
  fi
else
# Proxy ports are read from the engine config so this stays correct after a
# resize, paired with their node id so a drained node can be told apart from a
# broken one.
mapfile -t cfg_rows < <(python3 -c "
import json
cfg = json.load(open('${SCRIPT_DIR}/../stack/config.json'))
proxies = cfg.get('proxies', [])
for n, proxy in enumerate(proxies):
    print(chr(ord('a') + n), proxy.rsplit(':', 1)[1])
" 2>/dev/null)

# Every configured proxy must show up. A short read used to grade a six-node
# pool as 1/1 and report success, which is worse than failing outright.
expected=$(grep -o '"http://127.0.0.1:[0-9]*"' "${SCRIPT_DIR}/../stack/config.json" | wc -l)
ports=""
if [ "${#cfg_rows[@]}" -eq 0 ] || [ "${#cfg_rows[@]}" -ne "$expected" ]; then
  echo "FAIL: read ${#cfg_rows[@]} of $expected proxy entries from stack/config.json"
  fail=1
else
  for row in "${cfg_rows[@]}"; do ports="$ports ${row#* }"; done
fi

# A drained node's proxy is *supposed* to stop listening: ofu-dedupe drains the
# surplus when this colo's address pool cannot give everyone a distinct one.
# Grading it as unreachable would turn a working self-healing pool into a red
# smoke test, so drained and broken are counted separately.
is_drained() {
  command -v docker >/dev/null 2>&1 || return 1
  docker exec ofu-dedupe test -f "/markers/$1" >/dev/null 2>&1
}

if [ -z "$ports" ]; then
  echo "SKIP: could not read stack/config.json proxies"
else
  ok=0; total=0; drained=0
  for row in "${cfg_rows[@]}"; do
    id=${row%% *}; port=${row##* }
    total=$((total + 1))
    ip=$(curl -s -m 20 -x "http://127.0.0.1:$port" https://api.ipify.org 2>/dev/null)
    if printf '%s' "$ip" | grep -qE '^[0-9a-fA-F:.]+$'; then
      ok=$((ok + 1)); echo "  $id :$port -> $ip"
    elif is_drained "$id"; then
      drained=$((drained + 1)); echo "  $id :$port -> drained by ofu-dedupe (expected)"
    else
      echo "  $id :$port -> UNREACHABLE and not drained"
    fi
  done
  serving=$((total - drained))
  echo "  $ok/$serving serving ($drained drained, $total configured)"
  rc=1
  if [ "$ok" -eq "$serving" ]; then rc=0; fi
  check $rc "every serving node answers ($ok/$serving)"

  # Distinctness is the whole point of the pool: quota is bucketed per source
  # address, so two nodes sharing an address are one bucket, not two. This is
  # the check that would have caught the "5 of 6 nodes on one address" bug.
  distinct=$(for port in $ports; do
               curl -s -m 20 -x "http://127.0.0.1:$port" https://api.ipify.org 2>/dev/null
               echo
             done | grep -E '^[0-9a-fA-F:.]+$' | sort -u | wc -l)
  echo "  $distinct distinct egress addresses across $ok serving nodes"
  rc=1
  # Distinctness is a statement about the nodes that are actually serving, not
  # about how many were configured. A drained node holds no address by design:
  # ofu-dedupe drained it precisely because the colo's pool could not give it a
  # distinct one. Grading the pool against `total` therefore fails every install
  # where the arbiter did its job -- a correctly drained six-node pool with four
  # distinct addresses could never satisfy a threshold of `total - 1`.
  if [ "$ok" -gt 0 ] && [ "$distinct" -ge "$ok" ]; then rc=0; fi
  check $rc "pool is de-duplicated ($distinct/$ok serving distinct, $drained drained, $total configured)"
fi
fi

# A single free-tier completion. Retries once: when the engine is shared with
# other clients, a single request can lose the race and hit a 60s timeout.
# Two consecutive failures is the real signal.
ask_chat() { # $1 = extra json fragment (e.g. '"stream":false,')
  local extra="$1"
  for attempt in 1 2; do
    body=$(curl -s -m 150 -X POST "$BASE_URL/v1/chat/completions" \
      -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
      -d "{\"model\":\"$MODEL\",${extra}\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OK\"}],\"max_tokens\":200}")
    if printf '%s' "$body" | grep -q 'FreeUsageLimitError\|Free usage limit reached'; then
      return 2
    fi
    if printf '%s' "$body" | python3 -c 'import json,sys
d = json.load(sys.stdin)
if d.get("error"):
    sys.exit(1)
msg = d["choices"][0]["message"]
text = (msg.get("content") or "") + (msg.get("reasoning_content") or "")
sys.exit(0 if "OK" in text else 1)' 2>/dev/null; then
      return 0
    fi
    echo "  (attempt $attempt failed, retrying)"
  done
  return 1
}

step "4/6  free-tier chat completion ($MODEL)"
if [ "$QUOTA_EXHAUSTED" = "1" ]; then
  echo "SKIP: no exit with headroom; the completion checks need a successful baseline."
else
  ask_chat ""
  rc=$?
  if [ "$rc" -eq 0 ]; then check 0 "chat completion returned content"
  elif [ "$rc" -eq 2 ]; then echo "SKIP: upstream daily quota exhausted (FreeUsageLimitError). Retry after 00:00 UTC."
  else check 1 "chat completion returned content"
  fi
fi

# Gates 3 and 4 of the free-tier fingerprint: no tools at all, and stream:false.
# A gateway that only mints valid session headers returns 403 for this exact
# shape. See docs/free-tier-gates.md -- this is the cheapest canary we have.
step "5/6  fingerprint gate: zero tools + stream:false"
if [ "$QUOTA_EXHAUSTED" = "1" ]; then
  echo "SKIP: no exit with headroom; the gate canary needs a successful baseline."
else
  ask_chat '"stream":false,'
  rc=$?
  if [ "$rc" -eq 0 ]; then check 0 "zero-tool non-stream request accepted (gates 3+4 intact)"
  elif [ "$rc" -eq 2 ]; then echo "SKIP: upstream daily quota exhausted (FreeUsageLimitError). Retry after 00:00 UTC."
  else check 1 "zero-tool non-stream request accepted (gates 3+4 intact)"
  fi
fi

step "6/6  webui reachable on $WEBUI"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$WEBUI/" 2>/dev/null)
rc=1
if [ "$code" = "200" ]; then rc=0; fi
check $rc "webui answers 200 (got $code)"

printf '\n'
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED (see troubleshooting in README.md)"
fi
exit "$fail"
