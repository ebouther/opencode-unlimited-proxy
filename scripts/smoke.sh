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
MODEL="${3:-space-bunny-free}"
WEBUI="${WEBUI:-http://127.0.0.1:8081}"

fail=0
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
check() { if [ "$1" -eq 0 ]; then echo "PASS: $2"; else echo "FAIL: $2"; fail=1; fi; }

step "1/6  engine reachable on $BASE_URL"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$BASE_URL/health" 2>/dev/null)
[ "$code" = "200" ] || [ "$code" = "404" ]
check $? "engine answers on /health or / (got $code)"

step "2/6  model catalogue"
models=$(curl -s -m 15 "$BASE_URL/v1/models" -H "Authorization: Bearer $API_KEY")
count=$(printf '%s' "$models" | python3 -c 'import json,sys
try:
    print(len(json.load(sys.stdin).get("data", [])))
except Exception:
    print(0)' 2>/dev/null)
[ "${count:-0}" -gt 0 ]
check $? "v1/models returned $count models"

step "3/6  proxy pool reachable (each proxy must forward to an ip echo)"
# Proxy ports are read from the engine config so this stays correct after a resize.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ports=$(python3 -c "
import json
cfg = json.load(open('${SCRIPT_DIR}/../stack/config.json'))
print(' '.join(p.rsplit(':', 1)[1] for p in cfg.get('proxies', [])))
" 2>/dev/null)
if [ -z "$ports" ]; then echo "SKIP: could not read stack/config.json proxies"; else
  ok=0; total=0
  for port in $ports; do
    total=$((total + 1))
    ip=$(curl -s -m 20 -x "http://127.0.0.1:$port" https://api.ipify.org 2>/dev/null)
    if printf '%s' "$ip" | grep -qE '^[0-9a-fA-F:.]+$'; then
      ok=$((ok + 1)); echo "  :$port -> $ip"
    else
      echo "  :$port -> unreachable"
    fi
  done
  echo "  $ok/$total proxy nodes forwarding"
  [ "$ok" -eq "$total" ]
  check $? "all $total proxy nodes reachable"
fi

# A single free-tier completion. Retries once: when the engine is shared with
# other clients, a single request can lose the race and hit a 60s timeout.
# Two consecutive failures is the real signal.
ask_chat() { # $1 = extra json fragment (e.g. '"stream":false,')
  local extra="$1"
  for attempt in 1 2; do
    body=$(curl -s -m 150 -X POST "$BASE_URL/v1/chat/completions" \
      -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
      -d "{\"model\":\"$MODEL\",${extra}\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OK\"}],\"max_tokens\":120}")
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
ask_chat ""
check $? "chat completion returned content"

# Gates 3 and 4 of the free-tier fingerprint: no tools at all, and stream:false.
# A gateway that only mints valid session headers returns 403 for this exact
# shape. See docs/free-tier-gates.md -- this is the cheapest canary we have.
step "5/6  fingerprint gate: zero tools + stream:false"
ask_chat '"stream":false,'
check $? "zero-tool non-stream request accepted (gates 3+4 intact)"

step "6/6  webui reachable on $WEBUI"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$WEBUI/" 2>/dev/null)
[ "$code" = "200" ]
check $? "webui answers 200 (got $code)"

printf '\n'
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "SOME CHECKS FAILED (see troubleshooting in README.md)"
fi
exit "$fail"
