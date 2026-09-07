from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.dev import install_privacy_hook, privacy_guard
from autonomo_test_support.paths import REPO_ROOT


REVIEWED_UI_VALUES = (
    ("credential-literal", "synthetic-access-token"),
    ("email-address", "secret" + "@drive.google.com"),
    ("google-drive-link", "https://drive.google.com/file/d/synthetic-file-123/view"),
    ("google-drive-link", "https://drive.google.com/open?id=synthetic-file-123"),
    ("google-drive-link", "https://docs.google.com/document/d/synthetic-file-123/edit"),
    ("google-drive-link", "http://drive.google.com/file/d/synthetic-file-123"),
    ("google-drive-link", "https://drive.google.com/drive/folders/synthetic-folder-123"),
    ("personal-field", "Synthetic profile"),
    ("personal-field", "Synthetic other"),
    ("personal-field", "Synthetic saved"),
)


def _ui_specimen(category: str, value: str) -> bytes:
    field = {"credential-literal": "access_token", "personal-field": "full_name"}.get(category)
    return (f"{field}={value!r}\n" if field else value + "\n").encode()


@pytest.mark.parametrize(("category", "value"), REVIEWED_UI_VALUES)
def test_reviewed_ui_values_are_allowed_only_as_exact_findings(category, value):
    for location in ("tests/fixtures/example.py", "notes.txt"):
        assert privacy_guard.scan_content(_ui_specimen(category, value), location) == []
        findings = privacy_guard.scan_content(_ui_specimen(category, value + "-changed"), location)
        assert any(finding.category == category for finding in findings)


@pytest.mark.parametrize(("category", "value"), REVIEWED_UI_VALUES)
def test_reviewed_ui_values_do_not_exempt_other_categories(category, value):
    other_category = "personal-field" if category == "credential-literal" else "credential-literal"
    field = "full_name" if other_category == "personal-field" else "password"
    findings = privacy_guard.scan_content(f"{field}={value!r}\n".encode(), "sample.py")
    assert any(finding.category == other_category for finding in findings)


def test_reviewed_ui_exceptions_are_exactly_the_reviewed_category_digest_pairs():
    assert privacy_guard.ALLOWED_SYNTHETIC_FINDINGS == frozenset(
        (category, hashlib.sha256(value.encode()).hexdigest())
        for category, value in REVIEWED_UI_VALUES
    )


def test_npm_package_manager_version_is_not_an_email():
    version = "npm" + "@" + "11.19.0"
    value = ('{"packageManager": "' + version + '"}').encode()
    for location in ("index:package.json", "history-blob:abc:frontend/package.json"):
        assert not privacy_guard.scan_content(value, location)
    assert any(item.category == "email-address" for item in privacy_guard.scan_content(value, "index:notes.json"))


