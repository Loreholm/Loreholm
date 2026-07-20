#!/bin/sh
set -eu
tailscaled --state=/var/lib/tailscale/tailscaled.state &
pid=$!
until tailscale status >/dev/null 2>&1; do sleep 1; done
if ! tailscale status --json 2>/dev/null | grep -q '"BackendState":"Running"'; then
  tailscale up --login-server="https://${MESH_DOMAIN}" --authkey="$(cat /run/loreholm/plane-api-auth-key)" --hostname=loreholm-plane-api
fi
wait "$pid"
