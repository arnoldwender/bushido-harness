#!/usr/bin/env python3
"""The Bushido Harness gate: the blade is drawn last.

    python3 gate/irreversible.py                       # diff against origin/main
    python3 gate/irreversible.py --base HEAD           # diff against another ref
    python3 gate/irreversible.py --script deploy.sh    # inspect one script, whole file
    python3 gate/irreversible.py --sarif out.json

Exit codes are the contract shared by the conduct-harness family:

    0   no findings
    1   findings - the change reaches for something it cannot take back
    2   the gate itself failed

The third one is not decoration. A checker that returns 1 when it crashed reads
as "I found something"; one that returns 0 reads as "clean" and fails OPEN. This
gate distinguishes its own failure from its verdict, and when it cannot resolve
the base ref it says so and exits 2 rather than reporting an empty diff as clean.

WHAT THIS GATE IS FOR
---------------------
Gi 義 rule 2 - minimum force: reach for the reversible before the irreversible;
the blade is drawn only when nothing else serves. That rule is the one a codex
cannot enforce by being read, because the destructive command always arrives
disguised as the efficient one. So it is enforced here, on the diff.

The gate reads added lines. It never runs any of the commands it looks for.

THE PART THAT DECIDES WHETHER ANYONE KEEPS IT
---------------------------------------------
A verb regex is not a guardrail. An agent clearing its own scratch directory,
a build script clearing dist/, a CI job clearing node_modules - all of them
write the same three characters as the command that eats a repository. A gate
that fires on those gets switched off in a week, and deserves to be, which is
the failure mode this file is built around.

So the target path decides, not the verb:

    ephemeral   node_modules, dist, build, target, .cache, .venv, tmp, *.log,
                anything under /tmp, a fresh $(mktemp -d)   -> no finding
    unexpanded  a variable the gate cannot resolve, so it cannot know what is
                behind it                                   -> finding, warning
    tracked     the path is in git, or inside the repo, or is simply not
                provably disposable                         -> finding, error

The same discipline in SQL: a DELETE FROM with a WHERE clause is a statement
about some rows; without one it is a statement about all of them. Only the
second fires. `terraform plan -destroy` prints a plan; `terraform destroy`
executes one. Only the second fires.

WHAT IT DOES NOT SEE - stated plainly, because Makoto is not traded
-------------------------------------------------------------------
* Documentation is skipped whole: .md, .rst, .txt, .adoc. A README that shows
  a destructive command is teaching, not running.
* Inside code, comment lines and triple-quoted / block-comment regions are
  skipped. An arbitrary string literal in the middle of a code line is NOT -
  `subprocess.run("rm -rf " + path)` fires, and `HELP = "run rm -rf dist"`
  would too if the path were not ephemeral. Use the allowlist for those.
* `rm -r` without `-f` does not fire. The rule named the `rm -rf` family and
  the scope is the scope.
* A path is judged by name, not by what it holds. A tracked directory that
  someone named `build/` reads as ephemeral here.
* Only the added side of the diff. A destructive command that was already in
  the file is somebody else's finding, from the day it landed.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# The root is overridable so the tests can point the gate at a scratch repo with
# a defect planted in it. A checker that can only ever run on itself cannot be
# shown to work.
ROOT = Path(os.environ.get("HARNESS_ROOT") or Path(__file__).resolve().parent.parent)

DEFAULT_BASE = "origin/main"
ALLOWLIST_PATH = ".conduct/irreversible-allow.txt"

# Prose is documentation wherever it lives; a destructive command inside it is
# an example, not an instruction to a shell.
DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".adoc", ".asciidoc", ".txt"})

# A path segment from this set means the target is rebuildable from source. The
# list is deliberately conservative: every name on it is a directory whose whole
# purpose is to be deletable.
EPHEMERAL_SEGMENTS = frozenset({
    "node_modules", "bower_components", "vendor-bundle",
    "dist", "build", "target", "coverage", ".nyc_output",
    "tmp", "temp", ".tmp", ".temp", "scratch", ".scratch",
    ".cache", ".caches", ".parcel-cache", ".turbo", ".gradle", ".terraform",
    ".venv", "venv", ".virtualenv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".eggs", ".phpunit.cache",
    ".next", ".nuxt", ".svelte-kit", ".astro", ".output", ".vercel", ".netlify",
    ".serverless", ".sass-cache", "DerivedData", "logs",
    # `out`: the static export of Next.js and the build tree of IntelliJ and javac.
    # Measured over 27,000 real shell commands before it was added: 14 of the gate's
    # findings were `rm -rf apps/frontend/out && … build`, every one a rebuild.
    "out",
})

# A shell redirection is not an operand. `rm -rf dist 2>/dev/null` names one target,
# not two — and the gate once reported `2>/dev/null` as a path it could not prove
# disposable (16 of 182 findings over the same 27,000 commands).
REDIRECTION = re.compile(r"^(\d*>>?|&>|<<?)")
EPHEMERAL_ABS_PREFIXES = ("/tmp/", "/var/tmp/", "/private/tmp/",
                          "/var/folders/", "/private/var/folders/", "/dev/shm/")
EPHEMERAL_EXACT = frozenset({"/tmp", "/var/tmp", "/private/tmp", "/dev/shm"})
EPHEMERAL_GLOBS = ("*.log", "*.tmp", "*.pyc", "*.pyo", "*.o", "*.class", "*.swp")

# Command substitutions are replaced by these before tokenising, so that shlex
# does not split `$(mktemp -d)` into two tokens and lose the fact that it is one
# target. The distinction is worth keeping: mktemp hands back a directory that
# did not exist a moment ago, which is the one substitution provably disposable.
MKTEMP_TOKEN = "@@GATE_MKTEMP@@"
SUBST_TOKEN = "@@GATE_SUBST@@"

# Line-comment openers by file suffix. A line that is entirely a comment is
# prose. `--` is SQL/Lua only: elsewhere a line could plausibly start with a
# long flag.
LINE_COMMENTS: dict[str, tuple[str, ...]] = {
    ".sql": ("--", "#"), ".lua": ("--",),
    ".js": ("//",), ".mjs": ("//",), ".cjs": ("//",), ".jsx": ("//",),
    ".ts": ("//",), ".tsx": ("//",), ".go": ("//",), ".java": ("//",),
    ".c": ("//",), ".h": ("//",), ".cc": ("//",), ".cpp": ("//",),
    ".cs": ("//",), ".swift": ("//",), ".kt": ("//",), ".rs": ("//",),
    ".scala": ("//",), ".php": ("//", "#"), ".tf": ("#", "//"),
    ".css": (), ".scss": ("//",), ".ini": ("#", ";"), ".cfg": ("#", ";"),
}
DEFAULT_LINE_COMMENTS = ("#",)

# Suffixes whose /* ... */ regions are comments.
BLOCK_COMMENT_SUFFIXES = frozenset({
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".java", ".c", ".h",
    ".cc", ".cpp", ".cs", ".swift", ".kt", ".rs", ".scala", ".php", ".css",
    ".scss", ".less",
})

GIT_HOOK_NAMES = frozenset({
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit",
    "pre-merge-commit", "prepare-commit-msg", "commit-msg", "post-commit",
    "pre-rebase", "post-checkout", "post-merge", "pre-push", "pre-receive",
    "update", "post-receive", "post-update", "push-to-checkout", "pre-auto-gc",
})
REMOVAL_VERBS = frozenset({"rm", "unlink", "shred", "mv", "truncate"})


class GateFailure(Exception):
    """The gate could not judge. Always exit 2, never 1 and never 0."""


@dataclass
class Finding:
    check: str
    message: str
    path: str = ""
    line: int = 0
    level: str = "error"
    text: str = ""


@dataclass
class Entry:
    """One line the gate is allowed to judge: added by the diff, or from a
    --script. `statement` carries the following lines up to the first `;`, so a
    multi-line SQL statement is read as one statement and not as one line.

    `explicit` marks a line that came from a file the caller named. The gate
    filters what it DISCOVERS; it never filters away what it was handed.
    """
    path: str
    line: int
    text: str
    statement: str = ""
    explicit: bool = False


# --- git ---------------------------------------------------------------------

def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, check=False)


@lru_cache(maxsize=None)
def is_tracked(path: str) -> bool:
    """True when git knows this path. Read-only: `ls-files` changes nothing."""
    if not path or path.startswith("/"):
        return False
    r = git("ls-files", "--error-unmatch", "--", path)
    if r.returncode == 0:
        return True
    # A directory is not itself a tracked entry; ask whether it holds any.
    r = git("ls-files", "--", path)
    return r.returncode == 0 and bool(r.stdout.strip())


# --- scanning ----------------------------------------------------------------

def path_exists(p: Path) -> bool:
    """`Path.exists()` on Python 3.12 and older raises OSError for a name the filesystem
    cannot hold (ENAMETOOLONG, errno 36); 3.13 returns False. A gate that raises inside a
    judgement exits 2 and reports nothing — measured in CI on `rm -rf src/lib <500 chars>`:
    the tracked path went unreported because the junk operand beside it broke the probe.
    A name the filesystem rejects is not a path that exists; it is not a reason to stop."""
    try:
        os.stat(p)
        return True
    except OSError:
        return False


def rel_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except (ValueError, OSError):
        return str(path)


def scan_diff(base: str) -> list[Entry]:
    """Added lines between `base` and the working tree.

    The working tree is the right side on purpose: the gate is meant to catch a
    command before it is committed, not to audit history after the fact. It also
    means the `+` line numbers address the file as it sits on disk, which is
    what `doc_line_numbers` reads.
    """
    probe = git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    if probe.returncode != 0:
        detail = (probe.stderr or "").strip().splitlines()
        raise GateFailure(
            f"cannot resolve base ref {base!r}"
            + (f": {detail[0]}" if detail else "")
            + " - the gate cannot diff against a ref it cannot find, and an "
              "empty diff would read as 'clean'. Pass --base with a ref that "
              "exists, or fetch it first.")

    r = git("-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff",
            "--unified=0", base, "--")
    if r.returncode != 0:
        raise GateFailure(f"git diff against {base!r} failed: "
                          f"{(r.stderr or '').strip()[:160]}")

    entries: list[Entry] = []
    path = ""
    lineno = 0
    for raw in r.stdout.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = "" if target == "/dev/null" else re.sub(r"^b/", "", target).strip('"')
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if m:
            lineno = int(m.group(1))
            continue
        if not path:
            continue
        if raw.startswith("+"):
            entries.append(Entry(path, lineno, raw[1:]))
            lineno += 1
        elif raw.startswith(" "):
            lineno += 1
        # A '-' line is a deletion: it is leaving, not arriving.
    return entries


def scan_script(spec: str) -> list[Entry]:
    """Every line of one file. Used for a script handed over directly."""
    p = Path(spec)
    if not p.is_absolute():
        here = Path.cwd() / p
        p = here if here.exists() else (ROOT / spec)
    if not p.is_file():
        raise GateFailure(f"--script {spec}: no such file")
    rel = rel_to_root(p)
    text = p.read_text(encoding="utf-8", errors="replace")
    return [Entry(rel, n, line, explicit=True)
            for n, line in enumerate(text.splitlines(), 1)]


def collect(base: str | None, scripts: list[str]) -> tuple[list[Entry], str | None]:
    """--script and --base compose. With neither, the default base applies."""
    entries: list[Entry] = []
    for spec in scripts:
        entries.extend(scan_script(spec))
    base_used = base if base is not None else (None if scripts else DEFAULT_BASE)
    if base_used:
        entries.extend(scan_diff(base_used))
    return entries, base_used


# --- context: what is prose and what is an instruction -----------------------

@lru_cache(maxsize=None)
def doc_line_numbers(path: str) -> frozenset[int]:
    """Line numbers in `path` that are comment or docstring, not instruction.

    Read from the file on disk rather than inferred from the diff: a diff that
    adds one line inside an existing docstring carries no evidence that it is
    inside one. When the file cannot be read the answer is the empty set, and
    the per-line comment test in `is_doc_line` still applies.
    """
    p = ROOT / path
    try:
        if not p.is_file():
            p = Path(path)
            if not p.is_file():
                return frozenset()
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return frozenset()

    suffix = Path(path).suffix.lower()
    doc: set[int] = set()

    # Python-style triple quotes. Counting delimiters per line is enough for
    # real source and does not pretend to be a parser.
    if suffix in (".py", ".pyi"):
        fence: str | None = None
        for n, line in enumerate(lines, 1):
            if fence:
                doc.add(n)
                if fence in line:
                    fence = None
                continue
            for q in ('"""', "'''"):
                if line.count(q) == 1:
                    fence = q
                    doc.add(n)
                    break
                if line.count(q) >= 2:
                    doc.add(n)
                    break

    if suffix in BLOCK_COMMENT_SUFFIXES:
        inside = False
        for n, line in enumerate(lines, 1):
            if inside:
                doc.add(n)
                if "*/" in line:
                    inside = False
                continue
            if "/*" in line:
                doc.add(n)
                if "*/" not in line.split("/*", 1)[1]:
                    inside = True

    # Fenced code blocks carry examples wherever they appear.
    fenced = False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            doc.add(n)
            continue
        if fenced:
            doc.add(n)

    return frozenset(doc)


