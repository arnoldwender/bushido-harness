#!/usr/bin/env python3
"""Prove the irreversibility gate's tests actually defend it.

    python3 tests/mutation_check.py

For each mechanism in gate/irreversible.py: remove it, run the suite, and
require the suite to go RED. A test that still passes with the mechanism gone
is not testing the mechanism - it is decoration that reports green forever.

The list covers both halves of the gate, because both halves can rot:

  * the five checks, which decide what counts as destructive;
  * the four discriminators - documentation, rebuildable file, rebuildable
    target, allowlist - and the two guards that keep `WHERE` and
    `--force-with-lease` from being read as their dangerous siblings.

The second group is the one worth mutating hardest. A gate that fires on a
scratch directory gets switched off, and a switched-off gate reports nothing
forever, which looks exactly like a clean repo.

Exit 0 when every mutant was killed; 1 when any survived; 2 when this script
itself could not run (same contract as the gate).

The file is restored from an in-memory copy in a `finally`, never with
`git checkout`: this repo may hold uncommitted work, and a checkout to undo a
mutation would take that work with it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "gate" / "irreversible.py"

# (name, the exact source text to neuter, what to put in its place)
MUTANTS = [
    ("CHECK rm-rf", "check_rm_rf(entry, findings)", "pass"),
    ("CHECK git-destructive", "check_git_destructive(entry, findings)", "pass"),
    ("CHECK sql-destructive", "check_sql_destructive(entry, findings)", "pass"),
    ("CHECK cloud-destructive", "check_cloud_destructive(entry, findings)", "pass"),
    ("CHECK git-hook-removal", "check_hook_removal(entry, findings)", "pass"),
    ("SCOPE documentation is not an instruction",
     "entries = drop_doc_context(entries)", "entries = entries"),
    ("SCOPE a file inside build output is build output",
     "entries = drop_ephemeral_files(entries)", "entries = entries"),
    ("SCOPE a file the caller named is never filtered away",
     'if e.explicit or classify_target(e.path)[0] != "ephemeral"',
     'if classify_target(e.path)[0] != "ephemeral"'),
    ("SCOPE a rebuildable target is not a finding",
     'if kind == "ephemeral":', "if False:"),
    ("GUARD a DELETE with a WHERE clause is not a bulk delete",
     "and not SQL_WHERE.search(entry.statement or entry.text)", "and True"),
    ("GUARD the statement lookahead does not jump a gap",
     "if entries[nxt].line - previous > 3:", "if False:"),
    ("GUARD --force-with-lease is not --force",
     'if sub == "push" and ("--force" in flags or "f" in flags):',
     'if sub == "push":'),
    ("ALLOWLIST", "findings = apply_allowlist(findings, allow)",
     "findings = findings"),
]


def run_suite() -> bool:
    """True when the suite is green."""
    r = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q",
                        "-x", "--no-header"],
                       capture_output=True, text=True, cwd=ROOT, check=False)
    return r.returncode == 0


def main() -> int:
    original = GATE.read_text(encoding="utf-8")

    if not run_suite():
        print("the suite is RED before any mutation - fix that first", file=sys.stderr)
        return 2

    survivors: list[str] = []
    try:
        for name, target, replacement in MUTANTS:
            if original.count(target) != 1:
                print(f"  ??        {name}: the mutation list is stale - "
                      f"{original.count(target)} matches for that source text")
                survivors.append(f"{name} (stale)")
                continue
            GATE.write_text(original.replace(target, replacement, 1), encoding="utf-8")
            if run_suite():
                print(f"  SURVIVED  {name} - removed it and the suite stayed green")
                survivors.append(name)
            else:
                print(f"  killed    {name}")
    finally:
        GATE.write_text(original, encoding="utf-8")

    # The restore itself is verified. A mutation runner that leaves the file
    # mutated has done more harm than the bug it was hunting.
    if GATE.read_text(encoding="utf-8") != original:
        print("gate file was NOT restored cleanly", file=sys.stderr)
        return 2

    if survivors:
        print(f"\n{len(survivors)} mutant(s) survived: {', '.join(survivors)}")
        return 1
    print(f"\nall {len(MUTANTS)} mutants killed; gate restored and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