def test_package_manager_exception_does_not_hide_real_addresses_or_other_fields():
    address = "private" + "@" + "mail" + "." + "net"
    version = "npm" + "@" + "11.19.0"
    for document in (
        '{"packageManager": "' + address + '"}',
        '{"packageManager": "' + version + '", "contact": "' + address + '"}',
        '{"packageManager": "' + version + '", "notes": "' + version + '"}',
        '"packageManager": "' + version + '",',
    ):
        assert any(item.category == "email-address" for item in privacy_guard.scan_content(document.encode(), "index:package.json"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Synthetic Author")
    _git(repo, "config", "user.email", "synthetic@example.invalid")


@pytest.mark.parametrize("private_path", [False, True])
def test_existing_hook_entrypoint_keeps_the_same_guard_result(tmp_path, private_path):
    repo = tmp_path / "repository"
    _init_repo(repo)
    target = repo / ("data/fixture.txt" if private_path else "README.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Synthetic content")
    _git(repo, "add", ".")
    results = [subprocess.run(
        [sys.executable, str(REPO_ROOT / script), "--repo", str(repo)],
        cwd=tmp_path, capture_output=True, text=True,
    ) for script in ("scripts/privacy_guard.py", "scripts/dev/privacy_guard.py")]
    assert [result.returncode for result in results] == [int(private_path)] * 2
    assert results[0].stdout == results[1].stdout
    assert results[0].stderr == results[1].stderr


def test_scan_content_reports_fingerprint_without_secret_value() -> None:
    secret = "gh" + "p_" + "A" * 36

    findings = privacy_guard.scan_content(f"token={secret}\n".encode(), "sample.txt")

    assert [finding.category for finding in findings] == ["service-token"]
    assert findings[0].fingerprint == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in repr(findings)


def test_exact_synthetic_value_hash_is_allowed() -> None:
    findings = privacy_guard.scan_content(b'full_name="Example Taxpayer"\n', "sample.py")

    assert findings == []


def test_commit_messages_ignore_email_addresses_but_keep_other_findings() -> None:
    address = "private.person" + "@example.net"
    secret = "gh" + "p_" + "E" * 36
    message = f"Subject line\n\nCo-Authored-By: Someone <{address}>\ntoken={secret}\n".encode()

    findings = privacy_guard.scan_commit_message(message, "commit-message:0123456789ab")

    assert [finding.category for finding in findings] == ["service-token"]
    assert findings[0].fingerprint == hashlib.sha256(secret.encode()).hexdigest()


def test_email_addresses_in_files_remain_findings() -> None:
    address = "private.person" + "@example.net"

    findings = privacy_guard.scan_content(address.encode(), "sample.txt")

    assert [finding.category for finding in findings] == ["email-address"]


def test_existing_public_bot_email_remains_allowed_in_files() -> None:
    address = "noreply" + "@anthropic.com"

    assert privacy_guard.scan_content(address.encode(), "sample.txt") == []
    assert (
        hashlib.sha256(address.encode()).hexdigest()
        in privacy_guard.ALLOWED_PUBLIC_BOT_EMAIL_SHA256
    )


def test_commit_email_exception_does_not_bypass_credential_rules() -> None:
    field = "pass" + "word"
    specimen = field + '="' + "noreply" + "@anthropic.com" + '"'
    findings = privacy_guard.scan_commit_message(
        specimen.encode(), "commit-message:0123456789ab"
    )
    assert [finding.category for finding in findings] == ["credential-literal"]


def test_history_accepts_coauthor_attribution_without_rewriting_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("safe", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    address = "private.person" + "@example.net"
    _git(repo, "commit", "-m", f"Synthetic change\n\nCo-authored-by: Assistant <{address}>")
    head = _git_output(repo, "rev-parse", "HEAD")

    assert privacy_guard.scan_history(repo, "HEAD") == []
    assert _git_output(repo, "rev-parse", "HEAD") == head


def test_all_history_keeps_other_branches_checked_after_ui_exceptions(tmp_path, capsys):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("safe", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "safe base")
    base = _git_output(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "ui-fixture-history")
    fixture = repo / "fixture.txt"
    fixture.write_bytes(b"".join(_ui_specimen(*pair) for pair in REVIEWED_UI_VALUES))
    _git(repo, "add", "fixture.txt")
    _git(repo, "commit", "-m", "reviewed UI specimens")
    fixture.unlink()
    _git(repo, "commit", "-am", "remove fixture from tip")
    _git(repo, "checkout", "--detach", base)

    assert privacy_guard.main(["--repo", str(repo), "--history"]) == 0
    assert "privacy guard: PASS" in capsys.readouterr().out

    _git(repo, "checkout", "ui-fixture-history")
    unapproved = REVIEWED_UI_VALUES[0][1] + "-unreviewed"
    fixture.write_bytes(_ui_specimen("credential-literal", unapproved))
    _git(repo, "add", "fixture.txt")
    _git(repo, "commit", "-m", "unreviewed specimen on side branch")
    _git(repo, "checkout", "--detach", base)

    assert privacy_guard.scan_history(repo, "HEAD") == []
    assert privacy_guard.main(["--repo", str(repo), "--history"]) == 1
    output = capsys.readouterr().err
    assert "category=credential-literal" in output
    assert "location=history-blob:" in output
    assert ":fixture.txt line=1" in output
    assert f"sha256={hashlib.sha256(unapproved.encode()).hexdigest()}" in output
    assert unapproved not in output
    assert _git_output(repo, "rev-parse", "HEAD") == base


def test_path_rules_do_not_allow_a_fixture_directory_bypass() -> None:
    assert privacy_guard.prohibited_path_reason("tests/fixtures/customer.sqlite") == "private-file-type"
    assert privacy_guard.prohibited_path_reason("nested/browser_sessions/state.json") == "private-directory"
    assert privacy_guard.prohibited_path_reason(".env.example") == "environment-file"
    assert privacy_guard.prohibited_path_reason(".local/config.yaml") == "private-directory"
    assert privacy_guard.prohibited_path_reason("tests/fixtures/example.json") is None


def test_index_and_modified_worktree_are_both_scanned(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    tracked = repo / "tracked.txt"
    staged_secret = "AIza" + "A" * 32
    worktree_secret = "xoxb-" + "B" * 24
    tracked.write_text(staged_secret, encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    tracked.write_text(worktree_secret, encoding="utf-8")

    findings = privacy_guard.scan_index_and_worktree(repo)

    assert {finding.location for finding in findings} == {
        "index:tracked.txt",
        "worktree:tracked.txt",
    }
    assert {finding.category for finding in findings} == {"service-token"}


def test_history_finds_deleted_blob_and_commit_message_without_leaking_value(
    tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    tracked = repo / "tracked.txt"
    deleted_secret = "gh" + "p_" + "C" * 36
    message_secret = "xoxb-" + "C" * 24
    tracked.write_text(deleted_secret, encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", f"temporary token {message_secret}")
    tracked.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-m", "remove temporary file")

    exit_code = privacy_guard.main(["--repo", str(repo), "--history"])
    output = capsys.readouterr()

    assert exit_code == 1
    assert "history-blob:" in output.err
    assert "commit-message:" in output.err
    assert deleted_secret not in output.err
    assert message_secret not in output.err


def test_commit_range_limits_history_scan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("safe", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "safe base")
    base = _git_output(repo, "rev-parse", "HEAD")
    ranged_secret = "gh" + "p_" + "D" * 36
    tracked.write_text(ranged_secret, encoding="utf-8")
    _git(repo, "commit", "-am", "introduce synthetic secret specimen")
    head = _git_output(repo, "rev-parse", "HEAD")

    findings = privacy_guard.scan_history(repo, f"{base}..{head}")

    assert any(finding.category == "service-token" for finding in findings)


def test_prohibited_tracked_path_fails_even_with_safe_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    private_file = repo / "data" / "safe.txt"
    private_file.parent.mkdir()
    private_file.write_text("synthetic content", encoding="utf-8")
    _git(repo, "add", "data/safe.txt")

    findings = privacy_guard.scan_index_and_worktree(repo)

    assert any(finding.category == "private-directory" for finding in findings)


def test_binary_content_fails_closed() -> None:
    findings = privacy_guard.scan_content(b"prefix\x00suffix", "asset.bin")

    assert [finding.category for finding in findings] == ["binary-file"]


def test_iban_pattern_requires_spanish_numeric_bban() -> None:
    fake_iban = "ES" + "12" + "3" * 20

    findings = privacy_guard.scan_content(fake_iban.encode(), "sample.txt")

    assert [finding.category for finding in findings] == ["iban"]
    assert privacy_guard.scan_content(
        b"Estimation self-employment facts indicate a payment obligation.",
        "sample.txt",
    ) == []


def test_hook_installer_is_idempotent_and_preserves_other_hooks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)

    hook = install_privacy_hook.install(repo)

    assert hook.read_text(encoding="utf-8") == install_privacy_hook.HOOK_CONTENT
    assert install_privacy_hook.install(repo) == hook
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    try:
        install_privacy_hook.install(repo)
    except RuntimeError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("a different hook must not be overwritten")
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


def test_package_manager_span_is_root_only_and_supports_multiline_json():
    version = "npm" + "@" + "11.19.0"
    nested = ('{\n"packageManager": "' + version + '",\n"metadata": {"packageManager": "' + version + '"}\n}').encode()
    findings = privacy_guard.scan_content(nested, "index:package.json")
    assert len(findings) == 1 and findings[0].category == "email-address"
    assert findings[0].line == 3
    multiline = ('{\r\n"packageManager":\r\n"' + version + '"\r\n}').encode()
    assert not privacy_guard.scan_content(multiline, "index:package.json")
