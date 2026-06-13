#!/bin/sh
# Bootstrap script for tailscale-api authentication
# Enhanced for robust Docker startup and Headscale API communication

# We handle errors manually to provide better logging
set +e 

HEADSCALE_URL="${HEADSCALE_API_URL:-https://headscale:50443}"
HEADSCALE_KEY="${HEADSCALE_API_KEY}"
KEY_FILE="/var/lib/tailscale-init/authkey"

# -k: Ignore self-signed/internal cert mismatch
# -s: Silent (no progress bar)
# -S: Show error message if it fails
CURL_OPTS="-k -s -S"

echo "[tailscale-init] Starting bootstrap..."
echo "[tailscale-init] Target: ${HEADSCALE_URL}"

# 1. WAIT FOR HEADSCALE API
echo "[tailscale-init] Waiting for Headscale API to respond..."
for i in $(seq 1 60); do
    # Check if the API is reachable and accepting the key
    # We check for a 200 (Success) or 401 (Unauthorized) 
    # 401 means service is up but key is wrong; 000 means service is down
    HTTP_STATUS=$(curl $CURL_OPTS -o /tmp/api_check.txt -w "%{http_code}" \
        "${HEADSCALE_URL}/api/v1/apikey" \
        -H "Authorization: Bearer ${HEADSCALE_KEY}")

    if [ "$HTTP_STATUS" = "200" ]; then
        echo "[tailscale-init] Headscale API is ready (200 OK)"
        break
    elif [ "$HTTP_STATUS" = "401" ]; then
        echo "[tailscale-init] ERROR: API key is invalid (401 Unauthorized)"
        cat /tmp/api_check.txt
        exit 1
    fi

    if [ "$i" -eq 60 ]; then
        echo "[tailscale-init] ERROR: Timed out waiting for Headscale (Last status: $HTTP_STATUS)"
        exit 1
    fi

    echo "[tailscale-init] Still waiting... (Status: $HTTP_STATUS, Attempt $i/60)"
    sleep 2
done

# 2. ENSURE ADMIN USER
echo "[tailscale-init] Ensuring 'admin' user exists..."
# We don't exit on failure here because 'User already exists' returns an error code
curl $CURL_OPTS -X POST "${HEADSCALE_URL}/api/v1/user" \
    -H "Authorization: Bearer ${HEADSCALE_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"name": "admin"}' > /dev/null 2>&1

# 3. HANDLE PRE-AUTH KEY
if [ -f "$KEY_FILE" ] && [ -s "$KEY_FILE" ]; then
    echo "[tailscale-init] Valid existing auth key found. Skipping creation."
    exit 0
fi

echo "[tailscale-init] Creating new reusable pre-auth key..."

# Portable date calculation for BusyBox (Alpine)
# Current time + 365 days
CURRENT_EPOCH=$(date +%s)
FUTURE_EPOCH=$((CURRENT_EPOCH + 31536000))
EXPIRATION=$(date -u -d "@${FUTURE_EPOCH}" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "2027-01-01T00:00:00Z")

echo "[tailscale-init] Expiration set to: ${EXPIRATION}"

RESPONSE=$(curl $CURL_OPTS -X POST "${HEADSCALE_URL}/api/v1/preauthkey" \
    -H "Authorization: Bearer ${HEADSCALE_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"user\": \"admin\", \"reusable\": true, \"ephemeral\": false, \"expiration\": \"${EXPIRATION}\"}")

# Extract key using a more robust grep pattern
AUTH_KEY=$(echo "$RESPONSE" | grep -oE '"key":"[^"]+"' | sed 's/"key":"//;s/"//')

if [ -z "$AUTH_KEY" ]; then
    echo "[tailscale-init] ERROR: Failed to parse auth key from response"
    echo "[tailscale-init] Full Response: $RESPONSE"
    exit 1
fi

# 4. SAVE AND FINISH
echo "$AUTH_KEY" > "$KEY_FILE"
chmod 600 "$KEY_FILE"

echo "[tailscale-init] Bootstrap complete. Key saved to $KEY_FILE"