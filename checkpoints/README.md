# Checkpoint snapshots

⚠️ **Do not delete these directories yet.** Read this first.

These are pre-install rollback snapshots that were copied into the working tree
before this repository had useful Git history. Each holds the version of the
files an increment was about to overwrite.

| Directory | Holds | Represents |
| --- | --- | --- |
| `accepted-backup-81e837678814497aa9384c59dde2df54/` | `java_workflow.py`, `installed-files.json` | `java_workflow.py` **before** the `accepted-java-v1` workflow was installed. `installed-files.json` lists that increment's files. |
| `stream-rule-backup-75cee6743c604cdf82d16cbbf9da5c4d/` | `accepted_workflow.py`, `test_accepted_workflow.py` | Both files **before** the `javac-string-array-stream-loop-v1` compiler repair rule was added. |

## Why they were not replaced with Git tags

Git should be the recovery mechanism, and copied directories should not be. But
this repository currently contains **exactly one commit**
(`7e077ee`, "Initial NullBrain server backup"), which already includes both
increments. No commit represents either pre-install state, so:

- there is no historical commit to tag;
- tagging `HEAD` would point a "checkpoint" tag at the state *after* the
  increments it claims to precede, which would be misleading;
- deleting these directories would destroy the only record of those states.

So they were moved out of `src/nullcode/` (where they polluted the package) and
kept here, unchanged.

## How to retire them

Once the pre-install states are verifiably reachable from Git — for instance by
importing the original per-milestone archives as real commits and tagging them
`checkpoint-accepted-81e8376` and `checkpoint-stream-rule-75cee67` — verify that
`git show <tag>:<path>` reproduces each file byte-for-byte, then delete this
directory in a dedicated commit.

If that is not going to happen, the alternative is an explicit decision that the
pre-install states are no longer needed. Either way it is a deliberate call, not
a cleanup side effect.

## These files are not part of the build

They are not importable, not collected by the test suite (`pyproject.toml` sets
`testpaths` and `norecursedirs`), and nothing imports them.
