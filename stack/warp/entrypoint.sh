#!/bin/bash
# Entrypoint for the WARP rotator node.
#
# Derived from alztrk/opencode-ip-rotator's Dockerfile.warp inline entrypoint,
# with one change: the Cloudflare terms of service are no longer accepted
# silently on the operator's behalf. They must be accepted explicitly.
#
#   WARP_ACCEPT_TOS=1
#
# Read https://www.cloudflare.com/application/terms/ before setting it. Relevant
# excerpt (Application Terms of Service, section 3 "VPN Profile"):
#
#   "Although the Application will need to install a VPN profile on your device
#    and operates similarly to a VPN product or service, the Application is not
#    designed to hide your identity from the Internet properties you access from
#    your device."
#
# Section 6 also forbids using the Application to "misrepresent or act on behalf
# of others" and to "create an undue burden on the Application or the networks".

set -euo pipefail

if [ "${WARP_ACCEPT_TOS:-0}" != "1" ]; then
  cat >&2 <<'MSG'
WARP terms of service were not accepted.

This image runs the Cloudflare WARP client, which is governed by the Cloudflare
Application Terms of Service: https://www.cloudflare.com/application/terms/

If you have read and accept those terms, opt in explicitly:

    echo 'WARP_ACCEPT_TOS=1' > .env      # next to docker-compose.yml
    docker compose up -d

Note that Cloudflare states the WARP client "is not designed to hide your
identity" from the sites you reach. Using it to appear as several different
users is outside that intent, and Cloudflare may terminate accounts without
notice for violations.

Using a different egress source? You do not need this image: point the
"proxies" array in config.json at your own HTTP/SOCKS proxies and drop the
alztrk-warp-* services from the compose file.
MSG
  exit 1
fi

service dbus start
warp-svc &
sleep 3

warp-cli --accept-tos registration new
warp-cli --accept-tos mode warp
warp-cli --accept-tos connect
sleep 3

exec python3 rotator.py