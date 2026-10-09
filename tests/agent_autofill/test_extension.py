from pathlib import Path


def test_popup_explains_scoped_handle_and_manual_consent_boundary():
    popup = (
        Path(__file__).resolve().parents[2] / "browser_extension" / "popup.html"
    ).read_text()

    assert "scoped, one-time verification handoff" in popup
    assert "grant is the consent step" in popup
    assert "not a reusable link" in popup
    assert "review and submit yourself" in popup
    assert "requests stay in the CLI" in popup
