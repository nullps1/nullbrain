"""Restricted local-repository mode: one editable Numbers.java and fixed JDK commands."""
import json
import os
import pathlib
import re
import subprocess

from java_workflow import JOBS, clip, generate, verify
from validate_java import extract_source
from review_java import REVIEW_DEMO, review_source

TARGET = 'src/main/java/Numbers.java'
TEST = 'src/test/java/NumbersTest.java'


def git(repo, *args, raw=False, binary=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    process = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull,
        '-c', 'commit.gpgSign=false', '-c', 'core.autocrlf=false', '-C', str(repo), *args],
        env=env, capture_output=True, timeout=60)
    if process.returncode:
        raise RuntimeError('Git failed: ' + process.stderr.decode('utf-8', errors='replace')[-2000:])
    if binary:
        return process.stdout
    output = process.stdout.decode('utf-8', errors='replace').replace('\r\n', '\n')
    return output if raw else output.strip()


def check_tree(repo, commit):
    rows = git(repo, 'ls-tree', '-r', commit).splitlines()
    if len(rows) > 32:
        raise ValueError('This milestone supports repositories with at most 32 tracked files')
    paths = set()
    total = 0
    for row in rows:
        meta, path = row.split('\t', 1)
        mode, kind, oid = meta.split()
        if mode != '100644' or kind != 'blob':
            raise ValueError('Symlinks, submodules and executable files are not supported in this profile')
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', path) or '..' in pathlib.PurePosixPath(path).parts or path.startswith('/'):
            raise ValueError('Unsupported tracked path')
        size = int(git(repo, 'cat-file', '-s', oid))
        total += size
        if size > 32768 or total > 262144:
            raise ValueError('Repository exceeds the small-task size limit')
        paths.add(path)
    if not {TARGET, TEST} <= paths:
        raise ValueError(f'Repository needs {TARGET} and {TEST}')


def prepare_spec(repo, base, task):
    path = pathlib.Path(repo).resolve(strict=True)
    if not path.is_dir() or not task.strip() or len(task.encode()) > 500:
        raise ValueError('Use a local repository and a task of 1 to 500 bytes')
    commit = git(path, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}')
    if not re.fullmatch('[0-9a-f]{40,64}', commit):
        raise ValueError('Could not resolve the base commit')
    check_tree(path, commit)
    return {'repo': str(path), 'base_commit': commit, 'task': task.strip(), 'profile': 'numbers-jdk21-v1'}


