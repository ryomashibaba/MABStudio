"""Isolated, non-executing V11 source snapshots for Studio.

The selected snapshot is a cached candidate only. Studio currently has no
approved V11 runner adapter, so this module never starts V11 or a model.
It does not read or mutate the source checkout, MAB B143 state, or history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request

BRANCH = 'research/r125-v6-readonly-facade-diagnostic-20261111'
REMOTE = 'https://github.com/ryomashibaba/MinecraftAIBuilder.git'
API = 'https://api.github.com/repos/ryomashibaba/MinecraftAIBuilder/commits/'
SHA = re.compile(r'[0-9a-f]{40}\Z')
VERSION = re.compile(r'\b(V\d+\.\d+(?:\.\d+)?)\b')
RUNNER = Path('scripts/r125-local-architecture-plan-run.mjs')
CONTRACT_FILES = (
    RUNNER,
    Path('scripts/r125-local-architecture-plan-run.test.mjs'),
    Path('scripts/r125-offline-replay.mjs'),
    Path('source/ai/b143-building-dsl.mjs'),
)
MAX_COMMIT_RESPONSE = 256_000


def _git(repo: Path, *args: str, timeout: int = 90) -> str:
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    return subprocess.run(['git', '-C', str(repo), *args], env=env,
                          check=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def latest_v11_commit(*, remote: str = REMOTE, opener=None) -> dict:
    """Read the moving research ref and its commit subject; never fetch code."""
    line = subprocess.run(
        ['git', 'ls-remote', '--exit-code', remote, 'refs/heads/' + BRANCH],
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'},
        check=True, capture_output=True, text=True, timeout=20).stdout.strip()
    fields = line.split()
    if len(fields) != 2 or not SHA.fullmatch(fields[0]) or fields[1] != 'refs/heads/' + BRANCH:
        raise ValueError('Unexpected V11 branch response')
    sha = fields[0]
    if opener is None:
        opener = urllib.request.urlopen
    request = urllib.request.Request(API + sha, headers={
        'Accept': 'application/vnd.github+json', 'User-Agent': 'MABStudio-V11-Version-Check'})
    try:
        with opener(request, timeout=8) as response:
            raw = response.read(MAX_COMMIT_RESPONSE + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError('V11 commit version lookup failed') from exc
    if len(raw) > MAX_COMMIT_RESPONSE:
        raise ValueError('V11 commit response exceeds limit')
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict) or data.get('sha') != sha:
        raise ValueError('V11 commit metadata does not match branch SHA')
    subject = data.get('commit', {}).get('message', '').splitlines()[0]
    match = VERSION.search(subject)
    return {'sha': sha, 'version': match.group(1) if match else 'version unavailable',
            'subject': subject[:160], 'source': 'UNAPPROVED_RESEARCH_HEAD'}


def verify_candidate_layout(root: Path, sha: str) -> bool:
    """Cheap offline shape check only; it does not certify runtime compatibility."""
    if not SHA.fullmatch(sha) or not root.is_dir() or root.is_symlink():
        return False
    try:
        for rel in CONTRACT_FILES:
            item = root / rel
            if item.is_symlink() or not item.is_file() or item.stat().st_size > 4_000_000:
                return False
        runner = (root / RUNNER).read_text(encoding='utf-8')
        return all(marker in runner for marker in (
            'r125-local-plan-one-shot-v1', 'RUN_REPORT.json',
            'B143_SOURCE_REVIEW_ONLY.json', 'MODEL_RESPONSE_ORIGINAL.txt',
            'SOURCE_DIAGNOSIS.json'))
    except (OSError, UnicodeError):
        return False


class V11Versions:
    """Download and roll back inert research candidates outside the checkout."""
    def __init__(self, cache: str | Path, *, remote: str = REMOTE):
        self.cache = Path(cache).resolve()
        self.remote = remote
        self.state_file = self.cache / 'selection.json'

    def read(self) -> dict:
        if not self.state_file.exists():
            return {'selected': None, 'previous': None}
        if self.state_file.is_symlink():
            raise ValueError('V11 selection state is a symlink')
        data = json.loads(self.state_file.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) != {'selected', 'previous'}:
            raise ValueError('Invalid V11 selection state')
        if any(v is not None and (not isinstance(v, str) or not SHA.fullmatch(v))
               for v in data.values()):
            raise ValueError('Invalid V11 selection SHA')
        return data

    def status(self) -> dict:
        state = self.read()
        return {**state,
                'selected_version': self._version(state['selected']),
                'previous_version': self._version(state['previous']),
                'generation_connected': False,
                'label': 'CANDIDATE_ONLY_GENERATION_NOT_CONNECTED'}

    def _version(self, sha: str | None) -> str | None:
        if sha is None:
            return None
        metadata = self.cache / sha / '.studio-v11-candidate.json'
        if metadata.is_symlink() or not metadata.is_file():
            return None
        try:
            data = json.loads(metadata.read_text(encoding='utf-8'))
            return data.get('version') if data.get('sha') == sha else None
        except (OSError, ValueError, TypeError):
            return None

    def install_candidate(self, sha: str, *, idle: bool, version: str = 'version unavailable') -> Path:
        if not idle:
            raise ValueError('Finish generation and review before updating V11 candidates')
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ValueError('Invalid V11 candidate SHA')
        self.cache.mkdir(parents=True, exist_ok=True)
        if self.cache.is_symlink():
            raise ValueError('V11 cache must not be a symlink')
        prior = self.read()
        target = self.cache / sha
        if target.exists():
            if not verify_candidate_layout(target, sha):
                raise ValueError('Cached V11 candidate failed the offline layout check')
        else:
            with tempfile.TemporaryDirectory(prefix='.stage-', dir=self.cache) as temp:
                temp_root = Path(temp)
                bare = temp_root / 'objects.git'
                bare.mkdir()
                _git(bare, 'init', '--bare', '--template=')
                _git(bare, 'fetch', '--no-tags', '--depth=1', self.remote, sha)
                fetched = _git(bare, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}')
                if fetched != sha:
                    raise ValueError('Fetched V11 commit does not match requested SHA')
                archive = temp_root / 'source.tar'
                _git(bare, 'archive', '--format=tar', '--output=' + str(archive), sha)
                stage = temp_root / 'source'
                stage.mkdir()
                with tarfile.open(archive, 'r:') as stream:
                    members = stream.getmembers()
                    if any(not (member.isfile() or member.isdir()) for member in members):
                        raise ValueError('V11 archive contains non-regular entries')
                    stream.extractall(stage, members=members, filter='data')
                if not verify_candidate_layout(stage, sha):
                    raise ValueError('V11 candidate failed the offline layout check')
                (stage / '.studio-v11-candidate.json').write_text(
                    json.dumps({'sha': sha, 'version': str(version)[:40],
                                'role': 'INERT_CANDIDATE_NOT_RUNNABLE_BY_STUDIO'},
                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                stage.rename(target)
        if prior['selected'] != sha:
            self._save({'selected': sha, 'previous': prior['selected']})
        return target

    def rollback_candidate(self, *, idle: bool) -> str:
        if not idle:
            raise ValueError('Finish generation and review before changing V11 candidates')
        state = self.read()
        previous = state['previous']
        if previous is None or not verify_candidate_layout(self.cache / previous, previous):
            raise ValueError('No verified previous V11 candidate is available')
        self._save({'selected': previous, 'previous': state['selected']})
        return previous

    def _save(self, value: dict) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.cache,
                                             prefix='.selection-', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_file)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
