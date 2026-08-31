from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from scripts import install_privacy_hook, privacy_guard


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


def test_scan_content_reports_fingerprint_without_secret_value() -> None:
    secret = "gh" + "p_" + "A" * 36

    findings = privacy_guard.scan_content(f"token={secret}\n".encode(), "sample.txt")

    assert [finding.category for finding in findings] == ["service-token"]
    assert findings[0].fingerprint == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in repr(findings)


def test_exact_synthetic_value_hash_is_allowed() -> None:
    findings = privacy_guard.scan_content(b'full_name="Example Taxpayer"\n', "sample.py")

    assert findings == []


def test_anthropic_coauthor_trailer_is_allowed_in_commit_messages() -> None:
    service_email = "noreply" + "@anthropic.com"
    message = f"Subject line\n\nCo-Authored-By: Claude <{service_email}>\n".encode()

    findings = privacy_guard.scan_content(message, "commit-message:0123456789ab")

    assert findings == []
    assert (
        hashlib.sha256(service_email.encode()).hexdigest()
        in privacy_guard.ALLOWED_PUBLIC_BOT_EMAIL_SHA256
    )


def test_other_coauthor_trailer_emails_remain_findings() -> None:
    personal_email = "private.person" + "@example.net"
    message = f"Subject line\n\nCo-Authored-By: Someone <{personal_email}>\n".encode()

    findings = privacy_guard.scan_content(message, "commit-message:0123456789ab")

    assert [finding.category for finding in findings] == ["email-address"]


def test_only_the_exact_approved_public_bot_email_is_allowed() -> None:
    public_bot = "noreply@anthropic.com"
    assert privacy_guard.scan_content(public_bot.encode(), "sample.txt") == []
    # Negative specimens must not become literal rejected addresses in Git.
    for other_email in (
        "private.person" + "@example.net",
        "private.person" + "@anthropic.com",
        "noreply+other" + "@anthropic.com",
        "noreply" + "@anthropic.com.example.net",
        "NOREPLY" + "@anthropic.com",
    ):
        findings = privacy_guard.scan_content(other_email.encode(), "sample.txt")
        assert [finding.category for finding in findings] == ["email-address"]
        assert findings[0].fingerprint == hashlib.sha256(other_email.encode()).hexdigest()
        assert other_email not in repr(findings)


def test_public_bot_email_does_not_bypass_credential_rules() -> None:
    field = "pass" + "word"
    specimen = field + '="' + "noreply@anthropic.com" + '"'
    findings = privacy_guard.scan_content(specimen.encode(), "sample.txt")
    assert [finding.category for finding in findings] == ["credential-literal"]


def test_history_accepts_public_bot_attribution_without_rewriting_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("safe", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "Synthetic change\n\nCo-authored-by: Assistant <noreply@anthropic.com>")
    head = _git_output(repo, "rev-parse", "HEAD")

    assert privacy_guard.scan_history(repo, "HEAD") == []
    assert _git_output(repo, "rev-parse", "HEAD") == head


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
    message_email = "private.person" + "@example.net"
    tracked.write_text(deleted_secret, encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", f"temporary contact {message_email}")
    tracked.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-m", "remove temporary file")

    exit_code = privacy_guard.main(["--repo", str(repo), "--history"])
    output = capsys.readouterr()

    assert exit_code == 1
    assert "history-blob:" in output.err
    assert "commit-message:" in output.err
    assert deleted_secret not in output.err
    assert message_email not in output.err


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