def is_doc_line(entry: Entry) -> bool:
    suffix = Path(entry.path).suffix.lower()
    if suffix in DOC_SUFFIXES:
        return True
    stripped = entry.text.lstrip()
    if not stripped:
        return True
    openers = LINE_COMMENTS.get(suffix, DEFAULT_LINE_COMMENTS)
    if any(stripped.startswith(o) for o in openers):
        return True
    if stripped.startswith(("*", "<!--")):
        return True
    return entry.line in doc_line_numbers(entry.path)


def drop_doc_context(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if not is_doc_line(e)]


def drop_ephemeral_files(entries: list[Entry]) -> list[Entry]:
    """A file that lives inside a build output is itself build output.

    Vendored and generated trees are full of destructive commands nobody wrote
    and nobody reviews. Judging them produces noise and no decision.

    A file the caller named with --script is exempt, and the exemption is the
    whole reason this reads `e.explicit or`. Without it the gate answered
    `--script /tmp/deploy.sh` with "0 lines inspected, clean": the path lives
    under /tmp, so the file the caller explicitly asked about was filtered away
    as scratch. That is the fail-open the exit-code contract exists to prevent,
    and it was found by running the shipped gate on a real script rather than
    by reading the code. Discovery gets filtered. A request does not.
    """
    return [e for e in entries
            if e.explicit or classify_target(e.path)[0] != "ephemeral"]


