#!/bin/sh
set -eu
mkdir -p /run/loreholm
chmod 700 /run/loreholm
until headscale users list -o json >/dev/null 2>&1; do sleep 1; done
headscale users list -o json | grep -q '"plane-api"' || headscale users create plane-api
if [ ! -s /run/loreholm/headscale-api-key ]; then
  headscale apikeys create --expiration 87600h > /run/loreholm/headscale-api-key
fi
if [ ! -s /run/loreholm/plane-api-auth-key ]; then
  headscale preauthkeys create --user plane-api --reusable --expiration 87600h > /run/loreholm/plane-api-auth-key
fi
chmod 600 /run/loreholm/*
