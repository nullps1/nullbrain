"""Validate multi-file workflow evidence before the shared draft-only publisher."""
import hashlib
import json
import pathlib
import re

from nullcode.core.review_java import review_source
from nullcode.gradle.gradle_workflow import inspect
from nullcode.repo.repo_execute_workflow import (
    CONTINUE,
    DISTINGUISHING_CLASSIFICATIONS,
    evidence_level,
    evidence_summary,
    policy_decision,
)
from nullcode.repo.repo_plan_workflow import validate_plan
from nullcode.repo.repo_workflow import git


PROFILE = 'repo-execute-v1'


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _text(checkout, commit, name):
    """Read committed text the way the producer read it from the working tree.

    ``git(..., raw=True)`` folds CRLF only, but the producer hashed sources it
    read through Python text mode, which also folds a lone CR. Matching that
    normalization keeps a stray CR from failing an otherwise honest review
    record. Committed bytes stay pinned separately by the binary snapshot
    hashes, so this never widens what content can be published.
    """
    return git(checkout, 'show', commit + ':' + name, raw=True).replace('\r', '\n')


def _verification(record, minimum, label):
    if not isinstance(record, dict) or record.get('passed') is not True:
        raise ValueError('Missing successful ' + label + ' verification')
    for stage in ('compile', 'tests', 'cleanup'):
        evidence = record.get(stage) or {}
        if (type(evidence.get('exit_code')) is not int
                or evidence['exit_code'] != 0 or evidence.get('timed_out')):
            raise ValueError('Unsuccessful ' + label + ' stage: ' + stage)
    report = record.get('junit') or {}
    for field in ('tests', 'failures', 'skipped'):
        if type(report.get(field)) is not int or report[field] < 0:
            raise ValueError('Missing usable ' + label + ' JUnit evidence')
    executed = report['tests'] - report['skipped']
    if report['failures'] != 0 or executed < minimum:
        raise ValueError('Insufficient passing ' + label + ' JUnit evidence')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', record.get('image_id', '')):
        raise ValueError('Missing pinned ' + label + ' build image')
    return executed


def _behavioral_delta(result):
    """The producer's behavioral-delta evidence, re-checked before publishing.

    Publication eligibility is still `status == 'succeeded'`; this adds
    nothing to what the workflow accepts and removes nothing from it. It only
    refuses to publish a record whose novelty evidence is absent, is not a
    distinguishing classification, disagrees with its own evidence level, or
    whose recorded hybrid snapshot is not the state the producer said it was.
    """
    delta = result.get('behavioral_delta')
    if not isinstance(delta, dict):
        raise ValueError('Missing behavioral-delta evidence')
    classification = delta.get('classification')
    level = delta.get('evidence_level')
    if (classification not in DISTINGUISHING_CLASSIFICATIONS
            or delta.get('distinguishing') is not True
            or level != evidence_level(classification)
            or policy_decision(classification) != CONTINUE):
        raise ValueError('Behavioral-delta evidence is not distinguishing')
    manifest = delta.get('manifest') or {}
    expected = manifest.get('expected_snapshot_sha256')
    if not isinstance(expected, dict) or not expected \
            or manifest.get('hybrid_snapshot_sha256') != expected:
        raise ValueError('Hybrid counterfactual snapshot evidence does not match')
    return delta, classification, level


