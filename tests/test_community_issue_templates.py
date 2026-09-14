from pathlib import Path


ROOT = Path(__file__).parents[1]
TEMPLATE_DIR = ROOT / ".github" / "ISSUE_TEMPLATE"


def test_local_issue_chooser_has_unassigned_community_forms() -> None:
    for name in ("bug_report.yml", "documentation.yml", "feature_request.yml", "support.yml"):
        text = (TEMPLATE_DIR / name).read_text()
        assert "validations:" in text
        assert "assignees:" not in text


def test_issue_chooser_routes_to_public_forum_and_private_security() -> None:
    config = (TEMPLATE_DIR / "config.yml").read_text()

    assert "https://github.com/orgs/basefoundry/discussions" in config
    assert "https://github.com/basefoundry/base-cli/security/advisories/new" in config
