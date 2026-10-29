"""Check tracked public files; report locations and categories, never values.

This is a preventive pattern check, not proof that Git history or images are safe.
"""
import io
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

RULES = {
    'supabase-key': rb'\bsb_(?:publishable|secret)_[A-Za-z0-9_-]{20,}',
    'supabase-project': rb'https?://[a-z0-9]{20}\.supabase\.co',
    'github-token': rb'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})',
    'private-key': rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/]{40,}',
    'provider-key': rb'\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{32,}',
    'aws-access-key': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'jwt': rb'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}',
}
LIMIT = 64 * 1024 * 1024
PUBLIC_FILES = {
    '.gitignore', 'README.md', 'LICENSE', 'COMMUNITY.md', 'MAINTAINERS.md',
    '.github/pull_request_template.md', '.github/workflows/community.yml',
    '.github/workflows/private-validation.yml', '.github/workflows/private-platform-build.yml',
    '.github/workflows/spec-review-private-validation.yml',
    'docs/PRIVATE-PLATFORM-BUILDS.md', 'docs/actions-tunnel-workflow.md',
    'scripts/check_public_privacy.py',
    'scripts/test_platform_workflow.py', 'scripts/tests/test_public_privacy.py',
    'tests/test_private_validation_diagnostics.py',
}
LEGACY_COMMIT = 'e676bc3caacb4d238b9026c6025aae5049d71060'


def check_public_path(name, data=None, legacy=None):
    if name in PUBLIC_FILES:
        return []
    if legacy is not None and name in legacy:
        return [] if data == legacy[name] else [(name, 'changed-legacy-source-not-authorized')]
    return [(name, 'outside-public-build-allowlist')]


def legacy_files():
    # Read the previously sanitized public snapshot, not a private checkout or
    # a generated digest manifest. Content must match these exact original bytes.
    archive = subprocess.check_output(['git', 'archive', '--format=tar', LEGACY_COMMIT])
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as bundle:
        for item in bundle:
            if item.isfile():
                entries[item.name] = bundle.extractfile(item).read()
            elif not item.isdir():
                raise ValueError('Unexpected legacy object type')
    return entries


def scan(name, data, depth=0):
    findings = []
    base = Path(name.rsplit('!', 1)[-1]).name.lower()
    if base == '.env' or (base.startswith('.env.') and not base.endswith(('example', 'sample', 'template'))):
        findings.append((name, 'runtime-env'))
    if base.endswith(('.jks', '.keystore', '.p12', '.pfx')) or base in ('id_rsa', 'id_ed25519', 'local.properties'):
        findings.append((name, 'private-config-file'))
    for rule, pattern in RULES.items():
        if re.search(pattern, data):
            findings.append((name, rule))
    if base.endswith(('.zip', '.jar')):
        if depth >= 4:
            return findings + [(name, 'archive-depth-limit')]
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(i.file_size for i in archive.infolist()) > LIMIT:
                    return findings + [(name, 'archive-size-limit')]
                for item in archive.infolist():
                    if not item.is_dir():
                        findings.extend(scan(name + '!' + item.filename, archive.read(item), depth + 1))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
            findings.append((name, 'unreadable-archive'))
    return findings


def main():
    legacy = legacy_files()
    paths = subprocess.check_output(['git', 'ls-files', '-z']).decode('utf-8').split('\0')
    findings = []
    for name in filter(None, paths):
        path = Path(name)
        if path.is_file():
            data = path.read_bytes()
            findings.extend(check_public_path(name, data, legacy))
            findings.extend(scan(name, data))
        else:
            findings.append((name, 'missing-tracked-file'))
    for name, rule in findings:
        print(f'{name}: {rule}')
    print(f'Public privacy check: {len(findings)} findings. History and images need separate review.')
    return bool(findings)


if __name__ == '__main__':
    raise SystemExit(main())
