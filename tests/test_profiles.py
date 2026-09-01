"""Profile validation, and the deterministic seams of profile creation."""

import json

import pytest

from amplifier_digital_twin_universe.cli import main
from amplifier_digital_twin_universe.create import extract_yaml
from amplifier_digital_twin_universe.knowledge import authoring_guide
from amplifier_digital_twin_universe.profiles import validate_profile

MINIMAL = "base:\n  image: ubuntu:24.04\n"


def test_minimal_profile_is_launchable():
    report = validate_profile(MINIMAL)
    assert report.valid
    assert report.errors == []
    assert report.warnings == []


def test_missing_base_image_is_an_error():
    report = validate_profile("name: nope\n")
    assert not report.valid
    assert any("base.image" in e for e in report.errors)


def test_unknown_field_is_reported_not_silently_dropped():
    report = validate_profile(MINIMAL + "provisioning:\n  setup_cmds: []\n")
    assert report.valid
    assert any("provisioning" in w for w in report.warnings)


def test_repo_rewrite_with_prefix_mode_is_flagged():
    profile = (
        MINIMAL
        + "url_rewrites:\n"
        + "  rules:\n"
        + "    - match: github.com/microsoft/amplifier\n"
        + "      target: https://example.test/admin/amplifier\n"
    )
    report = validate_profile(profile)
    assert report.valid
    assert any("boundary" in w for w in report.warnings)


def test_boundary_mode_silences_the_repo_rewrite_warning():
    profile = (
        MINIMAL
        + "url_rewrites:\n"
        + "  default_match_mode: boundary\n"
        + "  rules:\n"
        + "    - match: github.com/microsoft/amplifier\n"
        + "      target: https://example.test/admin/amplifier\n"
    )
    report = validate_profile(profile)
    assert report.valid
    assert report.warnings == []


def test_variables_are_substituted_before_validation():
    profile = MINIMAL + "access:\n  ports:\n    - host: ${PORT}\n      container: 8080\n"
    assert not validate_profile(profile).valid
    assert validate_profile(profile, {"PORT": "31000"}).valid


def test_unresolved_variables_are_reported():
    profile = (
        MINIMAL
        + "url_rewrites:\n"
        + "  default_match_mode: boundary\n"
        + "  rules:\n"
        + "    - match: github.com/o/r\n"
        + "      target: ${GITEA_URL}/admin/r\n"
    )
    report = validate_profile(profile)
    assert report.valid
    assert report.unresolved_variables == ["GITEA_URL"]


def test_missing_required_key_does_not_escape_as_a_bare_keyerror():
    profile = MINIMAL + "readiness:\n  - name: check\n    tcp:\n      prt: 8080\n"
    report = validate_profile(profile)
    assert not report.valid
    assert report.errors


def test_malformed_yaml_is_an_error_not_a_crash():
    report = validate_profile("base:\n  image: [unclosed\n")
    assert not report.valid
    assert report.errors


def test_feedback_renders_findings_for_repair():
    report = validate_profile("name: nope\n")
    feedback = report.feedback()
    assert "base.image" in feedback
    assert feedback.startswith("Errors")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("```yaml\nbase:\n  image: x\n```", "base:\n  image: x\n"),
        ("prose\n```yml\na: 1\n```\nmore", "a: 1\n"),
        ("```\nc: 3\n```", "c: 3\n"),
        ("no fence here", None),
    ],
)
def test_extract_yaml_takes_the_last_fenced_block(reply, expected):
    assert extract_yaml(reply) == expected


def test_authoring_guide_ships_with_the_package():
    guide = authoring_guide()
    assert "base.image" in guide
    assert "readiness" in guide
    assert len(guide) > 2000


def test_cli_validate_profile_reports_valid(tmp_path, capsys):
    path = tmp_path / "p.yaml"
    path.write_text(MINIMAL, encoding="utf-8")
    assert main(["validate-profile", "--file", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["valid"] is True


def test_cli_validate_profile_exits_nonzero_when_invalid(tmp_path, capsys):
    path = tmp_path / "p.yaml"
    path.write_text("name: nope\n", encoding="utf-8")
    assert main(["validate-profile", "--file", str(path)]) == 5
    assert json.loads(capsys.readouterr().out)["result"]["valid"] is False


def test_cli_validate_profile_accepts_variables(tmp_path, capsys):
    path = tmp_path / "p.yaml"
    path.write_text(
        MINIMAL + "access:\n  ports:\n    - host: ${PORT}\n      container: 8080\n",
        encoding="utf-8",
    )
    assert main(["validate-profile", "--file", str(path), "--var", "PORT=31000"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["valid"] is True


def test_cli_unreadable_input_names_a_remedy(capsys):
    assert main(["validate-profile", "--file", "/nonexistent/p.yaml"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "unreadable_input"
    assert error["remedy"]


def test_provision_files_source_that_does_not_exist_is_an_error(tmp_path):
    profile = MINIMAL + "provision:\n  files:\n    - src: /definitely/not/here.js\n      dest: /root/x.js\n"
    report = validate_profile(profile, base_dir=tmp_path)
    assert not report.valid
    assert any("does not exist" in e for e in report.errors)


def test_provision_files_source_relative_to_base_dir_resolves(tmp_path):
    (tmp_path / "server.js").write_text("//\n", encoding="utf-8")
    profile = MINIMAL + "provision:\n  files:\n    - src: server.js\n      dest: /root/x.js\n"
    assert validate_profile(profile, base_dir=tmp_path).valid
    assert not validate_profile(profile, base_dir=tmp_path / "elsewhere").valid


def test_directory_source_without_recursive_is_an_error(tmp_path):
    (tmp_path / "seed").mkdir()
    profile = MINIMAL + "provision:\n  files:\n    - src: seed\n      dest: /root/seed\n"
    report = validate_profile(profile, base_dir=tmp_path)
    assert not report.valid
    assert any("recursive" in e for e in report.errors)


def test_guide_forbids_inventing_files_and_system_pip():
    guide = authoring_guide()
    assert "externally managed" in guide
    assert "heredoc" in guide
