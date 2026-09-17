#!/usr/bin/env python3
"""The Bushido Harness — the blade, checked before it is drawn.

A Claude Code `PreToolUse` hook for `Bash`. Before the shell command runs, it
hands that one command to the irreversibility gate (`gate/irreversible.py`) and,
if the command reaches for something it cannot take back — `rm -rf` on a tracked
path, `git reset --hard`, `git push --force`, `DROP TABLE`, `terraform destroy`,
a git hook removed — tells the agent so in the tool result. It warns; it does
not block. See hooks/README.md for the wiring and the README for why.

    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "python3 /abs/path/to/bushido-harness/hooks/irreversible-before-run.py",
         "timeout": 10}]}]}

WHY A HOOK AND NOT ONLY THE GATE
--------------------------------
The gate reads the added lines of a diff, so it catches the destructive command
an agent WRITES INTO A FILE. It never sees the one an agent TYPES: a `rm -rf`
sent straight to the shell leaves no diff, and by the time anyone diffs anything
the directory is gone. Gi 義 rule 2 — reach for the reversible before the
irreversible — is broken at the shell far more often than in a script, and the
shell is where an undo does not exist. This hook is the same gate, at the moment
the command is about to run, on the only line that matters.

WHAT IT DOES
------------
1. Reads the hook payload from stdin: `tool_name`, `tool_input.command`, `cwd`,
   `session_id`. Ignores every tool but `Bash`.
2. Imports the gate with `HARNESS_ROOT` set to the session's working directory,
   so "tracked in git", "inside the repository" and the allowlist are judged
   against the repository the agent is working in — not against this one.
3. Turns the command into the gate's own `Entry` lines (one per line of the
   command, `explicit=True`: the gate filters what it discovers, never what it
   is handed), drops comment lines, attaches multi-line SQL statements, and runs
   the gate's `run_checks` — the same five checks, the same path classification,
   the same allowlist (`.conduct/irreversible-allow.txt` in the working
   directory; a path, or a regex the command matches).
4. If anything fires: prints `{"hookSpecificOutput": {"hookEventName":
   "PreToolUse", "additionalContext": "..."}}` and exits 0. Claude Code adds
   that text to the agent's context alongside the tool result. The permission
   flow is not touched.
5. Appends one receipt line per run to `IRREVERSIBLE_RECEIPTS` (default
   `~/.local/state/bushido-harness/irreversible-receipts.jsonl`; `off` disables):
   `{ts, session, verdict, checks, ms}` and the command's first 120 characters.
   The receipts are how the false-positive rate gets measured on real sessions —
   the number this hook needs before anyone should let it block.

THREE THINGS THE GATE DOES NOT KNOW ABOUT A COMMAND, AND THE HOOK DOES
----------------------------------------------------------------------
Measured before this hook was published, over 27,132 real Bash calls from 80
sessions of one developer's agent: the gate as-is would have warned on 278
(1.0 %), and 233 of those were `unexpanded-target` — a variable the gate cannot
resolve. Reading the samples, most were resolvable from the command itself. So:

* A variable assigned IN THE SAME COMMAND is resolved before judging.
  `T=$(mktemp -d); … rm -rf "$T"` is a scratch directory, not a mystery; the
  gate sees `$T` and cannot know, the hook sees the assignment three lines up
  and can. Only plain assignments (`X=value`, quoted or not, one pass); a loop
  variable or anything set elsewhere stays unexpanded and keeps its warning —
  saying nothing would be a guess dressed as an all-clear.
* A LEADING `cd` sets the directory relative paths are judged from.
  `cd /tmp/probe && rm -rf r3f-probe` is judged under /tmp; the gate, reading a
  diff, has no cwd at all. Only a `cd` that opens the command counts; a `cd`
  in the middle is not tracked (stated so it stays a decision).
* A heredoc. `cat > notes.md <<'EOF' … rm -rf src … EOF` writes prose; the
  gate, reading lines, would see the command inside. A heredoc whose body is
  redirected into a file is treated as content and skipped; a heredoc fed to an
  interpreter (`bash <<'EOF'`, `psql <<SQL`, `python3 - <<'PY'`) is judged line
  by line like anything else — that is a script, delivered differently.

The gate's classification of a target is not touched: what is tracked, inside,
outside or rebuildable is still the gate's call. The hook only hands it what
the command already says.

WHAT IT DOES NOT SEE (inherited from the gate, stated so they stay decisions)
----------------------------------------------------------------------------
* `rm -r` without `-f`. The rule named the `rm -rf` family.
* A path judged by name: a tracked directory called `build/` reads as
  rebuildable. A relative path after a `cd` is judged from the session's
  working directory, not from where the `cd` went.
* A destructive command built at run time from variables: reported as
  `unexpanded-target`, a warning, because the hook cannot know what is behind
  `$DIR` — and saying nothing would be a guess dressed as an all-clear.
* Any tool but Bash. An `Edit` that writes `rm -rf` into a script is the gate's
  job, at diff time.

MODES AND FAIL-OPEN
-------------------
`IRREVERSIBLE_HOOK_MODE=warn` (default) injects the text and exits 0.
`IRREVERSIBLE_HOOK_MODE=block` writes it to stderr and exits 2, which Claude
Code treats as a denial. Block is shipped so the switch exists; it is not the
default, because a guard whose false-positive rate nobody has measured on real
sessions is switched off by the first person it wrongly stops.
Any error of the hook's own is a receipt with `verdict: error` and exit 0. The
hook is never the reason a session cannot proceed.

Tests: tests/test_irreversible_hook.py · mutants: tests/mutation_check_irreversible_hook.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
GATE = pathlib.Path(os.environ.get("IRREVERSIBLE_GATE") or HERE.parent / "gate" / "irreversible.py")


def _default_receipts() -> str:
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "bushido-harness", "irreversible-receipts.jsonl")


RECEIPTS = os.environ.get("IRREVERSIBLE_RECEIPTS") or _default_receipts()
MODE = os.environ.get("IRREVERSIBLE_HOOK_MODE", "warn")          # warn | block
TOOLS = {"Bash"}
# mutation-anchor: TOOLS
MAX_SHOWN = 4

HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# A heredoc whose body lands in a file, not in an interpreter: `cat > x <<EOF`,
# `tee x <<EOF`, `cat <<EOF > x`. The body is content the agent is writing.
WRITES_FILE = re.compile(r"(^|\s)(cat|tee)(\s|$)|(^|[^<>])>{1,2}\s*[^&\s]")
# `X=value` at the start of a line or after `;`, `&&`, `||`, `|`, or `export`.
ASSIGN = re.compile(r"(?:^|[;&|]\s*|\bexport\s+)([A-Za-z_][A-Za-z0-9_]*)="
                    r"(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|(\$\([^)]*\)|[^\s;&|]*))")
# A `cd` that opens the command: `cd /x && …`, `cd "/x y"; …`, `cd /x\n…`.
LEADING_CD = re.compile(r"^\s*cd\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|]+))\s*(?:&&|;|\n|$)")


VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def resolve_variables(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Substitute variables the command itself assigns, in order, one pass each.

    `$X` and `${X}` are replaced only where `X=` appeared EARLIER in this command — on a
    previous line, or earlier on the same line. A value is used as written: `$(mktemp -d)`
    stays literal, which is exactly what the gate reads as a fresh scratch directory."""
    known: dict[str, str] = {}

    def fill(text: str) -> str:
        return VAR.sub(lambda m: known.get(m.group(1) or m.group(2), m.group(0)), text) if known else text

    out: list[tuple[int, str]] = []
    for n, line in lines:
        pieces: list[str] = []
        pos = 0
        for m in ASSIGN.finditer(line):
            pieces.append(fill(line[pos:m.start()]))
            value = next((g for g in m.groups()[1:] if g is not None), "")
            if value:
                known[m.group(1)] = fill(value)
            pieces.append(line[m.start():m.end()])
            pos = m.end()
        pieces.append(fill(line[pos:]))
        out.append((n, "".join(pieces)))
    return out