def prepare_repo_execute(job, artifacts):
    if job.get('status') != 'succeeded':
        raise ValueError('Only successful repository workflows can be published')
    spec = json.loads(job['repo_spec'])
    root = (pathlib.Path(artifacts) / f"workflow-{job['id']}").resolve()
    checkout = root / 'repo'
    metadata = json.loads((root / 'repository.json').read_text(encoding='utf-8'))
    if metadata.get('profile') != PROFILE:
        raise ValueError('Unexpected repository profile')
    if pathlib.Path(metadata.get('checkout', '')).resolve() != checkout:
        raise ValueError('Unexpected checkout path')
    commit, base = metadata.get('commit'), spec.get('base_commit')
    if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40,64}', value)
               for value in (commit, base)) or metadata.get('base_commit') != base:
        raise ValueError('Invalid recorded commit or base')

    # Repair records can have larger attempt numbers than the final record.
    finals = [a for a in job.get('attempts', [])
              if isinstance(a.get('result'), dict)
              and a['result'].get('profile') == PROFILE
              and a['result'].get('repository') == metadata]
    if len(finals) != 1:
        raise ValueError('Expected one authoritative final workflow result')
    attempt = finals[0]
    result = attempt['result']
    if attempt.get('phase') != 'passed' or result.get('passed') is not True:
        raise ValueError('Missing passing final workflow result')
    if (git(checkout, 'rev-parse', 'HEAD') != commit
            or git(checkout, 'status', '--porcelain')):
        raise ValueError('Checkout changed after verification')
    if git(checkout, 'rev-list', '--parents', '-n', '1', commit).split() != [commit, base]:
        raise ValueError('Expected one task commit directly on the verified base')

    paths, config = inspect(checkout, base)
    committed_paths, committed_config = inspect(checkout, commit)
    if committed_paths != paths or committed_config != config:
        raise ValueError('Committed project changed protected paths or configuration')
    selected = metadata.get('editable_files')
    # Re-derive the approved test scope instead of trusting the field: the
    # shared Gradle inspect() validates editable_files only, so accepting
    # editable_test_files as written would leave the publisher's approved set
    # weaker than the producer's own prepare_spec check.
    approved_tests = config.get('editable_test_files')
    if (not isinstance(approved_tests, list) or not approved_tests
            or len(approved_tests) > 8
            or len(set(approved_tests)) != len(approved_tests)
            or not all(isinstance(name, str) and name in paths
                       and name.startswith('src/test/java/') and name.endswith('.java')
                       for name in approved_tests)):
        raise ValueError('Invalid approved editable_test_files configuration')
    approved = config['editable_files'] + approved_tests
    if (not isinstance(selected, list) or not all(isinstance(p, str) for p in selected)
            or not 2 <= len(selected) <= 3 or len(set(selected)) != len(selected)
            or not set(selected) <= set(approved)
            or not set(selected) & set(config['editable_files'])
            or not set(selected) & set(approved_tests)
            or result.get('selected_files') != selected):
        raise ValueError('Invalid approved multi-file scope')
    if set(git(checkout, 'diff', '--name-only', base, commit).splitlines()) != set(selected):
        raise ValueError('Committed diff does not match selected files')

    base_hashes = {p: _hash(git(checkout, 'show', base + ':' + p, binary=True)) for p in paths}
    hashes = {p: _hash(git(checkout, 'show', commit + ':' + p, binary=True)) for p in paths}
    if any(hashes[p] != base_hashes[p] for p in paths if p not in selected):
        raise ValueError('Protected repository content changed')
    candidate = result.get('candidate_verification')
    baseline = result.get('baseline_verification')
    candidate_count = _verification(candidate, config['minimum_tests'], 'candidate')
    baseline_count = _verification(baseline, config['minimum_tests'], 'baseline')
    if candidate_count <= baseline_count:
        raise ValueError('No increase in executed test cases over the original suite')
    if candidate['image_id'] != baseline['image_id']:
        raise ValueError('Candidate and baseline build images differ')
    if candidate.get('snapshot_sha256') != hashes:
        raise ValueError('Candidate snapshot does not match committed content')
    expected_baseline = dict(hashes)
    for name in set(selected) & set(approved_tests):
        expected_baseline[name] = base_hashes[name]
    if baseline.get('snapshot_sha256') != expected_baseline:
        raise ValueError('Baseline snapshot is not original tests against candidate production')

    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    if plan != result.get('plan'):
        raise ValueError('Plan artifact differs from final workflow evidence')
    validate_plan(plan, selected)
    patch = git(checkout, 'diff', '--no-ext-diff', '--no-textconv', '--binary', base, commit, raw=True)
    patch_hash = _hash(patch.encode())
    review = result.get('review') or {}
    reviews = review.get('files') or {}
    if review.get('status') != 'passed' or set(reviews) != set(selected):
        raise ValueError('Missing per-file targeted review')
    for name in selected:
        source = _text(checkout, commit, name)
        evidence = reviews[name]
        if (evidence.get('status') != 'passed' or evidence.get('findings') != []
                or evidence.get('source_sha256') != _hash(source.encode())
                or evidence.get('diff_sha256') != patch_hash):
            raise ValueError('Review hashes or findings do not match: ' + name)
        if review_source(source, patch)['status'] != 'passed':
            raise ValueError('Current review rules reject: ' + name)

    delta, classification, level = _behavioral_delta(result)
    replan = result.get('semantic_replan') or {}
    replan_note = (
        f"This candidate replaced an earlier one that demonstrated no behavioral "
        f"delta; it is semantic re-plan {replan.get('attempt')} and was re-verified "
        "from planning onwards by every gate.\n\n"
        if replan.get('attempt') else ''
    )

    file_summary = '\n'.join(f'- `{p}`: targeted review passed.' for p in selected)
    body = (
        f"{plan['summary']}\n\nTask: {spec['task']}\n\n"
        f"NullCode workflow {job['id']} (`{PROFILE}`).\n\n"
        f"Changed files:\n{file_summary}\n\n"
        f"Local validation: candidate and original-test regression compilation and tests passed. "
        f"Executed JUnit cases: {candidate_count} candidate, {baseline_count} original suite. "
        "Both verification containers were removed successfully. Protected files were preserved.\n\n"
        f"Behavioral-delta evidence: `{classification}`, evidence level **{level}**.\n"
        f"{evidence_summary(classification)}\n\n"
        f"{replan_note}"
        f"Verified commit: `{commit}`\n\nBase commit: `{base}`\n\n"
        f"Java build image: `{candidate['image_id']}`\n\nDiff SHA-256: `{patch_hash}`\n\n"
        "Targeted text checks are not a comprehensive correctness or security review. "
        "These are local checks, not GitHub CI results. Draft for human review.\n"
    )
    return {'workflow_id': job['id'], 'checkout': str(checkout), 'root': str(root),
            'commit': commit, 'base_commit': base, 'base': 'main',
            'head': f"agent/workflow-{job['id']}-{commit[:12]}",
            'title': 'Implement verified repository task ' + str(job['id']), 'body': body}
