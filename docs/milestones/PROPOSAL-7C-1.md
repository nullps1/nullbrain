# Proposal 7C-1: model-proposed scope, human-granted scope

**Status: proposal only. Nothing described here is implemented.** This document
exists so the 7C-1 design can be argued about before any code is written. It
makes no claim of verification, and it is not an approval of the design.

## The gap it addresses

Today the approved edit scope is a static, committed fact. `.nullcode.json`
lists `editable_files` and `editable_test_files`; a human wrote those lists and
committed them. Both 7B and the 7C-2 publisher read that committed
configuration and refuse anything outside it — the producer through
`repo_execute_workflow.prepare_spec`, the publisher by re-deriving the same
rules for itself.

That is the right default and this proposal does not change it. But it means a
task whose real scope nobody anticipated cannot run at all: someone has to
guess the file list in advance, by hand, per task. 7A already shows the model
can read a repository and reason about which files a task touches — it just has
no way to say so in a form anyone can act on.

7C-1 is the missing step: **the model proposes a scope; a human grants it.**

## The one invariant

> A model proposal is a suggestion. It is never an edit grant.

Everything below follows from that. The proposal is an artifact a person reads.
The grant is a committed `.nullcode.json` change a person makes. Nothing in
7B or 7C-2 learns to read a proposal, and their trust model does not change:
the approved scope is still "whatever `.nullcode.json` says at the base commit."

This is deliberate. If 7C-1 is implemented as proposed, the 7C-2 validator
needs **no** modification, because from its point of view nothing happened.

## Proposed shape

### A read-only proposal workflow

A new profile `repo-scope-v1`, built on 7A's primitives, running against an
isolated `origin`-less clone and changing nothing:

1. **Inventory.** Reuse 7A's committed-tree listing — Git blobs only, no
   symlinks, no build or VCS directories, the same extension allowlist.
2. **Proposal.** One model call: given the task and the inventory, return JSON
   naming the files that would have to change and why, plus the files it would
   need to *read* for context, plus stated risks.

Prompt construction stays under the controller's 2000-byte input limit and
raises rather than truncating, exactly as 7A does.

### Validation of the proposal

Rejection is the default. A proposal is well-formed only if:

- Every named path is in the committed inventory at the pinned base commit.
- Production paths match `src/main/java/*.java`, test paths
  `src/test/java/*.java` — the shapes `prepare_spec` already enforces.
- It names at least one production file and at least one test file, at most 3
  files to edit total, no duplicates.
- Combined with the existing configuration it stays inside the limits the rest
  of the system assumes: at most 8 `editable_files`, at most 8
  `editable_test_files`, and each named source within `MAX_SOURCE_BYTES` (900)
  so 7B's prompt budget still holds.
- It names none of the protected paths: `build.gradle`, `settings.gradle`,
  `gradle.properties`, and `.nullcode.json` itself. **A scope proposal must
  never be able to propose widening the thing that defines scope.**

A proposal that fails any check is recorded as failed and produces no artifact
a later step could consume.

### The human grant

A separate, explicit CLI step, on the precedent of
`publish/check_acceptance.py`'s `--acceptance-reviewed`: a required
`--scope-reviewed` attestation flag that is **never** defaulted, inferred, or
set programmatically.

The recommended form is the most conservative one: the tool prints the proposed
`.nullcode.json` diff and writes it to a file, and **a person commits it.** The
tool does not commit, does not push, and has no write access to the source
repository. A `--write` mode that stages an uncommitted edit in the operator's
own working tree is a defensible convenience; a mode that commits is not.

This keeps the grant a human act with a human's name on the commit, which is
what makes the committed configuration meaningful as evidence later.

## What this explicitly does not do

- It does not let a model edit `.nullcode.json`, directly or through a tool.
- It does not feed proposals into 7B automatically. A granted scope reaches 7B
  the same way a hand-written one does: committed, at the base commit.
- It does not change 7B's attempt budgets, verification, or review gates.
- It does not change 7C-2. The publisher keeps re-deriving the approved scope
  from the committed configuration and keeps refusing anything outside it.
- It does not widen the extension allowlist, touch build configuration, or add
  network access.

## Open questions for the human deciding this

1. **Is the `--write` staging mode worth it at all?** Printing a diff is
   strictly safer and barely less convenient. Convenience here buys very
   little and costs a clear line.
2. **Should a granted scope expire?** A committed `editable_files` entry is
   permanent until someone removes it, so scope only ever accumulates. A
   review prompt when the list grows past some size may be worth more than any
   automatic mechanism.
3. **Should the proposal be recorded in the workflow store at all?** Storing it
   makes it auditable; it also creates an artifact that a future milestone will
   be tempted to consume automatically. The invariant above says the artifact
   must stay inert, but the safest inert artifact is the one that is only ever
   a file a person reads.
4. **Is one model call enough?** 7A uses two (select, then plan) because
   planning needs file contents. A scope proposal arguably needs to read a
   little of the files it names before it can justify naming them, which would
   make this two calls and a larger prompt budget question.
5. **Does this need a fixture and mutant check** of the kind
   `check_acceptance.py` runs, to establish that a bad proposal is actually
   rejected rather than merely untested?

## If it is implemented

Follow the project's own rules: a small, verified increment; the full suite run
before and after; a milestone document written at the time rather than later;
and no weakening of any existing gate. The constraints in
[`../PROJECT_STATE.md` §10](../PROJECT_STATE.md) and
[`../ARCHITECTURE.md` §8](../ARCHITECTURE.md) apply unchanged.