# mutation-anchor: resolve_variables


def leading_cd(command: str, root: str) -> str:
    """The directory relative paths are judged from: a `cd` that opens the command, else root."""
    m = LEADING_CD.match(command)
    if not m:
        return root
    target = next(g for g in m.groups() if g is not None)
    if target.startswith("~"):
        target = os.path.expanduser(target)
    if not os.path.isabs(target):
        target = os.path.join(root, target)
    return target if os.path.isdir(target) else root
# mutation-anchor: leading_cd


TARGET_IN_MESSAGE = re.compile(r" on `([^`]*)` - ")


def is_scratch(ro, root: str) -> bool:
    """A directory the gate itself calls ephemeral (/tmp, /var/folders, a build dir) that is
    not a git repository: anything relative inside it is scratch too. A repository that
    happens to live under /tmp is a repository."""
    if ro.classify_target(root)[0] != "ephemeral":
        return False
    probe = subprocess.run(["git", "-C", root, "rev-parse", "--git-dir"],
                           capture_output=True, text=True, check=False)
    return probe.returncode != 0


def drop_relative_inside_scratch(findings, scratch: bool):
    """With a leading `cd` into a scratch directory, a relative target is scratch: dropped."""
    if not scratch:
        return findings
    kept = []
    for f in findings:
        m = TARGET_IN_MESSAGE.search(f.message)
        target = (m.group(1) if m else "").strip("\"'")
        if m and target and not target.startswith("/") and "$" not in target:
            continue
        kept.append(f)
    return kept
