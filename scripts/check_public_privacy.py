"""Check tracked public files; report locations and categories, never values.

This is a preventive pattern check, not proof that Git history or images are safe.
"""
import io
from pathlib import Path
import re
import subprocess
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
    paths = subprocess.check_output(['git', 'ls-files', '-z']).decode('utf-8').split('\0')
    findings = []
    for name in filter(None, paths):
        path = Path(name)
        if path.is_file():
            findings.extend(scan(name, path.read_bytes()))
        else:
            findings.append((name, 'missing-tracked-file'))
    for name, rule in findings:
        print(f'{name}: {rule}')
    print(f'Public privacy check: {len(findings)} findings. History and images need separate review.')
    return bool(findings)


if __name__ == '__main__':
    raise SystemExit(main())
