# javac repair rule: `javac-string-array-stream-loop-v1`

This document records an already-implemented narrow compiler-diagnostic repair rule used by `accepted-java-v1`.

It is not a general Java repair engine.

## Problem recognized

The rule matches one specific javac failure:

- compilation failed;
- the result is otherwise repairable;
- cleanup succeeded;
- the error belongs to the selected production target;
- javac reports `cannot find symbol`;
- the missing symbol is `method stream()`;
- the location is `String[]`.

This corresponds to code that attempts to call `.stream()` directly on the array returned by `String.split(...)`.

## Action

When and only when that exact error block is recognized, the next repair prompt receives this guidance:

> Replace the invalid array.stream() chain with an ordinary for loop over the split words. Do not use Streams. Retain all contract requirements and existing behavior.

The rule does not edit code itself. The model still produces the replacement source, and the normal acceptance workflow verifies it from scratch.

## Fail-closed matching

`compiler_repair_rule()` reads the javac compile log and matches one contiguous error block.

It deliberately refuses similar but different evidence, including:

- a different target file;
- a different missing method;
- a different receiver type;
- a different compiler error category;
- evidence found only in JUnit/test output;
- compile timeout;
- failed cleanup;
- a non-repairable result;
- two unrelated error blocks whose lines would only match if combined.

This prevents generic model prose or unrelated output from triggering the special-case guidance.

## Safety properties

The rule:

- does not widen file scope;
- does not change the fixed acceptance tests;
- does not change the repair budget;
- does not mark a candidate as passing;
- does not bypass compile, test, cleanup, review, or snapshot checks.

It only supplies additional advisory text on a repair attempt after deterministic evidence identifies the exact known compiler error.

## Tests

`tests/test_accepted_workflow.py` contains direct unit coverage for positive matching and the fail-closed cases above, plus integration coverage proving that the guidance participates in the normal accepted workflow without bypassing its gates.
