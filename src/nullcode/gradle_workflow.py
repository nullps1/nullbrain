"""Selectable single-file Java edits with an approved offline Gradle/JUnit build."""
import hashlib
import json
import pathlib
import re
import subprocess
import uuid
import xml.etree.ElementTree as ET
from java_workflow import JOBS, clip, command, generate
from repo_workflow import git
from review_java import review_source

PROFILE = pathlib.Path(__file__).parent / 'gradle_profile'
IMAGE = 'nullcode-gradle:8.14.3-jdk21'


def inspect(repo, commit):
    paths = []
    total = 0
    for row in git(repo, 'ls-tree', '-r', commit).splitlines():
        metadata, name = row.split('\t', 1)
        mode, kind, oid = metadata.split()
        if mode != '100644' or kind != 'blob' or not re.fullmatch(r'[A-Za-z0-9_./-]+', name):
            raise ValueError('Only ordinary tracked files with simple paths are supported')
        if '..' in pathlib.PurePosixPath(name).parts or name.startswith('/'):
            raise ValueError('Invalid tracked path')
        size = int(git(repo, 'cat-file', '-s', oid))
        total += size
        if size > 32768 or total > 524288:
            raise ValueError('Repository exceeds the small-project context limit')
        paths.append(name)
    if len(paths) > 128:
        raise ValueError('At most 128 tracked files are supported')
    for name in ('build.gradle', 'settings.gradle', 'gradle.properties'):
        if git(repo, 'show', commit + ':' + name, raw=True) != (PROFILE / name).read_text(encoding='utf-8'):
            raise ValueError('Build configuration must match the approved gradle_profile template: ' + name)
    config = json.loads(git(repo, 'show', commit + ':.nullcode.json'))
    if config.get('profile') != 'gradle-junit-v1':
        raise ValueError('Unsupported project profile')
    allowed = config.get('editable_files')
    if not isinstance(allowed, list) or not allowed or len(allowed) > 8 or len(set(allowed)) != len(allowed):
        raise ValueError('Configure 1 to 8 unique editable Java files')
    for name in allowed:
        if name not in paths or not name.startswith('src/main/java/') or not name.endswith('.java'):
            raise ValueError('Editable files must be committed Java production sources')
    minimum = config.get('minimum_tests')
    if type(minimum) is not int or minimum < 1 or minimum > 1000:
        raise ValueError('minimum_tests must be an integer between 1 and 1000')
    if not any(name.startswith('src/test/java/') and name.endswith('.java') for name in paths):
        raise ValueError('Project needs committed Java tests')
    return paths, config


def prepare_spec(repo, base, task, target):
    repo = pathlib.Path(repo).resolve(strict=True)
    if not task.strip() or len(task.encode()) > 500:
        raise ValueError('Task must contain 1 to 500 bytes')
    commit = git(repo, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}')
    paths, config = inspect(repo, commit)
    if target not in config['editable_files']:
        raise ValueError('Selected file is not in the committed editable_files list')
    current = git(repo, 'show', commit + ':' + target, raw=True)
    if len(current.encode()) > 900:
        raise ValueError('Current file exceeds the 900-byte model-context limit; split the task first')
    return {'repo': str(repo), 'base_commit': commit, 'task': task.strip(), 'target': target,
            'profile': 'gradle-junit-v1', 'minimum_tests': config['minimum_tests']}


def extract(answer, target):
    blocks = re.findall(r'^```(?:java)?[ \t]*\r?\n(.*?)^```[ \t]*$', answer, re.M | re.S)
    if '```' in answer:
        if len(blocks) != 1:
            raise ValueError('Return exactly one complete Java source block')
        source = blocks[0].strip() + '\n'
    else:
        source = answer.strip() + '\n'
    name = pathlib.PurePosixPath(target).stem
    if len(source.encode()) > 4096 or not re.search(r'\bpublic\s+(?:final\s+)?(?:class|record|interface|enum)\s+' + re.escape(name) + r'\b', source):
        raise ValueError('Expected a complete public Java type named ' + name + ' under 4096 bytes')
    return source


