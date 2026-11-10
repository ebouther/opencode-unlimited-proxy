FROM docker.io/library/alpine:3.20

ENV WARP_LOG_LEVEL=info

# Install system dependencies: Python3, pip, uvicorn, networking tools, ca-certificates, and bash for entrypoint.sh
RUN apk add --no-cache \
    python3 \
    py3-pip \
    uvicorn \
    curl \
    ca-certificates \
    iptables \
    iproute2 \
    bash

# Install python dependencies for rotator.py
RUN python3 -m pip install --break-system-packages --no-cache-dir curl_cffi fastapi

# Extract Cloudflare WARP bundle from AnyLinux AppImage (SrDicov/anylinux-builds)
ARG WARP_APPIMAGE_URL=https://github.com/SrDicov/anylinux-builds/releases/download/continuous/warp-x86_64.AppImage
RUN curl -fsSL -o /tmp/warp.AppImage "${WARP_APPIMAGE_URL}" && \
    chmod +x /tmp/warp.AppImage && \
    /tmp/warp.AppImage --appimage-extract && \
    rm -rf /opt/warp && \
    (mv AppDir /opt/warp 2>/dev/null || mv squashfs-root /opt/warp) && \
    rm -f /tmp/warp.AppImage && \
    ln -s /opt/warp/bin/warp-apprun /usr/local/bin/warp-cli && \
    ln -s /opt/warp/bin/warp-apprun /usr/local/bin/warp-svc

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