def attach_statements(entries: list[Entry]) -> list[Entry]:
    """Give every entry the rest of its statement, so a WHERE clause on the
    next line still counts as a WHERE clause.

    Bounded twice, because an unbounded lookahead is a false negative waiting
    to happen: it stops at the first `;`, and it refuses to jump a gap. A WHERE
    belonging to some other statement two hunks below must not be read as this
    statement's predicate.
    """
    by_path: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        by_path.setdefault(e.path, []).append(i)
    for indices in by_path.values():
        for pos, idx in enumerate(indices):
            parts = [entries[idx].text]
            previous = entries[idx].line
            if ";" not in entries[idx].text:
                for nxt in indices[pos + 1: pos + 8]:
                    if entries[nxt].line - previous > 3:
                        break
                    previous = entries[nxt].line
                    parts.append(entries[nxt].text)
                    if ";" in entries[nxt].text:
                        break
            entries[idx].statement = " ".join(parts)
    return entries


# --- tokenising --------------------------------------------------------------

@lru_cache(maxsize=None)
def parse_commands(text: str) -> tuple[tuple[str, ...], ...]:
    """Split one source line into candidate commands and tokenise each.

    Command substitutions are collapsed to a placeholder first, so the splitter
    does not cut a line in half inside `$( ... && ... )` and shlex does not
    shred `$(mktemp -d)` into two tokens.
    """
    protected = re.sub(r"\$\((?:[^()]|\([^()]*\))*\)",
                       lambda m: MKTEMP_TOKEN if "mktemp" in m.group(0) else SUBST_TOKEN,
                       text)
    protected = re.sub(r"`[^`]*`",
                       lambda m: MKTEMP_TOKEN if "mktemp" in m.group(0) else SUBST_TOKEN,
                       protected)

    out: list[tuple[str, ...]] = []
    for segment in re.split(r"\|\||&&|[;|&\n]", protected):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, comments=False, posix=True)
        except ValueError:
            tokens = segment.split()
        if tokens:
            out.append(tuple(tokens))
    return tuple(out)


def basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def find_verb(tokens: tuple[str, ...], name: str) -> int:
    """Index of `name` anywhere in the token list, or -1.

    Anywhere, not position zero, because the destructive verb is usually behind
    a wrapper: `sudo rm -rf x`, `find . | xargs rm -rf`, `env FOO=1 terraform
    destroy`. A false hit on a bare mention costs nothing, since every check
    then requires the flags that make the command destructive.
    """
    for i, tok in enumerate(tokens):
        if basename(tok) == name:
            return i
    return -1


# `find -exec rm -rf {} +` hands the gate a placeholder and a terminator rather
# than a path. The terminators are noise; the placeholder is a real target the
# gate cannot resolve, which is what the unexpanded verdict is for.
FIND_TERMINATORS = frozenset({"+", ";", "\\;"})


def flags_and_operands(tokens: tuple[str, ...]) -> tuple[set[str], list[str]]:
    """Short flags exploded (-rf -> {r, f}), long flags kept whole."""
    flags: set[str] = set()
    operands: list[str] = []
    end_of_flags = False
    for tok in tokens:
        if tok == "--":
            end_of_flags = True
            continue
        if REDIRECTION.match(tok):
            continue
        if not end_of_flags and tok.startswith("--"):
            flags.add(tok.split("=", 1)[0])
        elif not end_of_flags and tok.startswith("-") and len(tok) > 1:
            flags.update(tok[1:])
        else:
            operands.append(tok)
    return flags, operands


