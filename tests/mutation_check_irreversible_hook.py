#!/usr/bin/env python3
"""Prove the live hook's tests actually defend it.

    python3 tests/mutation_check_irreversible_hook.py

For each mechanism in hooks/irreversible-before-run.py: neuter it in a scratch
COPY, run the hook's suite against the copy, and require the suite to go RED.
The real hook is never rewritten; its bytes are compared before and after
anyway. The gate's own mechanisms are defended by tests/mutation_check.py; this
runner covers only what the hook adds on top of the gate.

Exit 0 when every mutant was killed; 1 when any survived; 2 when this script
itself could not run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "irreversible-before-run.py"
SUITE = ROOT / "tests" / "test_irreversible_hook.py"

MUTANTS = [
    ("TOOLS every tool is judged, not only Bash",
     'TOOLS = {"Bash"}\n# mutation-anchor: TOOLS', 'TOOLS = {"Bash", "Edit"}\n# mutation-anchor: TOOLS'),
    ("HEREDOC a heredoc written to a file is judged as a command",
     "        if m and WRITES_FILE.search(line[:m.start()] + line[m.end():]):",
     "        if False:"),
    ("PROSE comment lines are judged",
     "    entries = ro.drop_doc_context(entries)", "    pass"),
    ("ALLOWLIST the working directory's allowlist is ignored",
     "    return ro.apply_allowlist(findings, allow), len(entries)", "    return findings, len(entries)"),
    ("VARIABLES an assignment in the same command is not resolved",
     "    lines = resolve_variables(command_lines(command))", "    lines = command_lines(command)"),
    ("CD a leading cd into scratch does not make relative targets scratch",
     "    findings = drop_relative_inside_scratch(findings, where != root and is_scratch(ro, where))",
     "    pass"),
    ("CD a repository under the temp tree is treated as scratch",
     "    return probe.returncode != 0", "    return True"),
    ("WARNING a finding produces no text",
     '    if not findings:\n        receipt(verdict="ok", lines=judged, ms=ms, **common)\n        return 0',
     '    if True:\n        receipt(verdict="ok", lines=judged, ms=ms, **common)\n        return 0'),
]


def run_suite(hook: Path) -> bool:
    env = {**os.environ, "IRREVERSIBLE_HOOK_UNDER_TEST": str(hook),
           "IRREVERSIBLE_GATE": str(ROOT / "gate" / "irreversible.py")}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(SUITE), "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=ROOT, env=env, check=False)
    return result.returncode == 0


def main() -> int:
    original = HOOK.read_text(encoding="utf-8")
    if not run_suite(HOOK):
        print("the suite is RED before any mutation — fix that first", file=sys.stderr)
        return 2
    survivors: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        mutant = Path(scratch) / "irreversible-before-run.py"
        for name, old, new in MUTANTS:
            if original.count(old) != 1:
                print(f"  ?? {name}: anchor appears {original.count(old)} times — the "
                      f"mutation list is stale, so this script is measuring nothing")
                survivors.append(f"{name} (stale)")
                continue
            mutant.write_text(original.replace(old, new, 1), encoding="utf-8")
            if run_suite(mutant):
                print(f"  SURVIVED  {name} — removed it and the suite stayed green")
                survivors.append(name)
            else:
                print(f"  killed    {name}")
    if HOOK.read_text(encoding="utf-8") != original:
        print("the real hook file changed during the run — it must never be touched",
              file=sys.stderr)
        return 2
    if survivors:
        print(f"\n{len(survivors)} mutant(s) survived: {', '.join(survivors)}")
        return 1
    print(f"\nall {len(MUTANTS)} mutants killed; the real hook was never rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
