"""Tests for the irreversibility gate.

Every check gets the same treatment, and it is the treatment the gate's own
design turns on: plant the destructive command on a path that cannot be
rebuilt and require RED, then plant the identical command on a path that is
rebuildable and require GREEN. A gate that only ever proves the first half is
a verb regex, and a verb regex is the thing people switch off.

    python3 -m pytest tests/ -q

The gate is invoked as a subprocess rather than imported, because the exit code
is part of the contract the whole conduct-harness family shares (0 clean,
1 findings, 2 the gate itself broke). Importing would test the functions and
leave the contract untested.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "gate" / "irreversible.py"

# A baseline that commits cleanly and gives the tests every kind of path they
# need to talk about: tracked source, a vendored tree, a script, a migration.
BASELINE = {
    "deploy.sh": "#!/bin/sh\nset -eu\necho deploying\n",
    "migrate.sql": "CREATE TABLE users (id integer primary key);\n",
    "src/lib/app.py": "VERSION = 1\n\n\ndef main():\n    return VERSION\n",
    "node_modules/pkg/clean.sh": "#!/bin/sh\necho vendored\n",
    "docs/RUNBOOK.md": "# Runbook\n\nNothing here yet.\n",
    "legacy.sh": "#!/bin/sh\nrm -rf src/lib\n",
}


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HARNESS_ROOT": str(root)}
    return subprocess.run([sys.executable, str(GATE), *args],
                          capture_output=True, text=True, env=env,
                          cwd=str(root), check=False)


def add_line(root: Path, relpath: str, *lines: str) -> None:
    """Append to a tracked file, which is what makes it show up as an added
    line in `git diff HEAD`."""
    p = root / relpath
    p.write_text(p.read_text(encoding="utf-8") + "".join(f"{line}\n" for line in lines),
                 encoding="utf-8")


def allow(root: Path, *lines: str) -> None:
    d = root / ".conduct"
    d.mkdir(exist_ok=True)
    (d / "irreversible-allow.txt").write_text("".join(f"{line}\n" for line in lines),
                                              encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small git repo the gate passes cleanly."""
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


def gate(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(root, "--base", "HEAD", *args)


# --- the control -------------------------------------------------------------
# Without these two, every test below would still pass with a gate that failed
# on everything it was handed.

def test_untouched_repo_passes(repo: Path) -> None:
    r = gate(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the blade stays in the sheath" in r.stdout


def test_a_harmless_change_passes(repo: Path) -> None:
    add_line(repo, "deploy.sh", "cp dist/index.html /srv/www/index.html",
             "echo done")
    r = gate(repo)
    assert r.returncode == 0, r.stdout + r.stderr


# --- rm -rf ------------------------------------------------------------------

def test_rm_rf_on_a_tracked_path_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "rm -rf src/lib")
    r = gate(repo)
    assert r.returncode == 1, r.stdout
    assert "rm-rf" in r.stdout
    assert "tracked in git" in r.stdout


@pytest.mark.parametrize("target", [
    "node_modules",
    "dist/",
    "build",
    "target/",
    ".cache",
    ".venv",
    "coverage",
    "src/lib/__pycache__",
    "/tmp/agent-scratch",
    "/var/tmp/build-9",
    "logs/app.log",
    '"$(mktemp -d)"',
])
def test_rm_rf_on_a_rebuildable_path_is_silent(repo: Path, target: str) -> None:
    """The reason a guardrail survives contact with the agent it guards."""
    add_line(repo, "deploy.sh", f"rm -rf {target}")
    r = gate(repo)
    assert r.returncode == 0, f"fired on a rebuildable target:\n{r.stdout}"


def test_rm_rf_behind_an_unexpanded_variable_fires_as_a_warning(repo: Path) -> None:
    """The gate cannot know what is behind $BUILD_DIR, and saying nothing
    would be a guess dressed as an all-clear."""
    add_line(repo, "deploy.sh", 'rm -rf "$BUILD_DIR"')
    r = gate(repo)
    assert r.returncode == 1, r.stdout
    assert "unexpanded-target" in r.stdout
    assert "WARNING" in r.stdout


@pytest.mark.parametrize("command", [
    "rm -rf src/lib",
    "rm -fr src/lib",
    "rm -r -f src/lib",
    "rm --recursive --force src/lib",
    "rm -Rf src/lib",
    "sudo rm -rf src/lib",
    "cd /srv && rm -rf src/lib",
])
def test_every_spelling_of_the_same_command_fires(repo: Path, command: str) -> None:
    add_line(repo, "deploy.sh", command)
    assert gate(repo).returncode == 1, command


def test_rm_without_force_is_out_of_scope(repo: Path) -> None:
    """Documented limit, asserted so it stays a decision and not a surprise."""
    add_line(repo, "deploy.sh", "rm -r src/lib")
    assert gate(repo).returncode == 0


def test_rm_rf_inside_a_vendored_tree_is_silent(repo: Path) -> None:
    """A file that lives in build output is build output."""
    add_line(repo, "node_modules/pkg/clean.sh", "rm -rf src/lib")
    assert gate(repo).returncode == 0


# --- git ---------------------------------------------------------------------

def test_git_reset_hard_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "git reset --hard origin/main")
    r = gate(repo)
    assert r.returncode == 1
    assert "git-destructive" in r.stdout


