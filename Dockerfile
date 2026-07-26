FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Essential tools & dependencies
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

# Install FastAPI and Uvicorn for zen_server
RUN pip3 install fastapi uvicorn pydantic

# Add Cloudflare WARP repository & install warp-cli
RUN curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list \
    && apt-get update \
    && apt-get install -y cloudflare-warp \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (OpenCode dependency)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install OpenCode CLI globally
RUN npm install -g opencode-ai || true

WORKDIR /app

# Copy rotator script and zen server
COPY rotator.py /app/rotator.py
COPY server.py /app/server.py
COPY rate_limits.py /app/rate_limits.py

# Entrypoint script to handle DBus, WARP service, Zen Server and Rotator
RUN echo '#!/bin/bash\n\
service dbus start\n\
warp-svc &\n\
sleep 3\n\
warp-cli --accept-tos registration new\n\
warp-cli --accept-tos mode warp\n\
warp-cli --accept-tos connect\n\
sleep 3\n\
python3 /app/rotator.py &\n\
exec python3 /app/server.py\n\
' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
