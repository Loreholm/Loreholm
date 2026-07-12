# Loreholm Plane Wizard

Run from a Loreholm source checkout:

```bash
python plane-wizard/wizard.py
```

Then open `http://127.0.0.1:8765`.

The wizard binds to loopback only. SSH and SCP are launched as argument arrays,
not through a local shell. The private key is read by the installed OpenSSH
client and is never copied into the browser, deployment archive, or server.

Before starting, configure the OIDC public client to allow this redirect URI:

```text
https://chat.<base-domain>/
```

The server needs inbound TCP ports 22, 80, and 443. If no base domain is
provided for an IPv4 server, the wizard uses `<ip>.sslip.io` names so the first
installation can receive publicly trusted certificates without a DNS API.