def test_git_reset_soft_is_silent(repo: Path) -> None:
    add_line(repo, "deploy.sh", "git reset --soft HEAD~1")
    assert gate(repo).returncode == 0


def test_git_clean_with_no_path_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "git clean -fdx")
    r = gate(repo)
    assert r.returncode == 1
    assert "git-destructive" in r.stdout


def test_git_clean_scoped_to_a_rebuildable_path_is_silent(repo: Path) -> None:
    add_line(repo, "deploy.sh", "git clean -fdx node_modules dist")
    assert gate(repo).returncode == 0


def test_git_clean_scoped_to_a_tracked_path_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "git clean -fdx src")
    assert gate(repo).returncode == 1


def test_git_push_force_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "git push --force origin main")
    r = gate(repo)
    assert r.returncode == 1
    assert "git-destructive" in r.stdout


def test_git_push_force_with_lease_is_silent(repo: Path) -> None:
    """The whole point of the flag is that it refuses instead of overwriting."""
    add_line(repo, "deploy.sh", "git push --force-with-lease origin main")
    assert gate(repo).returncode == 0


def test_git_push_short_force_flag_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "git push -f origin main")
    assert gate(repo).returncode == 1


@pytest.mark.parametrize("sub", ["filter-repo", "filter-branch"])
def test_history_rewrites_fire(repo: Path, sub: str) -> None:
    add_line(repo, "deploy.sh", f"git {sub} --path secrets --invert-paths")
    r = gate(repo)
    assert r.returncode == 1
    assert "git-destructive" in r.stdout


def test_history_rewrite_dry_run_is_silent(repo: Path) -> None:
    add_line(repo, "deploy.sh", "git filter-repo --dry-run --path secrets")
    assert gate(repo).returncode == 0


def test_git_command_inside_a_vendored_tree_is_silent(repo: Path) -> None:
    add_line(repo, "node_modules/pkg/clean.sh", "git reset --hard origin/main")
    assert gate(repo).returncode == 0


# --- SQL ---------------------------------------------------------------------

@pytest.mark.parametrize("statement", [
    "DROP TABLE users;",
    "DROP DATABASE app;",
    "DROP SCHEMA public CASCADE;",
    "drop table if exists users;",
])
def test_drop_fires(repo: Path, statement: str) -> None:
    add_line(repo, "migrate.sql", statement)
    r = gate(repo)
    assert r.returncode == 1, statement
    assert "sql-destructive" in r.stdout


def test_truncate_fires(repo: Path) -> None:
    add_line(repo, "migrate.sql", "TRUNCATE TABLE sessions;")
    r = gate(repo)
    assert r.returncode == 1
    assert "sql-destructive" in r.stdout


def test_shell_truncate_is_not_mistaken_for_the_sql_one(repo: Path) -> None:
    add_line(repo, "deploy.sh", "truncate -s 0 logs/app.log")
    assert gate(repo).returncode == 0


def test_delete_without_a_where_clause_fires(repo: Path) -> None:
    add_line(repo, "migrate.sql", "DELETE FROM sessions;")
    r = gate(repo)
    assert r.returncode == 1
    assert "sql-destructive" in r.stdout


def test_delete_with_a_where_clause_is_silent(repo: Path) -> None:
    """A DELETE with a predicate is a statement about some rows. Without one it
    is a statement about all of them, and only that is irreversible in bulk."""
    add_line(repo, "migrate.sql", "DELETE FROM sessions WHERE id = 1;")
    assert gate(repo).returncode == 0


def test_a_where_clause_on_the_next_line_still_counts(repo: Path) -> None:
    add_line(repo, "migrate.sql", "DELETE FROM sessions", "  WHERE expires_at < now();")
    assert gate(repo).returncode == 0