# --- path classification: the part that decides ------------------------------

def classify_target(raw: str) -> tuple[str, str]:
    """Return (kind, human reason).

    kind is one of: ephemeral, unexpanded, tracked, inside, outside.
    Only `ephemeral` means "no finding".
    """
    p = raw.strip().strip('"\'')
    if not p:
        return "unexpanded", "the target is not visible on this line"
    if MKTEMP_TOKEN in p:
        return "ephemeral", "a directory mktemp created a moment ago"
    if SUBST_TOKEN in p or "$" in p or "`" in p or p == "{}":
        return "unexpanded", "the target is behind a variable this gate cannot resolve"

    q = p
    while q.endswith("/*"):
        q = q[:-2]
    while q.endswith("/") and len(q) > 1:
        q = q[:-1]
    if q in ("", ".", "./"):
        return "inside", "the repository root"
    if q == "/":
        return "outside", "the filesystem root"

    norm = q[2:] if q.startswith("./") else q
    segments = [s for s in norm.split("/") if s not in ("", ".")]
    if any(s in EPHEMERAL_SEGMENTS for s in segments):
        return "ephemeral", "a rebuildable directory"
    if segments and any(fnmatch.fnmatch(segments[-1], g) for g in EPHEMERAL_GLOBS):
        return "ephemeral", "a generated file"
    if norm in EPHEMERAL_EXACT or norm.startswith(EPHEMERAL_ABS_PREFIXES):
        return "ephemeral", "a system temporary directory"

    candidate = norm
    if norm.startswith("/"):
        try:
            candidate = str(Path(norm).resolve().relative_to(ROOT.resolve()))
        except (ValueError, OSError):
            return "outside", "outside the repository, and not provably disposable"
    if is_tracked(candidate):
        return "tracked", "tracked in git"
    if path_exists(ROOT / candidate):
        return "inside", "inside the repository"
    return "outside", "not a path this gate can prove is disposable"


def report_target(entry: Entry, findings: list[Finding], check: str,
                  command: str, target: str) -> None:
    """Turn one (command, target) pair into a finding, or into silence."""
    kind, why = classify_target(target)
    if kind == "ephemeral":
        return
    shown = target if target else "<no visible target>"
    if kind == "unexpanded":
        findings.append(Finding(
            "unexpanded-target",
            f"`{command}` on `{shown}` - {why}. The gate cannot tell a scratch "
            f"directory from a repository here; resolve the path, or name it in "
            f"{ALLOWLIST_PATH}.",
            entry.path, entry.line, "warning", entry.text))
        return
    findings.append(Finding(
        check,
        f"`{command}` on `{shown}` - {why}. Reversible before irreversible: "
        f"move it aside, or scope the command to something rebuildable.",
        entry.path, entry.line, "error", entry.text))


# --- the checks --------------------------------------------------------------

