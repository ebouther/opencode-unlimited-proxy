FROM alpine:3.20

RUN apk add --no-cache tinyproxy

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf

EXPOSE 8888

# -d = stay in foreground (so Docker can supervise it)
CMD ["tinyproxy", "-d"]