def junit_report(xml):
    if '<!DOCTYPE' in xml or '<!ENTITY' in xml:
        raise ValueError('Unexpected XML declarations')
    cleaned = re.sub(r'<\?xml[^?]*\?>', '', xml)
    root = ET.fromstring('<reports>' + cleaned + '</reports>')
    cases = list(root.iter('testcase'))
    failures = []
    for case in cases:
        failure = case.find('failure')
        if failure is None:
            failure = case.find('error')
        if failure is not None:
            message = failure.get('message') or next((line.strip() for line in (failure.text or '').splitlines() if line.strip()), 'Test failed')
            failures.append(case.get('name', 'test') + ': ' + message)
    return {'tests': len(cases),
            'failures': sum(c.find('failure') is not None or c.find('error') is not None for c in cases),
            'skipped': sum(c.find('skipped') is not None for c in cases),
            'diagnostics': '\n'.join(clip(line, 160) for line in failures)[:3000]}


def _diagnostic_lines(diagnostic):
    return [
        line.strip()
        for line in diagnostic.splitlines()
        if line.strip() and not line.lstrip().startswith('at ')
    ]


def diagnosis_prompt(target, task, source, diagnostic):
    lines = _diagnostic_lines(diagnostic)

    intro = (
        'You are debugging a Java 21 implementation. Do NOT write replacement code yet. '
        'Analyze the failure in at most 6 short bullets. Identify the exact logic/state '
        'mistake causing the expected/actual results and state what behavior must change.\n'
        f'File: {target}\n'
        f'Task: {task}\n'
        'Failures:\n'
    )

    suffix = '\nCurrent complete source:\n' + source
    budget = 2000 - len((intro + suffix).encode())

    if budget < 0:
        raise ValueError(
            'Complete source and diagnosis instructions exceed the 2000-byte controller limit'
        )

    selected = []
    used = 0
    for line in lines:
        candidate = clip(line, 150) + '\n'
        size = len(candidate.encode())
        if used + size > budget:
            break
        selected.append(candidate)
        used += size

    if not selected:
        raise ValueError('No diagnostic evidence fits within the diagnosis prompt limit')

    return intro + ''.join(selected) + suffix


def repair_prompt(header, source, diagnostic, diagnosis=''):
    lines = _diagnostic_lines(diagnostic)

    diagnosis = ' '.join(diagnosis.strip().split())
    if len(diagnosis) > 500:
        diagnosis = diagnosis[:500] + '...'

    instructions = (
        'The previous implementation failed. You MUST produce a materially different '
        'implementation that corrects the underlying logic. Do not return the previous '
        'source unchanged and do not merely reformat it.\n'
        'Use the debugging diagnosis and expected/actual failures below. Check the new '
        'algorithm against every task requirement before answering.\n'
        'Return only the complete corrected Java source, with no prose.\n'
    )

    diagnosis_section = (
        'Debugging diagnosis:\n' + diagnosis + '\n'
        if diagnosis else ''
    )

    suffix = '\nPrevious complete source:\n' + source
    fixed = header + instructions + diagnosis_section + 'Failures:\n' + suffix
    budget = 2000 - len(fixed.encode())

    if budget < 0:
        raise ValueError(
            'Complete source, diagnosis, and repair instructions exceed the '
            '2000-byte controller limit'
        )

    selected = []
    used = 0
    for line in lines:
        candidate = clip(line, 140) + '\n'
        size = len(candidate.encode())
        if used + size > budget:
            break
        selected.append(candidate)
        used += size

    if not selected:
        raise ValueError('No repair diagnostic fits within the 2000-byte controller limit')

    return (
        header
        + instructions
        + diagnosis_section
        + 'Failures:\n'
        + ''.join(selected)
        + suffix
    )


