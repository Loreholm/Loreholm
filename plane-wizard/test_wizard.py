import importlib.util
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("plane_wizard", Path(__file__).with_name("wizard.py"))
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)


def config(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("test")
    os.chmod(key, 0o600)
    return {
        "host": "203.0.113.10", "ssh_user": "deploy", "ssh_key": str(key),
        "base_domain": "plane.example.net", "acme_email": "owner@example.net",
        "oidc_issuer": "https://auth.example.net", "oidc_client_id": "loreholm",
        "oidc_audience": "https://api.plane.example.net",
    }


def test_validate_derives_plane_domains(tmp_path):
    result = wizard.validate(config(tmp_path))
    assert result["chat_domain"] == "chat.plane.example.net"
    assert result["api_domain"] == "api.plane.example.net"
    assert result["mesh_domain"] == "mesh.plane.example.net"


def test_ipv4_can_use_sslip_domain(tmp_path):
    body = config(tmp_path)
    body["base_domain"] = ""
    result = wizard.validate(body)
    assert result["base_domain"] == "203.0.113.10.sslip.io"


def test_rejects_permissive_private_key(tmp_path):
    body = config(tmp_path)
    os.chmod(body["ssh_key"], 0o644)
    with pytest.raises(ValueError, match="0600"):
        wizard.validate(body)
