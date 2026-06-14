#!/bin/sh
# /opt/mcp-cloud/deploy/tailscale-entrypoint.sh

# Path to the key created by tailscale-init
AUTHKEY_FILE="/var/lib/tailscale-init/authkey"

# Wait for the init container to actually write the file
while [ ! -f "$AUTHKEY_FILE" ]; do
  echo "Waiting for authkey file..."
  sleep 2
done

AUTHKEY=$(cat "$AUTHKEY_FILE")

# Start tailscaled in the background
/usr/local/bin/tailscaled --state=/var/lib/tailscale/tailscaled.state &

# Give tailscaled a moment to start
sleep 2

# Authenticate with the pre-auth key
# We use --accept-routes so the API can reach the client databases
tailscale up \
    --login-server=https://${HEADSCALE_DOMAIN:-localhost}:50443 \
    --authkey="${AUTHKEY}" \
    --accept-routes \
    --hostname=loreholm-api

# Keep the container running
wait