# mutation-anchor: drop_relative_inside_scratch


def receipt(**row: object) -> None:
    """One JSON line per run. The command's first 120 characters, never its output."""
    if RECEIPTS == "off":
        return
    try:
        pathlib.Path(RECEIPTS).parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}
        with open(RECEIPTS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — a receipt never brings the hook down
        pass


def load_gate(root: str):
    """Import the gate by path with `HARNESS_ROOT` = the session's working directory.
    The gate fixes its root at import time, and everything it decides — tracked, inside,
    outside, the allowlist — is relative to that root."""
    os.environ["HARNESS_ROOT"] = root
    spec = importlib.util.spec_from_file_location("bushido_irreversible_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                        # dataclasses resolve annotations here
    spec.loader.exec_module(mod)
    return mod


def command_lines(command: str) -> list[tuple[int, str]]:
    """The lines of the command the gate should judge, numbered as typed.

    A heredoc body that is redirected into a file is content and is skipped; a heredoc
    body fed to an interpreter is a script and is kept. Everything else is kept."""
    kept: list[tuple[int, str]] = []
    skipping_until: str | None = None
    for n, line in enumerate(command.split("\n"), 1):
        if skipping_until is not None:
            if line.strip() == skipping_until:
                skipping_until = None
            continue
        m = HEREDOC.search(line)
        if m and WRITES_FILE.search(line[:m.start()] + line[m.end():]):
            skipping_until = m.group(2)
        kept.append((n, line))
    return kept
# mutation-anchor: command_lines


def judge(command: str, root: str):
    """Findings the gate raises for ONE command, judged from `root`. Importable, so the
    false-positive rate can be measured over real sessions without spawning a process
    per command."""
    where = leading_cd(command, root)
    ro = load_gate(where)
    lines = resolve_variables(command_lines(command))
    entries = [ro.Entry("<command>", n, text, explicit=True) for n, text in lines]
    entries = ro.drop_doc_context(entries)
    entries = ro.attach_statements(entries)
    findings: list = []
    ro.run_checks(entries, findings)
    findings = drop_relative_inside_scratch(findings, where != root and is_scratch(ro, where))
    allow = ro.load_allowlist(findings)
    return ro.apply_allowlist(findings, allow), len(entries)


def message(findings) -> str:
    parts = []
    for f in findings[:MAX_SHOWN]:
        parts.append(f"[{f.check}] {f.message}")
    more = f" (+{len(findings) - MAX_SHOWN} more)" if len(findings) > MAX_SHOWN else ""
    return ("irreversible: this command reaches for something it cannot take back. "
            + " ".join(parts) + more
            + " Gi 義 · 2: reach for the reversible before the irreversible — the blade is "
            "drawn only when nothing else serves. If this is deliberate and reviewed, name "
            "the command in .conduct/irreversible-allow.txt. Warning mode: this command is "
            "NOT blocked.")


def main() -> int:
    t0 = time.time()
    payload = json.loads(sys.stdin.read() or "{}")
    if payload.get("tool_name") not in TOOLS:
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command.strip():
        return 0
    root = str(payload.get("cwd") or os.getcwd())
    session = str(payload.get("session_id") or "")[:8]
    common = {"session": session, "command": command[:120], "mode": MODE}

    try:
        findings, judged = judge(command, root)
    except Exception as exc:  # noqa: BLE001 — fail open, but the receipt still names the command
        receipt(verdict="error", error=f"{type(exc).__name__}: {exc}"[:200], **common)
        return 0
    ms = int((time.time() - t0) * 1000)
    if not findings:
        receipt(verdict="ok", lines=judged, ms=ms, **common)
        return 0
    receipt(verdict="finding", lines=judged, checks=sorted({f.check for f in findings}),
            levels=sorted({f.level for f in findings}), ms=ms, **common)
    text = message(findings)
    if MODE == "block":
        sys.stderr.write(text.replace("Warning mode: this command is NOT blocked.",
                                      "Block mode: this command was not run.") + "\n")
        return 2
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "additionalContext": text}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — fail open on purpose: the hook is never the blocker
        receipt(verdict="error", error=f"{type(exc).__name__}: {exc}"[:200])
        sys.exit(0)