def test_a_where_clause_two_hunks_away_does_not_launder_a_bulk_delete(repo: Path) -> None:
    """The lookahead that finds a WHERE on the next line must not reach across
    the file and borrow one from an unrelated statement."""
    p = repo / "migrate.sql"
    body = ["CREATE TABLE users (id integer primary key);"]
    body += [f"-- filler {i}" for i in range(12)]
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "filler")

    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = "DELETE FROM sessions"
    lines[11] = "SELECT 1 WHERE true;"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = gate(repo)
    assert r.returncode == 1, r.stdout
    assert "sql-destructive" in r.stdout


def test_sql_inside_a_vendored_tree_is_silent(repo: Path) -> None:
    p = repo / "node_modules" / "pkg" / "seed.sql"
    p.write_text("DROP TABLE users;\n", encoding="utf-8")
    git(repo, "add", "-A")
    assert gate(repo).returncode == 0


# --- cloud -------------------------------------------------------------------

def test_aws_s3_recursive_delete_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "aws s3 rm s3://assets/site --recursive")
    r = gate(repo)
    assert r.returncode == 1
    assert "cloud-destructive" in r.stdout


def test_aws_s3_single_key_delete_is_silent(repo: Path) -> None:
    add_line(repo, "deploy.sh", "aws s3 rm s3://assets/site/index.html")
    assert gate(repo).returncode == 0


def test_terraform_destroy_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "terraform destroy -auto-approve")
    r = gate(repo)
    assert r.returncode == 1
    assert "cloud-destructive" in r.stdout


def test_terraform_apply_destroy_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "terraform apply -destroy -auto-approve")
    assert gate(repo).returncode == 1


def test_terraform_plan_destroy_is_silent(repo: Path) -> None:
    """`plan -destroy` prints the same list and removes nothing. A gate that
    cannot tell them apart teaches people to ignore it."""
    add_line(repo, "deploy.sh", "terraform plan -destroy -out tfplan")
    assert gate(repo).returncode == 0


def test_kubectl_delete_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "kubectl delete pvc data-0 -n prod")
    r = gate(repo)
    assert r.returncode == 1
    assert "cloud-destructive" in r.stdout


def test_kubectl_delete_dry_run_is_silent(repo: Path) -> None:
    add_line(repo, "deploy.sh", "kubectl delete pvc data-0 -n prod --dry-run=client")
    assert gate(repo).returncode == 0


def test_docker_system_prune_all_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "docker system prune -a -f")
    r = gate(repo)
    assert r.returncode == 1
    assert "cloud-destructive" in r.stdout


def test_docker_system_prune_without_all_is_silent(repo: Path) -> None:
    add_line(repo, "deploy.sh", "docker system prune -f")
    assert gate(repo).returncode == 0


def test_cloud_command_inside_a_vendored_tree_is_silent(repo: Path) -> None:
    add_line(repo, "node_modules/pkg/clean.sh", "terraform destroy -auto-approve")
    assert gate(repo).returncode == 0


# --- git hooks ---------------------------------------------------------------

def test_removing_a_git_hook_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "rm -f .git/hooks/pre-commit")
    r = gate(repo)
    assert r.returncode == 1
    assert "git-hook-removal" in r.stdout


def test_disabling_a_git_hook_with_chmod_fires(repo: Path) -> None:
    add_line(repo, "deploy.sh", "chmod -x .git/hooks/pre-push")
    r = gate(repo)
    assert r.returncode == 1
    assert "git-hook-removal" in r.stdout


def test_installing_a_git_hook_is_silent(repo: Path) -> None:
    add_line(repo, "deploy.sh", "cp scripts/pre-commit .git/hooks/pre-commit")
    assert gate(repo).returncode == 0


def test_hook_removal_inside_a_vendored_tree_is_silent(repo: Path) -> None:
    add_line(repo, "node_modules/pkg/clean.sh", "rm -f .git/hooks/pre-commit")
    assert gate(repo).returncode == 0


# --- context: prose is not an instruction ------------------------------------

def test_command_inside_a_python_docstring_is_silent(repo: Path) -> None:
    add_line(repo, "src/lib/app.py",
             '"""Recovery note.',
             "",
             "To reset a broken checkout: rm -rf src/lib && git clone ...",
             '"""')
    r = gate(repo)
    assert r.returncode == 0, f"fired on a docstring:\n{r.stdout}"


def test_command_inside_a_markdown_code_block_is_silent(repo: Path) -> None:
    add_line(repo, "docs/RUNBOOK.md",
             "Reset the environment:", "", "```sh", "rm -rf src/lib",
             "git reset --hard origin/main", "DROP TABLE users;", "```")
    r = gate(repo)
    assert r.returncode == 0, f"fired on documentation:\n{r.stdout}"


