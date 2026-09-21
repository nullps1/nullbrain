"""Preview or explicitly publish a reviewed workflow as a GitHub draft PR."""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess

from java_workflow import JOBS, Store
from repo_workflow import TARGET, TEST, git
from review_java import review_source


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def prepare(job, artifacts=JOBS):
    if job.get('repo_spec') and json.loads(job['repo_spec']).get('profile') == 'gradle-junit-v1':
        return prepare_gradle(job, artifacts)
    if job['status'] != 'succeeded' or not job.get('repo_spec'):
        raise ValueError('Only successful repository workflows can be published')
    attempt = job['attempts'][-1]
    result = attempt['result']
    if attempt['phase'] != 'passed' or not result.get('passed') or not result.get('verification_passed'):
        raise ValueError('Workflow has no passing verification record')
    for stage in ('compile', 'tests', 'cleanup'):
        if result.get(stage, {}).get('exit_code') != 0 or result[stage].get('timed_out'):
            raise ValueError('Missing or unsuccessful ' + stage)
    if 'RESULT: 8/8 checks passed' not in result['tests'].get('log', ''):
        raise ValueError('Missing test completion evidence')
    review = result.get('review', {})
    if review.get('status') != 'passed' or review.get('findings') != []:
        raise ValueError('A passing targeted review is required; use milestone 4 or later')
    root = (artifacts / f"workflow-{job['id']}").resolve()
    checkout = root / 'repo'
    metadata = result['repository']
    if pathlib.Path(metadata['checkout']).resolve() != checkout:
        raise ValueError('Unexpected checkout path')
    commit, base = metadata['commit'], metadata['base_commit']
    if not all(re.fullmatch('[0-9a-f]{40,64}', value) for value in (commit, base)):
        raise ValueError('Invalid recorded commit')
    if git(checkout, 'rev-parse', 'HEAD') != commit or git(checkout, 'status', '--porcelain'):
        raise ValueError('Checkout changed after verification')
    if git(checkout, 'rev-parse', commit + '^') != base:
        raise ValueError('Expected exactly one task commit directly on the verified base')
    if git(checkout, 'diff', '--name-only', base, commit).splitlines() != [TARGET]:
        raise ValueError('Unexpected files in task commit')
    source = git(checkout, 'show', commit + ':' + TARGET, raw=True)
    patch = git(checkout, 'diff', '--no-ext-diff', '--no-textconv', '--binary', base, commit, raw=True)
    if digest(source) != review.get('source_sha256') or digest(patch) != review.get('diff_sha256'):
        raise ValueError('Commit content does not match the reviewed source/diff hashes')
    folder = root / f"attempt-{attempt['number']}"
    if (folder / 'Numbers.java').read_text(encoding='utf-8') != source:
        raise ValueError('Verified source snapshot does not match the commit')
    if (folder / 'NumbersTest.java').read_text(encoding='utf-8') != git(checkout, 'show', commit + ':' + TEST, raw=True):
        raise ValueError('Verified tests do not match committed tests')
    if review_source(source, patch)['status'] != 'passed':
        raise ValueError('Current review rules reject this change')
    body = (
        'Replace the placeholder Numbers.max implementation with a maximum calculation that handles '
        'negative values and integer boundaries, and rejects null or empty arrays.\n\n'
        f'NullCode workflow: {job["id"]}; accepted attempt: {attempt["number"]}.\n\n'
        'Validation performed locally on nullbrain:\n'
        '- Java compilation succeeded.\n- All 8 fixed functional checks passed.\n'
        '- Targeted review passed with no findings. These three rules are not a comprehensive review.\n'
        '- Temporary verification container was removed successfully.\n\n'
        f'Base commit: `{base}`\n\nVerified commit: `{commit}`\n\n'
        f'Java image: `{result["image_id"]}`\n\n'
        f'Source SHA-256: `{review["source_sha256"]}`\n\n'
        f'Diff SHA-256: `{review["diff_sha256"]}`\n\n'
        'This is a draft for human review. These are local checks, not GitHub CI results.\n')
    return {'workflow_id': job['id'], 'checkout': str(checkout), 'root': str(root),
            'commit': commit, 'base_commit': base, 'base': 'main',
            'head': f"agent/workflow-{job['id']}-{commit[:12]}",
            'title': 'Implement verified Numbers.max behavior', 'body': body}


