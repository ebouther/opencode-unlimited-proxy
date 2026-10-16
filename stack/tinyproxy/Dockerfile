FROM alpine:3.20

RUN apk add --no-cache tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY gate.sh /usr/local/bin/gate.sh
RUN chmod +x /usr/local/bin/gate.sh

EXPOSE 8888

# gate.sh supervises tinyproxy so the node can be drained at runtime; see the
# comments in gate.sh. NODE_ID must be set by compose.
ENTRYPOINT ["/usr/local/bin/gate.sh"]
