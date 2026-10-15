FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV WARP_LOG_LEVEL=info

RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    lsb-release \
    sudo \
    python3 \
    python3-pip \
    iptables \
    dbus \
    net-tools \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install curl_cffi "fastapi" "uvicorn[standard]"

RUN curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list \
    && apt-get update \
    && apt-get install -y cloudflare-warp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY rotator.py .
COPY manager.py .
COPY entrypoint.sh .

# rotator.py + manager.py are verbatim from alztrk/opencode-ip-rotator (MIT,
# see LICENSE in this directory). entrypoint.sh is derived from that project's
# Dockerfile.warp entrypoint: the Cloudflare ToS must be accepted explicitly
# via WARP_ACCEPT_TOS=1 instead of being accepted silently here.
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]