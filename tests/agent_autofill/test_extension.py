import json
from pathlib import Path


def test_popup_explains_scoped_handle_and_manual_consent_boundary():
    popup = (
        Path(__file__).resolve().parents[2] / "browser_extension" / "popup.html"
    ).read_text()

    assert "scoped, one-time verification handoff" in popup
    assert "grant is the consent step" in popup
    assert "not a reusable link" in popup
    assert "review and submit yourself" in popup
    assert "Pair this browser session" in popup


def test_extension_manifest_keeps_the_bridge_loopback_only():
    manifest = json.loads(
        (Path(__file__).resolve().parents[2] / "browser_extension" / "manifest.json").read_text()
    )
    assert manifest["manifest_version"] == 3
    assert manifest["host_permissions"] == [
        "http://127.0.0.1:8766/*",
        "http://localhost:8766/*",
    ]
    assert "<all_urls>" not in manifest["host_permissions"]
    assert "tabs" not in manifest["permissions"]
