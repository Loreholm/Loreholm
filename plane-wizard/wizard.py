#!/usr/bin/env python3
"""Local-only installer UI for a user-owned Loreholm server plane."""
from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
BUNDLE = REPO / "deploy" / "server-plane"
HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def response(handler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def validate(data: dict) -> dict:
    host = str(data.get("host", "")).strip()
    user = str(data.get("ssh_user", "")).strip()
    key = Path(str(data.get("ssh_key", ""))).expanduser().resolve()
    base = str(data.get("base_domain", "")).strip().lower()
    issuer = str(data.get("oidc_issuer", "")).strip().rstrip("/")
    client = str(data.get("oidc_client_id", "")).strip()
    audience = str(data.get("oidc_audience", "")).strip()
    email = str(data.get("acme_email", "")).strip()
    if not HOST_RE.fullmatch(host): raise ValueError("Invalid server address")
    if not USER_RE.fullmatch(user): raise ValueError("Invalid SSH username")
    if not key.is_file(): raise ValueError("SSH private key was not found")
    if key.stat().st_mode & 0o077: raise ValueError("SSH key permissions must be 0600 or stricter")
    if not base:
        if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
            raise ValueError("A base domain is required when the server address is not IPv4")
        base = f"{host}.sslip.io"
    if not DOMAIN_RE.fullmatch(base): raise ValueError("Invalid base domain")
    if not issuer.startswith("https://"): raise ValueError("OIDC issuer must use HTTPS")
    if not client or not audience: raise ValueError("OIDC client ID and audience are required")
    if "@" not in email: raise ValueError("A valid ACME email is required")
    return {**data, "host": host, "ssh_user": user, "ssh_key": str(key), "base_domain": base,
            "oidc_issuer": issuer, "oidc_client_id": client, "oidc_audience": audience,
            "acme_email": email, "chat_domain": f"chat.{base}", "api_domain": f"api.{base}",
            "mesh_domain": f"mesh.{base}"}


def ssh_args(cfg: dict) -> list[str]:
    return ["ssh", "-i", cfg["ssh_key"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            f"{cfg['ssh_user']}@{cfg['host']}"]


def preflight(cfg: dict) -> dict:
    discovery = cfg["oidc_issuer"] + "/.well-known/openid-configuration"
    with urlopen(discovery, timeout=8) as result:
        oidc = json.load(result)
    if oidc.get("issuer", "").rstrip("/") != cfg["oidc_issuer"]:
        raise ValueError("OIDC discovery issuer does not match")
    command = "uname -s; uname -m; command -v docker || true; command -v sudo || true; df -Pk / | tail -1"
    run = subprocess.run(ssh_args(cfg) + [command], text=True, capture_output=True, timeout=15)
    if run.returncode: raise RuntimeError(run.stderr.strip() or "SSH preflight failed")
    lines = run.stdout.splitlines()
    return {"ssh": "connected", "os": lines[0] if lines else "unknown",
            "architecture": lines[1] if len(lines) > 1 else "unknown",
            "docker": any("docker" in line for line in lines[2:]), "oidc": "verified",
            "domains": [cfg["chat_domain"], cfg["api_domain"], cfg["mesh_domain"]]}


def plan(cfg: dict) -> dict:
    return {"server": f"{cfg['ssh_user']}@{cfg['host']}", "install_dir": "/opt/loreholm-plane",
            "domains": [cfg["chat_domain"], cfg["api_domain"], cfg["mesh_domain"]],
            "services": ["Caddy", "Loreholm chat", "Loreholm relay API", "Headscale", "Tailscale", "Redis"],
            "credential_policy": "Generated on this machine and written only to the user's server",
            "ssh_key": cfg["ssh_key"]}


def install(cfg: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="loreholm-plane-") as temp:
        stage = Path(temp) / "plane"
        shutil.copytree(BUNDLE, stage)
        headscale = (stage / "headscale.yaml").read_text().replace("__MESH_DOMAIN__", cfg["mesh_domain"])
        (stage / "headscale.yaml").write_text(headscale)
        env = {
            "CHAT_DOMAIN": cfg["chat_domain"], "API_DOMAIN": cfg["api_domain"],
            "MESH_DOMAIN": cfg["mesh_domain"], "ACME_EMAIL": cfg["acme_email"],
            "OIDC_ISSUER": cfg["oidc_issuer"], "OIDC_CLIENT_ID": cfg["oidc_client_id"],
            "OIDC_AUDIENCE": cfg["oidc_audience"],
            "LOCAL_SYNC_SIGNING_SECRET": secrets.token_urlsafe(48),
            "API_KEY_SIGNING_SECRET": secrets.token_urlsafe(48),
            "REDIS_PASSWORD": secrets.token_urlsafe(36),
        }
        (stage / ".env").write_text("".join(f"{key}={value}\n" for key, value in env.items()))
        os.chmod(stage / ".env", 0o600)
        archive = Path(temp) / "plane.tar.gz"
        with tarfile.open(archive, "w:gz") as tar: tar.add(stage, arcname="plane")
        remote_archive = f"/tmp/loreholm-plane-{secrets.token_hex(6)}.tar.gz"
        copy = subprocess.run(["scp", "-i", cfg["ssh_key"], "-o", "BatchMode=yes", str(archive),
                               f"{cfg['ssh_user']}@{cfg['host']}:{remote_archive}"],
                              text=True, capture_output=True, timeout=60)
        if copy.returncode: raise RuntimeError(copy.stderr.strip() or "Upload failed")
        script = f'''set -Eeuo pipefail
if ! command -v docker >/dev/null; then
  if command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
  elif command -v pacman >/dev/null; then sudo pacman -Sy --noconfirm docker docker-compose && sudo systemctl enable --now docker
  elif command -v dnf >/dev/null; then sudo dnf install -y docker docker-compose-plugin && sudo systemctl enable --now docker
  else echo "Unsupported package manager; install Docker first" >&2; exit 4; fi
fi
sudo mkdir -p /opt/loreholm-plane
sudo tar -xzf {remote_archive} -C /opt/loreholm-plane --strip-components=1
sudo rm -f {remote_archive}
sudo chmod 600 /opt/loreholm-plane/.env
cd /opt/loreholm-plane
sudo docker compose --env-file .env pull
sudo docker compose --env-file .env up -d
sudo docker compose --env-file .env ps
'''
        run = subprocess.run(ssh_args(cfg) + [script], text=True, capture_output=True, timeout=900)
        if run.returncode: raise RuntimeError((run.stderr or run.stdout)[-4000:])
    fingerprint = hashlib.sha256(cfg["mesh_domain"].encode()).hexdigest()[:16]
    return {"ok": True, "chat_url": f"https://{cfg['chat_domain']}",
            "api_url": f"https://{cfg['api_domain']}", "mesh_url": f"https://{cfg['mesh_domain']}",
            "pairing": f"loreholm instance pair https://{cfg['api_domain']}",
            "plane_fingerprint": fingerprint, "details": run.stdout[-3000:]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT / "static"), **kwargs)
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            cfg = validate(json.loads(self.rfile.read(length) or b"{}"))
            if self.path == "/api/preflight": payload = preflight(cfg)
            elif self.path == "/api/plan": payload = plan(cfg)
            elif self.path == "/api/install": payload = install(cfg)
            else: return response(self, 404, {"error": "Not found"})
            response(self, 200, payload)
        except Exception as exc:
            response(self, 400, {"error": str(exc)})
    def log_message(self, format, *args): pass


if __name__ == "__main__":
    port = int(os.getenv("LOREHOLM_WIZARD_PORT", "8765"))
    print(f"Loreholm plane wizard: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
