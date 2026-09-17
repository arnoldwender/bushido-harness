"""Tests for the live hook, hooks/irreversible-before-run.py.

Same pair as the gate's own suite: the destructive command aimed at a path that
cannot be rebuilt must be WARNED about, and the identical command aimed at a
rebuildable path must pass in silence. The hook is run as a PreToolUse
subprocess with the payload on stdin, inside a small git repo, and the exit
code, the stdout JSON and the receipt are what is asserted.

    python3 -m pytest tests/test_irreversible_hook.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# The mutation runner points this at a mutated COPY; the real hook is never rewritten.
HOOK = Path(os.environ.get("IRREVERSIBLE_HOOK_UNDER_TEST")
            or Path(__file__).resolve().parent.parent / "hooks" / "irreversible-before-run.py")

BASELINE = {
    "deploy.sh": "#!/bin/sh\nset -eu\necho deploying\n",
    "src/lib/app.py": "VERSION = 1\n",
    "node_modules/pkg/clean.sh": "#!/bin/sh\necho vendored\n",
}


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel, body in BASELINE.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "init.templateDir=",
                    "init", "-q", str(tmp_path)], check=True, capture_output=True)
    git(tmp_path, "config", "user.email", "gate@example.invalid")
    git(tmp_path, "config", "user.name", "Gate Test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "baseline")
    return tmp_path


def hook(root: Path, command: str, tool: str = "Bash", env: dict[str, str] | None = None
         ) -> tuple[int, dict | None, str, dict | None]:
    receipts = root / "receipts.jsonl"
    payload = {"session_id": "test-session", "cwd": str(root), "hook_event_name": "PreToolUse",
               "tool_name": tool, "tool_input": {"command": command}, "tool_use_id": "toolu_x"}
    run_env = {**os.environ, "IRREVERSIBLE_RECEIPTS": str(receipts)}
    run_env.pop("IRREVERSIBLE_HOOK_MODE", None)
    run_env.update(env or {})
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=run_env, cwd=str(root),
                       check=False, timeout=60)
    out = json.loads(r.stdout) if r.stdout.strip() else None
    rec = None
    if receipts.is_file():
        rec = json.loads(receipts.read_text(encoding="utf-8").strip().split("\n")[-1])
    return r.returncode, out, r.stderr, rec


def warning(out: dict | None) -> str:
    return ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext") or ""


# --- the control -------------------------------------------------------------

def test_a_harmless_command_is_silent(repo: Path) -> None:
    """Without this, every test below could pass because the hook always warns."""
    rc, out, _, rec = hook(repo, "cp dist/index.html /srv/www/index.html && echo done")
    assert rc == 0 and out is None, (rc, out)
    assert rec["verdict"] == "ok" and rec["lines"] == 1


# --- the pair: red on the tracked path, green on the rebuildable one ---------

def test_rm_rf_on_a_tracked_path_warns(repo: Path) -> None:
    rc, out, _, rec = hook(repo, "rm -rf src/lib")
    assert rc == 0
    assert "[rm-rf]" in warning(out) and "tracked in git" in warning(out)
    assert "NOT blocked" in warning(out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert rec["verdict"] == "finding" and rec["checks"] == ["rm-rf"]


@pytest.mark.parametrize("target", ["node_modules", "dist/", "build", ".cache", "coverage",
                                    "/tmp/agent-scratch", "logs/app.log", '"$(mktemp -d)"'])
def test_rm_rf_on_a_rebuildable_path_is_silent(repo: Path, target: str) -> None:
    rc, out, _, rec = hook(repo, f"rm -rf {target}")
    assert rc == 0 and out is None, f"warned on a rebuildable target: {warning(out)}"
    assert rec["verdict"] == "ok"


def test_rm_rf_behind_a_variable_is_a_warning_not_silence(repo: Path) -> None:
    _, out, _, rec = hook(repo, 'rm -rf "$BUILD_DIR"')
    assert "[unexpanded-target]" in warning(out)
    assert rec["levels"] == ["warning"]


def test_rm_without_force_is_out_of_scope(repo: Path) -> None:
    _, out, _, rec = hook(repo, "rm -r src/lib")
    assert out is None and rec["verdict"] == "ok"


def test_git_reset_hard_warns_and_force_with_lease_does_not(repo: Path) -> None:
    _, out, _, _ = hook(repo, "git reset --hard origin/main")
    assert "[git-destructive]" in warning(out)
    _, out, _, rec = hook(repo, "git push --force-with-lease origin main")
    assert out is None and rec["verdict"] == "ok"


def test_git_push_force_warns(repo: Path) -> None:
    _, out, _, _ = hook(repo, "git push -f origin main")
    assert "[git-destructive]" in warning(out)


def test_a_chained_command_is_judged_segment_by_segment(repo: Path) -> None:
    _, out, _, rec = hook(repo, "cd /srv && npm ci && rm -rf src/lib && echo ok")
    assert "[rm-rf]" in warning(out) and rec["verdict"] == "finding"


def test_a_multiline_command_is_judged_line_by_line(repo: Path) -> None:
    _, out, _, rec = hook(repo, "echo start\nterraform destroy -auto-approve\necho end")
    assert "[cloud-destructive]" in warning(out) and rec["lines"] == 3


def test_sql_through_an_interpreter_heredoc_is_judged(repo: Path) -> None:
    """A heredoc fed to psql is a script delivered differently."""
    _, out, _, _ = hook(repo, "psql app <<'SQL'\nDELETE FROM sessions;\nSQL")
    assert "[sql-destructive]" in warning(out)


def test_a_where_clause_on_the_next_line_still_counts(repo: Path) -> None:
    _, out, _, rec = hook(repo, "psql app <<'SQL'\nDELETE FROM sessions\n  WHERE id = 1;\nSQL")
    assert out is None and rec["verdict"] == "ok"


def test_a_heredoc_written_to_a_file_is_content_not_a_command(repo: Path) -> None:
    """Writing a runbook that SHOWS `rm -rf` is teaching, not running."""
    _, out, _, rec = hook(repo, "cat > docs/RUNBOOK.md <<'EOF'\nReset:\nrm -rf src/lib\nEOF")
    assert out is None, warning(out)
    assert rec["verdict"] == "ok"


def test_a_comment_line_is_not_an_instruction(repo: Path) -> None:
    _, out, _, rec = hook(repo, "# never do this: rm -rf src/lib\necho fine")
    assert out is None and rec["verdict"] == "ok"


def test_removing_a_git_hook_warns(repo: Path) -> None:
    _, out, _, _ = hook(repo, "rm -f .git/hooks/pre-commit")
    assert "[git-hook-removal]" in warning(out)


def test_outside_a_git_repo_the_target_is_still_not_provably_disposable(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    _, out, _, rec = hook(tmp_path, "rm -rf src")
    assert "[rm-rf]" in warning(out) and rec["verdict"] == "finding"


def test_several_findings_are_capped_in_the_text(repo: Path) -> None:
    cmd = "\n".join(["rm -rf src/lib", "git reset --hard", "git push -f origin main",
                     "terraform destroy", "kubectl delete pvc x", "DROP TABLE users;"])
    _, out, _, rec = hook(repo, cmd)
    assert "(+2 more)" in warning(out) and len(rec["checks"]) >= 4


# --- what the hook knows that the gate does not ------------------------------

def test_a_variable_assigned_in_the_same_command_is_resolved(repo: Path) -> None:
    """`T=$(mktemp -d)` three lines up makes `rm -rf "$T"` a scratch directory."""
    _, out, _, rec = hook(repo, 'T=$(mktemp -d)\nmkdir -p "$T/x"\nrm -rf "$T"')
    assert out is None, warning(out)
    assert rec["verdict"] == "ok"


def test_a_variable_resolved_to_a_tracked_path_still_warns(repo: Path) -> None:
    _, out, _, _ = hook(repo, 'D=src/lib; rm -rf "$D"')
    assert "[rm-rf]" in warning(out) and "tracked in git" in warning(out)


def test_a_variable_set_elsewhere_stays_unexpanded(repo: Path) -> None:
    """A loop variable could be anything; the warning stays."""
    _, out, _, _ = hook(repo, 'for d in a b; do rm -rf "$d"; done')
    assert "[unexpanded-target]" in warning(out)


def test_a_variable_assigned_earlier_on_the_same_line_is_resolved(repo: Path) -> None:
    _, out, _, rec = hook(repo, 'S=/tmp/probe-9; mkdir -p "$S"; rm -rf "$S/x"')
    assert out is None, warning(out)
    assert rec["verdict"] == "ok"


def test_a_leading_cd_into_scratch_makes_relative_targets_scratch(repo: Path) -> None:
    """`cd <scratch> && rm -rf probe`: the scratch dir lives under the system temp tree the
    gate calls ephemeral and is not a repository, so `probe` is scratch too. (It must be
    OUTSIDE the fixture repo: a scratch directory inside a repository is inside a repository.)"""
    scratch = Path(tempfile.mkdtemp(prefix="hook-scratch-"))
    try:
        (scratch / "probe").mkdir()
        _, out, _, rec = hook(repo, f"cd {scratch} && rm -rf probe && mkdir probe")
        assert out is None, warning(out)
        assert rec["verdict"] == "ok"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_a_leading_cd_into_a_repository_under_tmp_is_still_a_repository(repo: Path) -> None:
    """A second repository under the temp tree: `cd` there and remove a tracked path. The
    temp prefix alone must not make it scratch — a repository is a repository."""
    other = Path(tempfile.mkdtemp(prefix="hook-other-repo-"))
    try:
        (other / "keep").mkdir()
        (other / "keep" / "a.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "init.templateDir=",
                        "init", "-q", str(other)], check=True, capture_output=True)
        git(other, "config", "user.email", "gate@example.invalid")
        git(other, "config", "user.name", "Gate Test")
        git(other, "config", "commit.gpgsign", "false")
        git(other, "add", "-A")
        git(other, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "baseline")
        _, out, _, _ = hook(repo, f"cd {other} && rm -rf keep")
        assert "[rm-rf]" in warning(out) and "tracked in git" in warning(out)
    finally:
        shutil.rmtree(other, ignore_errors=True)


def test_a_cd_in_the_middle_is_not_tracked(repo: Path) -> None:
    """Stated limit: only a cd that opens the command counts."""
    _, out, _, _ = hook(repo, "echo start && cd /tmp && rm -rf src/lib")
    assert "[rm-rf]" in warning(out)


# --- the allowlist -----------------------------------------------------------

def test_an_allowlisted_command_is_silent(repo: Path) -> None:
    (repo / ".conduct").mkdir()
    (repo / ".conduct" / "irreversible-allow.txt").write_text(
        "# the deploy script really does rebuild src/lib from the generator\nrm -rf src/lib\n",
        encoding="utf-8")
    _, out, _, rec = hook(repo, "rm -rf src/lib")
    assert out is None and rec["verdict"] == "ok"


def test_the_allowlist_does_not_silence_everything(repo: Path) -> None:
    (repo / ".conduct").mkdir()
    (repo / ".conduct" / "irreversible-allow.txt").write_text("rm -rf src/lib\n", encoding="utf-8")
    _, out, _, _ = hook(repo, "git reset --hard")
    assert "[git-destructive]" in warning(out)


# --- fail-open and modes -----------------------------------------------------

def test_other_tools_are_ignored_without_a_receipt(repo: Path) -> None:
    rc, out, _, rec = hook(repo, "rm -rf src/lib", tool="Edit")
    assert rc == 0 and out is None and rec is None


def test_an_empty_command_is_ignored(repo: Path) -> None:
    rc, out, _, rec = hook(repo, "   ")
    assert rc == 0 and out is None and rec is None


def test_a_missing_gate_fails_open_with_a_receipt(repo: Path) -> None:
    rc, out, _, rec = hook(repo, "rm -rf src/lib", env={"IRREVERSIBLE_GATE": str(repo / "none.py")})
    assert rc == 0 and out is None and rec["verdict"] == "error"


def test_block_mode_exits_2_with_the_text_on_stderr(repo: Path) -> None:
    rc, out, err, rec = hook(repo, "rm -rf src/lib", env={"IRREVERSIBLE_HOOK_MODE": "block"})
    assert rc == 2 and out is None and "Block mode" in err and rec["mode"] == "block"


def test_receipts_can_be_switched_off(repo: Path) -> None:
    rc, out, _, _ = hook(repo, "rm -rf src/lib", env={"IRREVERSIBLE_RECEIPTS": "off"})
    assert rc == 0 and warning(out)
    assert not (repo / "receipts.jsonl").exists()


def test_the_receipt_keeps_the_command_short_and_never_its_output(repo: Path) -> None:
    _, _, _, rec = hook(repo, "rm -rf src/lib " + "x" * 500)
    assert len(rec["command"]) == 120 and "ms" in rec and "session" in rec


def test_an_operand_the_filesystem_cannot_hold_does_not_hide_the_tracked_one(repo: Path) -> None:
    """Found in CI, not locally: on Python 3.12 `Path.exists()` raises ENAMETOOLONG for a
    500-character name, the gate broke mid-judgement, and the hook recorded `error` while
    saying nothing about `rm -rf src/lib` sitting right beside the junk operand. A name the
    filesystem rejects is not a reason to stop judging the others."""
    _, out, _, rec = hook(repo, "rm -rf src/lib " + "x" * 500)
    assert "[rm-rf]" in warning(out) and "tracked in git" in warning(out), rec
    assert rec["verdict"] == "finding"


def test_an_error_receipt_still_names_the_command(repo: Path) -> None:
    """Fail-open is not the same as fail-silent: the receipt of a hook that broke must say
    on which command it broke, or the failure cannot be counted against anything."""
    _, out, _, rec = hook(repo, "rm -rf src/lib", env={"IRREVERSIBLE_GATE": str(repo / "none.py")})
    assert out is None and rec["verdict"] == "error"
    assert rec["command"] == "rm -rf src/lib" and rec["session"] == "test-ses"
