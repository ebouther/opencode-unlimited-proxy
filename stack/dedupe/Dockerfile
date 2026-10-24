FROM docker.io/library/python:3.12-alpine

# Needed to verify the CA chain when measuring each node's egress address
# through its own CONNECT proxy.
RUN apk add --no-cache ca-certificates

COPY dedupe.py /app/dedupe.py

ENTRYPOINT ["python3", "/app/dedupe.py"]