def check_rm_rf(entry: Entry, findings: list[Finding]) -> None:
    """Recursive forced removal. Both flags required, per the named rule."""
    for tokens in parse_commands(entry.text):
        i = find_verb(tokens, "rm")
        if i < 0:
            continue
        flags, operands = flags_and_operands(tokens[i + 1:])
        recursive = bool({"r", "R"} & flags) or "--recursive" in flags
        forced = "f" in flags or "--force" in flags
        if not (recursive and forced):
            continue
        targets = [o for o in operands if o not in FIND_TERMINATORS]
        if not targets:
            report_target(entry, findings, "rm-rf", "rm -rf", "")
            continue
        for target in targets:
            report_target(entry, findings, "rm-rf", "rm -rf", target)


def check_git_destructive(entry: Entry, findings: list[Finding]) -> None:
    """The four git operations that do not have an undo worth the name."""
    for tokens in parse_commands(entry.text):
        i = find_verb(tokens, "git")
        if i < 0:
            continue
        rest = list(tokens[i + 1:])
        # Step over git's own global options to reach the subcommand.
        while rest and (rest[0].startswith("-")):
            opt = rest.pop(0)
            if opt in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and rest:
                rest.pop(0)
        if not rest:
            continue
        sub, args = rest[0], tuple(rest[1:])
        flags, operands = flags_and_operands(args)

        if sub == "reset" and "--hard" in flags:
            findings.append(Finding(
                "git-destructive",
                "`git reset --hard` discards every uncommitted change in the "
                "working tree, and nothing in git remembers them. Stash or "
                "commit first, or use `git restore` on the paths you mean.",
                entry.path, entry.line, "error", entry.text))

        if sub == "clean" and ("f" in flags or "--force" in flags):
            if not operands:
                findings.append(Finding(
                    "git-destructive",
                    "`git clean` with no path operand deletes untracked files "
                    "across the whole tree - including the ones not yet added "
                    "on purpose. Scope it to a rebuildable path.",
                    entry.path, entry.line, "error", entry.text))
            else:
                for operand in operands:
                    report_target(entry, findings, "git-destructive",
                                  "git clean -fd", operand)

        if sub == "push" and ("--force" in flags or "f" in flags):
            findings.append(Finding(
                "git-destructive",
                "`git push --force` overwrites the remote branch and drops "
                "whatever someone else pushed in between. "
                "`--force-with-lease` refuses instead of overwriting.",
                entry.path, entry.line, "error", entry.text))

        if sub in ("filter-repo", "filter-branch") and "--dry-run" not in flags:
            findings.append(Finding(
                "git-destructive",
                f"`git {sub}` rewrites every commit it touches; the old hashes "
                f"are gone and every clone diverges. Run it with --dry-run "
                f"first and keep a mirror of the original refs.",
                entry.path, entry.line, "error", entry.text))


SQL_DROP = re.compile(r"(?<![\w.])DROP\s+(TABLE|DATABASE|SCHEMA)\b", re.I)
SQL_TRUNCATE = re.compile(r"(?<![\w.])TRUNCATE\s+(?:TABLE\s+)?[A-Za-z_\"`\[]", re.I)
SQL_DELETE = re.compile(r"(?<![\w.])DELETE\s+FROM\s+([\w.\"`\[\]]+)", re.I)
SQL_WHERE = re.compile(r"(?<![\w.])WHERE\b", re.I)


def check_sql_destructive(entry: Entry, findings: list[Finding]) -> None:
    """DROP and TRUNCATE always; DELETE FROM only when it names no rows."""
    m = SQL_DROP.search(entry.text)
    if m:
        findings.append(Finding(
            "sql-destructive",
            f"`DROP {m.group(1).upper()}` destroys the object and everything in "
            f"it; a migration can add a column back but not the rows. Ship the "
            f"rename-then-drop pair, or take a dump the same transaction.",
            entry.path, entry.line, "error", entry.text))
    if SQL_TRUNCATE.search(entry.text):
        findings.append(Finding(
            "sql-destructive",
            "`TRUNCATE` empties the table without a row-by-row log; most "
            "engines will not roll it back and none will tell you what was in "
            "it. `DELETE FROM ... WHERE` is the reversible sibling.",
            entry.path, entry.line, "error", entry.text))
    m = SQL_DELETE.search(entry.text)
    if m and not SQL_WHERE.search(entry.statement or entry.text):
        findings.append(Finding(
            "sql-destructive",
            f"`DELETE FROM {m.group(1)}` with no WHERE clause is a statement "
            f"about every row in the table. If that is the intent, say so with "
            f"an explicit predicate.",
            entry.path, entry.line, "error", entry.text))