def prepare_gradle(job, artifacts):
    from gradle_workflow import inspect
    if job['status'] != 'succeeded':
        raise ValueError('Workflow did not succeed')
    spec = json.loads(job['repo_spec'])
    attempt = job['attempts'][-1]
    result = attempt['result']
    if attempt['phase'] != 'passed' or not result.get('passed') or not result.get('verification_passed'):
        raise ValueError('Missing successful Gradle verification')
    for stage in ('compile', 'tests', 'cleanup'):
        record = result.get(stage) or {}
        if record.get('exit_code') != 0 or record.get('timed_out'):
            raise ValueError('Unsuccessful Gradle stage: ' + stage)
    root = (artifacts / f"workflow-{job['id']}").resolve()
    checkout = root / 'repo'
    metadata = result['repository']
    commit, base = metadata['commit'], spec['base_commit']
    if pathlib.Path(metadata['checkout']).resolve() != checkout:
        raise ValueError('Unexpected checkout')
    if git(checkout, 'rev-parse', 'HEAD') != commit or git(checkout, 'status', '--porcelain'):
        raise ValueError('Checkout changed since verification')
    if git(checkout, 'rev-parse', commit + '^') != base:
        raise ValueError('Task commit must be directly on the verified base')
    if git(checkout, 'diff', '--name-only', base, commit).splitlines() != [spec['target']]:
        raise ValueError('Unexpected changed files')
    paths, config = inspect(checkout, commit)
    hashes = {p: hashlib.sha256(git(checkout, 'show', commit + ':' + p, binary=True)).hexdigest() for p in paths}
    if hashes != result.get('snapshot_sha256'):
        raise ValueError('Committed project differs from the verified snapshot')
    report = result.get('junit') or {}
    if report.get('failures') != 0 or report.get('tests', 0) - report.get('skipped', 0) < config['minimum_tests']:
        raise ValueError('Missing passing JUnit evidence')
    source = git(checkout, 'show', commit + ':' + spec['target'], raw=True)
    patch = git(checkout, 'diff', '--no-ext-diff', '--no-textconv', '--binary', base, commit, raw=True)
    review = result.get('review') or {}
    if review.get('status') != 'passed' or review.get('findings') != [] or review.get('source_sha256') != digest(source) or review.get('diff_sha256') != digest(patch):
        raise ValueError('Review evidence does not match the commit')
    if review_source(source, patch)['status'] != 'passed':
        raise ValueError('Current review rules reject the source')
    body = (f"Implement the requested change in `{spec['target']}`.\n\nTask: {spec['task']}\n\n"
            f"Workflow {job['id']}, attempt {attempt['number']}.\n\n"
            f"Local validation: Gradle testClasses and test succeeded; {report['tests']} JUnit cases, "
            f"{report['failures']} failures, {report['skipped']} skipped. Targeted review passed.\n\n"
            f"Verified commit: `{commit}`\n\nBase: `{base}`\n\nJava build image: `{result['image_id']}`\n\n"
            f"Diff SHA-256: `{review['diff_sha256']}`\n\n"
            'Tests and build configuration were preserved. These are local checks, not GitHub CI results. '
            'The targeted review is not comprehensive. Draft for human review.\n')
    return {'workflow_id': job['id'], 'checkout': str(checkout), 'root': str(root), 'commit': commit,
            'base_commit': base, 'base': 'main', 'head': f"agent/workflow-{job['id']}-{commit[:12]}",
            'title': 'Implement verified Java change: ' + pathlib.PurePosixPath(spec['target']).name, 'body': body}


