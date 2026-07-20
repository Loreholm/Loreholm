FROM headscale/headscale:0.25.1 AS headscale
FROM alpine:3.22
RUN apk add --no-cache ca-certificates
COPY --from=headscale /ko-app/headscale /usr/local/bin/headscale
ENTRYPOINT ["/bin/sh"]