def check_cloud_destructive(entry: Entry, findings: list[Finding]) -> None:
    """Infrastructure that deletes faster than it provisions."""
    for tokens in parse_commands(entry.text):
        flags, _ = flags_and_operands(tokens)

        i = find_verb(tokens, "aws")
        if i >= 0:
            tail = tokens[i + 1:]
            words = [t for t in tail if not t.startswith("-")]
            if words[:2] == ["s3", "rm"] and "--recursive" in flags:
                findings.append(Finding(
                    "cloud-destructive",
                    "`aws s3 rm --recursive` deletes every key under the prefix, "
                    "and without versioning enabled the objects do not come "
                    "back. Confirm the prefix with `aws s3 ls` first.",
                    entry.path, entry.line, "error", entry.text))

        i = find_verb(tokens, "terraform")
        if i >= 0:
            words = [t for t in tokens[i + 1:] if not t.startswith("-")]
            sub = words[0] if words else ""
            if sub == "destroy" or (sub == "apply" and "-destroy" in tokens):
                findings.append(Finding(
                    "cloud-destructive",
                    "`terraform destroy` tears down real infrastructure, "
                    "including the stateful resources that are not in code. "
                    "`terraform plan -destroy` shows the same list and removes "
                    "nothing.",
                    entry.path, entry.line, "error", entry.text))

        i = find_verb(tokens, "kubectl")
        if i >= 0:
            words = [t for t in tokens[i + 1:] if not t.startswith("-")]
            dry = any(t.startswith("--dry-run") for t in tokens)
            if words[:1] == ["delete"] and not dry:
                findings.append(Finding(
                    "cloud-destructive",
                    "`kubectl delete` with no --dry-run removes the live object; "
                    "a PersistentVolumeClaim taken with it does not come back "
                    "with the manifest. Run `--dry-run=client` and read the list.",
                    entry.path, entry.line, "error", entry.text))

        i = find_verb(tokens, "docker")
        if i >= 0:
            words = [t for t in tokens[i + 1:] if not t.startswith("-")]
            if words[:2] == ["system", "prune"] and ("a" in flags or "--all" in flags):
                findings.append(Finding(
                    "cloud-destructive",
                    "`docker system prune -a` removes every image not attached "
                    "to a running container, including the ones no registry "
                    "still serves. Without -a it spares them.",
                    entry.path, entry.line, "error", entry.text))


HOOK_PATH = re.compile(r"(?:^|/)hooks/([\w-]+)$")


def targets_a_git_hook(operand: str) -> bool:
    cleaned = operand.strip("\"'").rstrip("*")
    if ".git/hooks" in cleaned:
        return True
    m = HOOK_PATH.search(cleaned)
    return bool(m and m.group(1) in GIT_HOOK_NAMES)


def check_hook_removal(entry: Entry, findings: list[Finding]) -> None:
    """Deleting a git hook is the quiet way to disable every other gate.

    `chmod` counts alongside the removal verbs: a hook stripped of its execute
    bit is as absent as one that was deleted, and it is the workaround anyone
    reaches for second.
    """
    for tokens in parse_commands(entry.text):
        verb = next((basename(t) for t in tokens
                     if basename(t) in REMOVAL_VERBS or basename(t) == "chmod"), "")
        if not verb:
            continue
        flags, operands = flags_and_operands(tokens)
        if verb == "chmod" and not ("x" in flags or any(
                mode.replace("+", "-").endswith("-x") or mode in ("000", "0000", "644")
                for mode in operands)):
            continue
        for operand in operands:
            if targets_a_git_hook(operand):
                findings.append(Finding(
                    "git-hook-removal",
                    f"`{verb}` on `{operand}` takes out a git hook. Every other "
                    f"gate in a repo runs from one; removing it makes the next "
                    f"failure silent instead of loud. Use the documented "
                    f"per-commit escape rather than deleting the hook.",
                    entry.path, entry.line, "error", entry.text))
                break


def run_checks(entries: list[Entry], findings: list[Finding]) -> None:
    for entry in entries:
        check_rm_rf(entry, findings)
        check_git_destructive(entry, findings)
        check_sql_destructive(entry, findings)
        check_cloud_destructive(entry, findings)
        check_hook_removal(entry, findings)


# --- allowlist ---------------------------------------------------------------

