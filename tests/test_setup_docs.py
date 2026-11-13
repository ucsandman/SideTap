"""Keep the manual and embedded onboarding aligned with upstream asset names."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_setup_names_real_device_zip_not_nonexistent_ipa():
    for name in ("docs/setup-windows.md", "src/phone_harness/viewer.html"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "WebDriverAgentRunner-Runner.zip" in text
        assert "WebDriverAgentRunner-Runner-*.ipa" not in text
        assert "Payload" in text


def test_signing_failure_is_not_presented_as_verified_fix():
    text = (ROOT / "docs/setup-windows.md").read_text(encoding="utf-8")
    assert "community-reported workaround, not a SideTap-verified fix" in text
    assert "never succeeded" in text
    assert "Sideloadly 0.60" in text