def test_command_in_a_comment_line_is_silent(repo: Path) -> None:
    add_line(repo, "deploy.sh", "# never do this: rm -rf src/lib")
    assert gate(repo).returncode == 0


def test_sql_comment_is_silent(repo: Path) -> None:
    add_line(repo, "migrate.sql", "-- DROP TABLE users; kept for the record")
    assert gate(repo).returncode == 0


def test_deleted_lines_are_not_judged(repo: Path) -> None:
    """The gate reads the added side. Removing a destructive line is the fix,
    not the offence."""
    p = repo / "legacy.sh"
    p.write_text("#!/bin/sh\n", encoding="utf-8")
    r = gate(repo)
    assert r.returncode == 0, r.stdout


# --- allowlist ---------------------------------------------------------------

def test_allowlist_path_entry_suppresses_a_finding(repo: Path) -> None:
    add_line(repo, "deploy.sh", "rm -rf src/lib")
    assert gate(repo).returncode == 1
    allow(repo, "# provisioning script, reviewed", "deploy.sh")
    assert gate(repo).returncode == 0


def test_allowlist_regex_entry_suppresses_a_finding(repo: Path) -> None:
    add_line(repo, "deploy.sh", "rm -rf src/lib")
    allow(repo, r"rm -rf src/(lib|vendor)")
    assert gate(repo).returncode == 0


def test_allowlist_does_not_suppress_everything(repo: Path) -> None:
    """An allowlist that silences the whole gate is not an allowlist."""
    add_line(repo, "deploy.sh", "rm -rf src/lib")
    add_line(repo, "migrate.sql", "DROP TABLE users;")
    allow(repo, "deploy.sh")
    r = gate(repo)
    assert r.returncode == 1
    assert "migrate.sql" in r.stdout
    assert "deploy.sh" not in r.stdout


def test_an_unusable_allowlist_entry_is_reported(repo: Path) -> None:
    """It silences nothing, so the reader has to be told it silences nothing."""
    allow(repo, "rm -rf [src")
    r = gate(repo)
    assert r.returncode == 1
    assert "bad-allowlist-entry" in r.stdout


# --- the contract ------------------------------------------------------------

def test_an_unresolvable_base_is_exit_2_not_a_pass(repo: Path) -> None:
    """The rule the whole family shares: when the input is missing, the default
    is never the value that means 'all good'. There is no origin/main here."""
    r = run(repo)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "gate failure" in r.stderr


def test_a_directory_that_is_not_a_repo_is_exit_2(tmp_path: Path) -> None:
    r = run(tmp_path, "--base", "HEAD")
    assert r.returncode == 2, r.stdout + r.stderr


def test_a_missing_script_is_exit_2(repo: Path) -> None:
    r = run(repo, "--script", "no-such-file.sh")
    assert r.returncode == 2, r.stdout + r.stderr


def test_script_mode_needs_no_base_ref(tmp_path: Path) -> None:
    """A script can be judged on its own, outside any repository."""
    (tmp_path / "clean.sh").write_text("#!/bin/sh\nrm -rf src/lib\n", encoding="utf-8")
    r = run(tmp_path, "--script", "clean.sh")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "rm-rf" in r.stdout


def test_script_mode_reads_the_whole_file_not_just_a_diff(repo: Path) -> None:
    r = run(repo, "--script", "legacy.sh")
    assert r.returncode == 1, r.stdout
    assert "legacy.sh" in r.stdout


# --- SARIF -------------------------------------------------------------------

def test_sarif_is_written_and_well_formed(repo: Path, tmp_path: Path) -> None:
    add_line(repo, "deploy.sh", "rm -rf src/lib")
    out = tmp_path / "out.sarif"
    r = gate(repo, "--sarif", str(out))
    assert r.returncode == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["version"] == "2.1.0"
    results = doc["runs"][0]["results"]
    assert results, "SARIF carries no results for a failing run"
    assert results[0]["ruleId"] == "rm-rf"
    assert results[0]["level"] == "error"
    assert results[0]["locations"][0]["physicalLocation"]["region"]["startLine"] >= 1


def test_sarif_records_the_lower_severity_as_a_warning(repo: Path,
                                                       tmp_path: Path) -> None:
    add_line(repo, "deploy.sh", 'rm -rf "$BUILD_DIR"')
    out = tmp_path / "warn.sarif"
    assert gate(repo, "--sarif", str(out)).returncode == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["runs"][0]["results"][0]["level"] == "warning"
