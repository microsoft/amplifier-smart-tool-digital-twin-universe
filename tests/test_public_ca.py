"""A public trust bundle stays readable without widening private state permissions."""

import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from digital_twin_universe.capabilities.universe import overlay, state


def test_exported_public_ca_is_readable_under_private_umask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    previous = os.umask(0o077)
    try:
        assets = tmp_path / "assets"
        ca = overlay._ca_path(assets)
        private_key = assets / "private-key.pem"
        private_key.write_text("PRIVATE KEY MUST STAY PRIVATE")
        roots = tmp_path / "public-roots.pem"
        roots.write_text("PUBLIC ROOTS")

        def copy_public(source: str, destination: Path) -> None:
            assert source == overlay.MITM_CA
            destination.write_text("PUBLIC GATEWAY CERTIFICATE")

        gateway = SimpleNamespace(copy_from=copy_public)
        monkeypatch.setattr(overlay, "project_containers", lambda identity: [gateway])
        monkeypatch.setattr(overlay, "service_name", lambda container: overlay.GATEWAY_SERVICE)
        monkeypatch.setattr(overlay.certifi, "where", lambda: str(roots))
        record = state.UniverseRecord(
            id="dtu-public-ca-test",
            name="public-ca-test",
            description=None,
            profile_path=tmp_path / "compose.yaml",
            twin_machine="box",
            created_at=state.now(),
        )

        overlay.export_ca(record, overlay.Overlay(None, [overlay.GATEWAY_SERVICE], ca, []))

        assert ca.read_text() == "PUBLIC ROOTS\nPUBLIC GATEWAY CERTIFICATE"
        assert stat.S_IMODE(ca.stat().st_mode) == 0o644
        assert stat.S_IMODE(ca.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(assets.stat().st_mode) == 0o700
        assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
        assert private_key.read_text() == "PRIVATE KEY MUST STAY PRIVATE"
        assert not ca.with_suffix(".minted").exists()
    finally:
        os.umask(previous)