def verify(checkout, paths, folder, minimum, phase):
    snapshot = folder / 'snapshot'
    snapshot.mkdir()
    snapshot.chmod(0o755)
    hashes = {}
    for name in paths:
        source = checkout / name
        if source.is_symlink():
            raise ValueError('Unexpected symlink in checkout')
        data = source.read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Container user shares null's UID, and directories are readable/traversable.
        destination.write_bytes(data)
        destination.chmod(0o644)
    image = subprocess.check_output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True, timeout=30).strip()
    container = 'nullcode-gradle-' + uuid.uuid4().hex
    result = {'passed': False, 'repairable': False, 'image_id': image, 'snapshot_sha256': hashes,
              'compile': None, 'tests': None, 'junit': None}
    try:
        started = command(['docker', 'run', '-d', '--rm', '--pull=never', '--name', container,
            '--label=io.nullcode.workflow=java-smoke', '--network=none', '--read-only', '--user=1001:1001',
            '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--memory=1536m', '--memory-swap=1536m',
            '--cpus=2', '--pids-limit=256', '--log-driver=none',
            '--tmpfs=/work:rw,nosuid,nodev,size=768m,mode=1777', '--tmpfs=/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--mount', f'type=bind,source={snapshot},target=/src,readonly', '--entrypoint=/bin/sleep', image, '900'], 30)
        if started['exit_code']:
            result['infrastructure_error'] = started
            return result
        setup = command(['docker', 'exec', container, '/bin/sh', '-ec',
            'cp -R /src /work/project; cp -R /opt/nullcode-cache /work/cache; chmod -R u+w /work/project /work/cache'], 60)
        if setup['exit_code']:
            result['infrastructure_error'] = setup
            return result
        base = ['docker', 'exec', container, 'gradle', '--offline', '--no-daemon', '--max-workers=1',
                '--console=plain', '-g', '/work/cache', '-p', '/work/project']
        phase('compiling')
        result['compile'] = command(base + ['clean', 'testClasses'], 240)
        if result['compile']['exit_code']:
            result['repairable'] = result['compile']['exit_code'] == 1 and ':compileJava FAILED' in result['compile']['log']
            return result
        phase('testing')
        result['tests'] = command(base + ['test', '--rerun-tasks'], 240)
        xml = command(['docker', 'exec', container, '/bin/sh', '-ec',
            'cat /work/project/build/test-results/test/TEST-*.xml'], 30)
        if xml['exit_code'] == 0:
            (folder / 'junit.xml').write_text(xml['log'], encoding='utf-8')
            result['junit'] = junit_report(xml['log'])
            report = result['junit']
            result['passed'] = result['tests']['exit_code'] == 0 and report['failures'] == 0 and report['tests'] - report['skipped'] >= minimum
            result['repairable'] = result['tests']['exit_code'] == 1 and report['failures'] > 0
        return result
    finally:
        result['cleanup'] = command(['docker', 'rm', '-f', container], 30)
        if result['cleanup']['exit_code'] and 'No such container' not in result['cleanup']['log']:
            result['passed'] = result['repairable'] = False
        (folder / 'verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


def run_job(store, job, generate_fn=generate, verify_fn=verify, artifacts=JOBS):
    number = 1
    try:
        spec = json.loads(job['repo_spec'])
        root = artifacts / f"workflow-{job['id']}"
        root.mkdir(parents=True, exist_ok=False)
        checkout = root / 'repo'
        git(root, 'clone', '--no-hardlinks', '--no-checkout', '--', pathlib.Path(spec['repo']).as_posix(), checkout.as_posix())
        paths, config = inspect(checkout, spec['base_commit'])
        if spec['target'] not in config['editable_files']:
            raise ValueError('Target not approved')
        branch = f"agent/workflow-{job['id']}"
        git(checkout, 'checkout', '-b', branch, spec['base_commit'])
        git(checkout, 'remote', 'remove', 'origin')
        target = checkout / spec['target']
        original_hashes = {p: hashlib.sha256((checkout / p).read_bytes()).hexdigest() for p in paths}
        header = ('Java 21. Edit only ' + spec['target'] + '. Preserve its package and public API. '
                  'Return the entire Java source, no prose. Task: ' + spec['task'] + '\n')
        prompt = header + 'Current source:\n' + target.read_text(encoding='utf-8')
        previous_source = None
        for number in (1, 2):
            folder = root / f'attempt-{number}'
            folder.mkdir()
            store.status(job['id'], 'generating' if number == 1 else 'repairing')
            store.attempt(job['id'], number, phase='generating', artifact_dir=str(folder))
            (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
            answer = generate_fn(prompt, lambda ident: store.attempt(job['id'], number, inference_job=ident))
            (folder / 'answer.txt').write_text(answer, encoding='utf-8')
            source = answer
            try:
                source = extract(answer, spec['target'])
            except ValueError as error:
                result = {'passed': False, 'repairable': True, 'error': str(error)}
            else:
                if number > 1 and source == previous_source:
                    raise ValueError('Model returned identical source on repair; previous failures remain. Skipped redundant build.')
                previous_source = source
                target.write_text(source, encoding='utf-8')
                changed = git(checkout, 'diff', '--name-only', spec['base_commit']).splitlines()
                if changed != [spec['target']]:
                    raise ValueError('Expected an edit to exactly the selected file')
                store.attempt(job['id'], number, source=source)
                def phase(state):
                    store.status(job['id'], state)
                    store.attempt(job['id'], number, phase=state)
                result = verify_fn(checkout, paths, folder, config['minimum_tests'], phase)
                patch = git(checkout, 'diff', '--no-ext-diff', '--no-textconv', '--binary', spec['base_commit'], raw=True)
                (folder / 'diff.patch').write_text(patch, encoding='utf-8')
                result['verification_passed'] = result['passed']
                if result['passed']:
                    phase('reviewing')
                    review = review_source(source, patch)
                    review['scope'] = 'Targeted text checks only; JUnit determines tested behavior.'
                    result['review'] = review
                    (folder / 'review.json').write_text(json.dumps(review, indent=2), encoding='utf-8')
                    if review['status'] != 'passed':
                        result['passed'] = False
                        result['repairable'] = True
                if result['passed']:
                    current = {p: hashlib.sha256((checkout / p).read_bytes()).hexdigest() for p in paths}
                    if current != result['snapshot_sha256'] or any(current[p] != original_hashes[p] for p in paths if p != spec['target']):
                        raise ValueError('Checkout changed during verification')
                    git(checkout, 'add', '--', spec['target'])
                    git(checkout, '-c', 'user.name=NullCode', '-c', 'user.email=nullcode@localhost', 'commit', '-m', f"Implement verified Java task {job['id']}")
                    result['repository'] = {'profile': 'gradle-junit-v1', 'checkout': str(checkout), 'branch': branch,
                        'base_commit': spec['base_commit'], 'commit': git(checkout, 'rev-parse', 'HEAD'),
                        'editable_file': spec['target'], 'diff_path': str(folder / 'diff.patch')}
                    (root / 'repository.json').write_text(json.dumps(result['repository'], indent=2), encoding='utf-8')
            (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            store.attempt(job['id'], number, phase='passed' if result['passed'] else 'failed', result=json.dumps(result))
            if result['passed']:
                store.status(job['id'], 'succeeded')
                return
            if number == 2 or not result.get('repairable'):
                store.status(job['id'], 'failed', 'Gradle/JUnit verification failed; see attempt evidence')
                return
            diagnostic = result.get('error') or (result.get('junit') or {}).get('diagnostics')
            if not diagnostic:
                diagnostic = str(result.get('review', {}).get('findings') or (result.get('compile') or {}).get('log') or 'Verification failed')

            diag_prompt = diagnosis_prompt(
                spec['target'],
                spec['task'],
                source,
                diagnostic,
            )
            (folder / 'diagnosis-prompt.txt').write_text(diag_prompt, encoding='utf-8')

            diagnosis_job = {'id': None}

            def record_diagnosis_job(ident):
                diagnosis_job['id'] = ident
                (folder / 'diagnosis-inference-job.txt').write_text(
                    str(ident),
                    encoding='utf-8',
                )

            diagnosis = generate_fn(diag_prompt, record_diagnosis_job)
            (folder / 'diagnosis.txt').write_text(diagnosis, encoding='utf-8')

            prompt = repair_prompt(header, source, diagnostic, diagnosis)
    except Exception as error:
        store.attempt(job['id'], number, phase='error', result=json.dumps({'error': str(error)}))
        store.status(job['id'], 'failed', str(error))