def run_repo_job(store, job, generate_fn=generate, verify_fn=verify, artifacts=JOBS, review_fn=review_source):
    job_id = job['id']
    number = 1
    try:
        spec = json.loads(job['repo_spec'])
        root = artifacts / f'workflow-{job_id}'
        root.mkdir(parents=True, exist_ok=False)
        checkout = root / 'repo'
        store.status(job_id, 'checking_out')
        git(root, 'clone', '--no-hardlinks', '--no-checkout', '--', pathlib.Path(spec['repo']).as_posix(), checkout.as_posix())
        check_tree(checkout, spec['base_commit'])
        branch = f'agent/workflow-{job_id}'
        git(checkout, 'checkout', '-b', branch, spec['base_commit'])
        # Future jobs must never accidentally push back to the source repository.
        git(checkout, 'remote', 'remove', 'origin')
        target = checkout / TARGET
        tests = (checkout / TEST).read_text(encoding='utf-8')
        original = target.read_text(encoding='utf-8')
        base_prompt = ('Edit only Numbers.java. Return the complete public class Numbers, no package or main method. '
                       'The existing NumbersTest is read-only. Task:\n' + spec['task'] + '\n')
        prompt = base_prompt + 'Current source:\n' + clip(original, 900)
        metadata = {'repository': spec['repo'], 'base_commit': spec['base_commit'], 'branch': branch,
                    'checkout': str(checkout), 'editable_file': TARGET, 'profile': spec['profile']}
        (root / 'repository.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        build_repairs = 0
        review_corrections = 0
        for number in (1, 2, 3):
            folder = root / f'attempt-{number}'
            folder.mkdir()
            folder.chmod(0o755)
            store.status(job_id, 'generating' if number == 1 else 'repairing')
            store.attempt(job_id, number, phase='generating', artifact_dir=str(folder))
            answer = REVIEW_DEMO if number == 1 and spec.get('review_demo') else generate_fn(
                prompt, lambda ident: store.attempt(job_id, number, inference_job=ident))
            (folder / 'answer.txt').write_text(answer, encoding='utf-8')
            source = answer
            try:
                source = extract_source(answer)
            except ValueError as error:
                result = {'passed': False, 'repairable': True, 'extraction_error': str(error)}
            else:
                target.write_text(source, encoding='utf-8')
                store.attempt(job_id, number, source=source)
                changed = git(checkout, 'diff', '--name-only', spec['base_commit']).splitlines()
                if changed != [TARGET]:
                    raise ValueError('Expected exactly one changed file: ' + TARGET)
                def phase(state):
                    store.status(job_id, state)
                    store.attempt(job_id, number, phase=state)
                result = verify_fn(source, folder, phase, test_source=tests)
                patch = git(checkout, 'diff', '--no-ext-diff', '--no-textconv', '--binary', spec['base_commit'], raw=True)
                (folder / 'diff.patch').write_text(patch, encoding='utf-8')
                result['diff_path'] = str(folder / 'diff.patch')
                result['verification_passed'] = result['passed']
                if result['passed']:
                    phase('reviewing')
                    review = review_fn(source, patch)
                    (folder / 'review.json').write_text(json.dumps(review, indent=2), encoding='utf-8')
                    result['review'] = review
                    if review['status'] != 'passed':
                        result['passed'] = False
                        result['repairable'] = True
            if result['passed']:
                # The build used this exact source and the original committed test file.
                if target.read_text(encoding='utf-8') != source or (checkout / TEST).read_text(encoding='utf-8') != tests:
                    raise ValueError('Checkout changed during verification')
                git(checkout, 'add', '--', TARGET)
                git(checkout, '-c', 'user.name=NullCode', '-c', 'user.email=nullcode@localhost',
                    'commit', '-m', f'Implement verified Java task {job_id}')
                metadata['commit'] = git(checkout, 'rev-parse', 'HEAD')
                metadata['diff_path'] = result['diff_path']
                metadata['review_path'] = str(folder / 'review.json')
                (root / 'repository.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
            result['repository'] = dict(metadata)
            (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            store.attempt(job_id, number, phase='passed' if result['passed'] else 'failed', result=json.dumps(result))
            if result['passed']:
                store.status(job_id, 'succeeded')
                return
            if not result.get('repairable'):
                store.status(job_id, 'failed', 'Repository verification failed; see attempts')
                return
            if result.get('review', {}).get('status') == 'changes_requested':
                if review_corrections >= 1:
                    store.status(job_id, 'failed', 'Review still requests changes after one correction; no commit created')
                    return
                review_corrections += 1
                diagnostic = 'Review findings: ' + ' '.join(item['message'] for item in result['review']['findings'])
            else:
                if build_repairs >= 1:
                    store.status(job_id, 'failed', 'Verification still fails after one build/test repair; no commit created')
                    return
                build_repairs += 1
                diagnostic = result.get('extraction_error') or next(
                    (result[key]['log'] for key in ('tests', 'compile') if result.get(key)), 'Verification failed')
            prompt = base_prompt + 'Repair source:\n' + clip(source, 700) + '\nDiagnostic:\n' + clip(diagnostic, 400)
        store.status(job_id, 'failed', 'Attempt budget exhausted; no commit created')
    except Exception as error:
        store.attempt(job_id, number, phase='error', result=json.dumps({'error': str(error)}))
        store.status(job_id, 'failed', str(error))