def gh_api(endpoint, method='GET', fields=None):
    args = ['gh', 'api', '--hostname', 'github.com', '--method', method, endpoint]
    for key, value in (fields or {}).items():
        args += ['-f', f'{key}={value}']
    response = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if response.returncode:
        raise RuntimeError(response.stderr.strip())
    return json.loads(response.stdout)


def validate_repo(repo):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Use a GitHub owner/repository name')
    return repo


def deliver(plan, repo, api=gh_api, git_fn=git, create_fn=None):
    validate_repo(repo)
    base = api(f'repos/{repo}/git/ref/heads/main')['object']['sha']
    if base != plan['base_commit']:
        raise ValueError('Remote main differs from the verified base. Submit a new workflow against its current commit.')
    existing = api(f'repos/{repo}/pulls', fields={
        'head': repo.split('/')[0] + ':' + plan['head'], 'base': 'main', 'state': 'all', 'per_page': '100'})
    if existing:
        if len(existing) != 1 or existing[0]['head']['sha'] != plan['commit'] or existing[0]['state'] != 'open' or not existing[0]['draft']:
            raise ValueError('A conflicting, closed, merged or non-draft PR already exists for this head')
        return existing[0]['html_url']
    url = 'https://github.com/' + repo + '.git'
    # Supply a fixed credential helper explicitly because local Git operations ignore global config.
    auth = ('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential')
    ref = 'refs/heads/' + plan['head']
    remote = git_fn(plan['checkout'], *auth, 'ls-remote', '--heads', url, ref)
    if remote and remote.split()[0] != plan['commit']:
        raise ValueError('Destination branch already contains a different commit')
    if not remote:
        git_fn(plan['checkout'], *auth, 'push', url, plan['commit'] + ':' + ref)
    remote = git_fn(plan['checkout'], *auth, 'ls-remote', '--heads', url, ref)
    if not remote or remote.split()[0] != plan['commit']:
        raise ValueError('Remote branch verification failed')
    if api(f'repos/{repo}/git/ref/heads/main')['object']['sha'] != plan['base_commit']:
        raise ValueError('Remote main advanced during publication. Task branch exists, but no PR was created.')
    body_path = pathlib.Path(plan['root']) / 'pull-request.md'
    body_path.write_text(plan['body'], encoding='utf-8')
    args = ['gh', 'pr', 'create', '--repo', 'github.com/' + repo, '--base', 'main', '--head', plan['head'],
            '--draft', '--title', plan['title'], '--body-file', str(body_path)]
    if create_fn:
        return create_fn(args)
    response = subprocess.run(args, cwd=plan['checkout'], capture_output=True, text=True, timeout=120)
    if response.returncode:
        raise RuntimeError('Branch may already be pushed. Rerun this same command to recover: ' + response.stderr.strip())
    url = response.stdout.strip()
    if not re.fullmatch(r'https://github\.com/[^/]+/[^/]+/pull/\d+', url):
        raise RuntimeError('Unexpected PR response; inspect GitHub before retrying')
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow_id', type=int)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--publish', action='store_true', help='Push the verified task branch and create a draft PR')
    args = parser.parse_args()
    validate_repo(args.repo)
    plan = prepare(Store().show(args.workflow_id))
    print(json.dumps({key: value for key, value in plan.items() if key != 'body'}, indent=2))
    print('\nDraft PR description:\n' + plan['body'])
    if not args.publish:
        print('DRY RUN: no GitHub requests or pushes performed. Add --publish to deliver.')
        return
    import fcntl
    with (pathlib.Path(plan['root']) / 'publish.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Recheck local evidence after acquiring the publication lock.
        plan = prepare(Store().show(args.workflow_id))
        url = deliver(plan, args.repo)
        receipt = {'repository': args.repo, 'commit': plan['commit'], 'branch': plan['head'], 'url': url}
        (pathlib.Path(plan['root']) / 'publication.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        print('Draft PR:', url)


if __name__ == '__main__':
    main()
