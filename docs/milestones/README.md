# Milestone documents

Detailed technical write-ups for each increment: what was implemented, why, how
it was tested, and which architectural choices were made. They are preserved as
historical record.

| Document | Increment |
| --- | --- |
| [MILESTONE-2.md](MILESTONE-2.md) | Automated Java verification and one repair |
| [MILESTONE-3.md](MILESTONE-3.md) | Repository-backed Java jobs |
| [MILESTONE-4.md](MILESTONE-4.md) | Targeted review before committing |
| [MILESTONE-5.md](MILESTONE-5.md) | Draft pull-request delivery |
| [MILESTONE-6.md](MILESTONE-6.md) | Configurable small Gradle/JUnit tasks |
| [MILESTONE-7A.md](MILESTONE-7A.md) | Read-only repository inspection and planning |
| [MILESTONE-7B.md](MILESTONE-7B.md) | Planned, bounded multi-file execution |
| [MILESTONE-7C-2.md](MILESTONE-7C-2.md) | Multi-file draft PR publishing |
| [MILESTONE-7B-HARDENING.md](MILESTONE-7B-HARDENING.md) | Behavioral-novelty validation |
| [MILESTONE-7B-1.md](MILESTONE-7B-1.md) | Behavioral-delta evidence hardening |

Milestone 1 is documented in [`rust/README.md`](../../rust/README.md).

## Proposals

Design documents for work that is **not implemented**. They record a proposed
shape and its open questions so the design can be argued about first; they are
not approvals and claim no verification.

| Document | Increment |
| --- | --- |
| [PROPOSAL-7C-1.md](PROPOSAL-7C-1.md) | Model-proposed scope, human-granted scope |

## ⚠️ Commands in these documents are historical

Each document records the install and exercise commands **as they were at the
time**, when the project lived flat in `/srv/nullbrain/src/nullcode` and modules
were run as scripts (`python3 java_workflow.py ...`). Those paths and commands
are intentionally left unchanged so the record stays accurate.

For commands that work against the current layout, see
[`../../README.md`](../../README.md) and
[`../PROJECT_STATE.md`](../PROJECT_STATE.md). The mapping is mechanical:

| Then | Now |
| --- | --- |
| `cd /srv/nullbrain/src/nullcode` | `cd /srv/nullbrain` |
| `python3 java_workflow.py <cmd>` | `python3 -m nullcode.core.java_workflow <cmd>` |
| `python3 publish_workflow.py …` | `python3 -m nullcode.publish.publish_workflow …` |
| `python3 create_fixture.py …` | `python3 -m nullcode.fixtures.create_fixture …` |
| `python3 create_gradle_fixture.py …` | `python3 -m nullcode.fixtures.create_gradle_fixture …` |
| `python3 -m unittest test_*.py` | `python3 -m unittest discover -s tests` |
| `docker build … gradle_profile` | `docker build … src/nullcode/gradle/gradle_profile` |
| `docker compose …` in `src/nullcode` | `docker compose -f compose/nullcode.compose.yml …` |

Prefix Python commands with `PYTHONPATH=src` unless the package is installed.

## Undocumented increments

7A and 7B were written up in `44e36aa` and are listed above. The
`accepted-java-v1` acceptance workflow and the
`javac-string-array-stream-loop-v1` repair rule are implemented and tested but
still have **no milestone document**. See `docs/PROJECT_STATE.md` §6.1 —
writing them is the clearest remaining documentation gap.
