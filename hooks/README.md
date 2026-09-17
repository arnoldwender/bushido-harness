# Hooks — keeping the virtues present

The Codex only works if it's *in context* when the agent acts. A one-time paste into
`AGENTS.md` works; a hook makes it automatic, every session, and opens each run with the
precept.

## `session-start.sh`

Emits, to stdout:

1. The **opening precept** + a rotating **precept of the day** (`bin/precept`, drawn from
   `precepts.txt`).
2. The **conduct block** — the four virtues, precedence, and the gate limit (`codex-block.md`).

It's harness-agnostic: any harness that can run a command at session start can use it, and
its stdout is plain readable text.

## Wiring it into Claude Code

Claude Code injects a `SessionStart` hook's stdout into the session context. Add to your
`settings.json` (use the **absolute** path, and check your Claude Code version's hook docs —
the schema evolves):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "/abs/path/to/bushido-harness/hooks/session-start.sh" }
        ]
      }
    ]
  }
}
```

## Wiring it into any other harness

Run `hooks/session-start.sh` as the first step of your session bootstrap and prepend its
output to the system prompt. The precept goes first, the virtues stay present.

## `irreversible-before-run.py` — the blade, checked before it is drawn

A Claude Code `PreToolUse` hook for `Bash`. Before the shell command runs, it hands that one
command to [`gate/irreversible.py`](../gate/irreversible.py) — the same five checks, the same
path classification, the same allowlist — and, if the command reaches for something it cannot
take back, tells the agent so in the tool result. It **warns**; it does not block:

> irreversible: this command reaches for something it cannot take back. [rm-rf] `rm -rf` on
> `src/lib` - tracked in git. Reversible before irreversible: move it aside, or scope the
> command to something rebuildable. Gi 義 · 2: … Warning mode: this command is NOT blocked.

Why a hook when the gate exists: the gate reads the added lines of a diff, so it catches the
destructive command an agent *writes into a file*. It never sees the one an agent *types* —
a `rm -rf` sent straight to the shell leaves no diff, and by the time anyone diffs anything
the directory is gone.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command",
            "command": "python3 /abs/path/to/bushido-harness/hooks/irreversible-before-run.py",
            "timeout": 10 }
        ]
      }
    ]
  }
}
```

The gate is imported with the session's working directory as its root, so *tracked in git*,
*inside the repository* and `.conduct/irreversible-allow.txt` all refer to the repository the
agent is working in — not to this one. Three things the hook resolves that a diff cannot,
each stated with its limit in the file's header: a variable assigned in the same command
(`T=$(mktemp -d); … rm -rf "$T"` is a scratch directory), a `cd` that opens the command
(relative paths are judged from there), and a heredoc written to a file (content, not a
command; a heredoc fed to an interpreter is judged like a script).

Every run leaves a receipt in `~/.local/state/bushido-harness/irreversible-receipts.jsonl`
(`IRREVERSIBLE_RECEIPTS=…` to move it, `off` to disable) — verdict, checks, the first 120
characters of the command, never its output. `IRREVERSIBLE_HOOK_MODE=block` makes it deny the
command instead (exit 2); shipped so the switch exists, not the default. Any error of its own
is a receipt and exit 0 — the hook is never the reason a session cannot proceed.

Tests: [`tests/test_irreversible_hook.py`](../tests/test_irreversible_hook.py) ·
mutants: [`tests/mutation_check_irreversible_hook.py`](../tests/mutation_check_irreversible_hook.py).

## Just want to see it?

```sh
./hooks/session-start.sh
```