def load_allowlist(findings: list[Finding]) -> list[str]:
    """One path or regex per line; `#` comments.

    A gate with no escape hatch gets disabled wholesale the first time it is
    wrong, which trades one false positive for every true one.
    """
    p = ROOT / ALLOWLIST_PATH
    if not p.is_file():
        return []
    out: list[str] = []
    for n, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            re.compile(line)
        except re.error as exc:
            findings.append(Finding(
                "bad-allowlist-entry",
                f"line {n} is neither a usable path nor a valid regex ({exc}); "
                f"it silences nothing and hides the intent of whoever wrote it",
                ALLOWLIST_PATH, n, "error"))
            continue
        out.append(line)
    return out


def apply_allowlist(findings: list[Finding], allow: list[str]) -> list[Finding]:
    kept: list[Finding] = []
    for f in findings:
        if f.check == "bad-allowlist-entry":
            kept.append(f)
            continue
        if any(entry_matches(rule, f) for rule in allow):
            continue
        kept.append(f)
    return kept


def entry_matches(rule: str, finding: Finding) -> bool:
    if finding.path and (finding.path == rule or finding.path.startswith(rule.rstrip("/") + "/")):
        return True
    try:
        pattern = re.compile(rule)
    except re.error:
        return False
    return bool(pattern.search(finding.path) or (finding.text and pattern.search(finding.text)))


# --- output ------------------------------------------------------------------

def to_sarif(findings: list[Finding]) -> dict[str, Any]:
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "bushido-harness-irreversible",
                "informationUri": "https://github.com/arnoldwender/bushido-harness",
                "rules": [{"id": r} for r in sorted({f.check for f in findings})],
            }},
            "results": [{
                "ruleId": f.check,
                "level": f.level,
                "message": {"text": f.message},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": f.path or "."},
                    "region": {"startLine": max(f.line, 1)},
                }}],
            } for f in findings],
        }],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Find the irreversible command in a change before it runs.")
    ap.add_argument("--base", metavar="REF", default=None,
                    help=f"diff against this ref (default {DEFAULT_BASE}; "
                         f"skipped when --script is given alone)")
    ap.add_argument("--script", metavar="PATH", action="append", default=[],
                    help="inspect this file in full; repeatable")
    ap.add_argument("--sarif", metavar="PATH", help="write SARIF 2.1.0 to PATH")
    args = ap.parse_args(argv)

    findings: list[Finding] = []
    entries: list[Entry] = []
    requested: list[str] = []
    base_used: str | None = None
    try:
        entries, base_used = collect(args.base, args.script)
        requested = sorted({e.path for e in entries if e.explicit})
        entries = drop_doc_context(entries)
        entries = drop_ephemeral_files(entries)
        entries = attach_statements(entries)
        run_checks(entries, findings)
        allow = load_allowlist(findings)
        findings = apply_allowlist(findings, allow)
    except GateFailure as exc:
        print(f"gate failure: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                            # noqa: BLE001
        # Exit 2, never 1 and never 0: the gate broke, it did not judge.
        print(f"gate failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.sarif:
        Path(args.sarif).write_text(json.dumps(to_sarif(findings), indent=2),
                                    encoding="utf-8")

    scope = f"base {base_used}" if base_used else "scripts only"
    files = len({e.path for e in entries})
    print(f"irreversible: {len(entries)} line(s) inspected across {files} file(s) [{scope}]")

    # "0 lines inspected" and "clean" print almost the same for a reader in a
    # hurry. When a file was named on the command line and nothing in it was
    # judged, say so instead of letting the verdict imply it was read.
    inspected = {e.path for e in entries}
    for path in requested:
        if path not in inspected:
            print(f"  note: {path} was handed over but holds no line to judge - "
                  f"every line reads as documentation or comment")

    for f in sorted(findings, key=lambda x: (x.path, x.line)):
        where = f"{f.path}:{f.line}" if f.line else (f.path or ".")
        print(f"  {f.level.upper():<7} [{f.check}] {where}: {f.message}")
    if findings:
        errors = sum(1 for f in findings if f.level == "error")
        print(f"\n{len(findings)} finding(s): {errors} error, "
              f"{len(findings) - errors} warning")
        return 1
    print("  the blade stays in the sheath: nothing here destroys what it "
          "cannot restore")
    return 0


if __name__ == "__main__":
    sys.exit(main())
