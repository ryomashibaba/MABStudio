import json
from pathlib import Path
import subprocess

import pytest

from app.v11_versions import V11Versions, latest_v11_commit, verify_candidate_layout


SHA_RE = __import__('re').compile(r'^[0-9a-f]{40}$')


def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _source_files(root: Path, *, bad=False):
    files = {
        'scripts/r125-local-architecture-plan-run.mjs': (
            "r125-local-plan-one-shot-v1 RUN_REPORT.json "
            "B143_SOURCE_REVIEW_ONLY.json MODEL_RESPONSE_ORIGINAL.txt SOURCE_DIAGNOSIS.json"),
        'scripts/r125-local-architecture-plan-run.test.mjs': 'offline tests',
        'scripts/r125-offline-replay.mjs': 'offline replay',
        'source/ai/b143-building-dsl.mjs': 'frozen compiler',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('bad' if bad and name.endswith('run.mjs') else content,
                        encoding='utf-8')


def _remote_repo(tmp_path):
    work = tmp_path / 'source'
    work.mkdir()
    bare = tmp_path / 'remote.git'
    _git(tmp_path, 'init', '--bare', str(bare))
    _git(work, 'init', '-b', 'main')
    _git(work, 'config', 'user.email', 'test@example.invalid')
    _git(work, 'config', 'user.name', 'test')
    _source_files(work)
    _git(work, 'add', '.')
    _git(work, 'commit', '-m', 'V39.1: test fixture')
    _git(work, 'remote', 'add', 'origin', str(bare))
    _git(work, 'push', 'origin', 'HEAD:refs/heads/research/r125-v6-readonly-facade-diagnostic-20261111')
    return work, bare, _git(work, 'rev-parse', 'HEAD')


def test_latest_candidate_reads_only_expected_remote_head(tmp_path):
    _, remote, sha = _remote_repo(tmp_path)

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            assert size <= 300_000
            return json.dumps({'sha': sha, 'commit': {'message': 'V39.1: fixture'}}).encode()

    result = latest_v11_commit(remote=str(remote), opener=lambda *a, **kw: Response())
    assert result['sha'] == sha
    assert result['version'] == 'V39.1'
    assert result['source'] == 'UNAPPROVED_RESEARCH_HEAD'


def test_candidate_download_and_rollback_use_isolated_sha_cache(tmp_path):
    source, remote, sha = _remote_repo(tmp_path)
    cache = tmp_path / 'studio' / 'v11-versions'
    mgr = V11Versions(cache, remote=str(remote))
    first = mgr.install_candidate(sha, idle=True, version='V39.1')
    assert first == cache / sha
    assert verify_candidate_layout(first, sha)
    assert mgr.status()['selected_version'] == 'V39.1'

    # Add a second immutable commit to the fixture remote.
    (source / 'extra.txt').write_text('second', encoding='utf-8')
    _git(source, 'add', '.')
    _git(source, 'commit', '-m', 'V39.2: second fixture')
    _git(source, 'push', 'origin', 'HEAD:refs/heads/research/r125-v6-readonly-facade-diagnostic-20261111')
    second_sha = _git(source, 'rev-parse', 'HEAD')
    mgr.install_candidate(second_sha, idle=True, version='V39.2')
    assert mgr.status()['previous'] == sha
    assert mgr.rollback_candidate(idle=True) == sha
    assert mgr.status()['selected_version'] == 'V39.1'
    assert mgr.status()['previous'] == second_sha
    # The source checkout and its refs stay untouched by the Studio cache.
    assert _git(source, 'rev-parse', 'HEAD') == second_sha
    assert _git(source, 'status', '--porcelain') == ''


def test_nonidle_and_bad_layout_leave_selection_unchanged(tmp_path):
    _, remote, sha = _remote_repo(tmp_path)
    mgr = V11Versions(tmp_path / 'cache', remote=str(remote))
    with pytest.raises(ValueError, match='generation and review'):
        mgr.install_candidate(sha, idle=False)
    assert mgr.read() == {'selected': None, 'previous': None}

    work = tmp_path / 'bad'
    work.mkdir()
    _source_files(work, bad=True)
    assert not verify_candidate_layout(work, sha)

