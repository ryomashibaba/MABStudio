"""Regression tests for the Windows installer LICENSE omission observed on 2026-11-17."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import node_packaging as packaging


LICENSE = ("Node.js is licensed for use as follows:\n\n"
           "Copyright Node.js contributors. All rights reserved.\n"
           + "Permission is hereby granted, free of charge, for redistribution.\n" * 15).encode()


@pytest.fixture
def mock_node(tmp_path, monkeypatch):
    installed = tmp_path / 'NodeInstallation'
    installed.mkdir()
    node = installed / 'node.exe'
    node.write_bytes(b'MZ' + b'\0' * 100_001)
    dest = tmp_path / 'dist' / 'MABStudio'
    dest.mkdir(parents=True)
    monkeypatch.setattr(packaging.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(stdout='v22.23.5\n', stderr='', returncode=0))
    return node, dest


def test_local_license_is_packaged_without_network(mock_node, monkeypatch):
    node, dest = mock_node
    (node.parent / 'LICENSE').write_bytes(LICENSE)
    monkeypatch.setattr(packaging, '_upstream_license', lambda *_:
                        pytest.fail('local matching license should not require network'))
    result = packaging.package_node(dest, node_path=node)
    assert result['licenseSource'] == 'installed-directory'
    assert (dest / 'node.exe').read_bytes() == node.read_bytes()
    assert (dest / 'NODE_LICENSE.txt').read_bytes() == LICENSE



def test_bin_layout_finds_license_in_installation_root(tmp_path, monkeypatch):
    install = tmp_path / 'node-v22'
    bindir = install / 'bin'
    bindir.mkdir(parents=True)
    node = bindir / 'node.exe'
    node.write_bytes(b'MZ' + b'\0' * 100_001)
    (install / 'LICENSE').write_bytes(LICENSE)
    dest = tmp_path / 'output'
    dest.mkdir()
    monkeypatch.setattr(packaging.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(stdout='v22.23.5', stderr='', returncode=0))
    monkeypatch.setattr(packaging, '_upstream_license', lambda *_:
                        pytest.fail('existing installation license must be reused'))
    assert packaging.package_node(dest, node_path=node)['licenseSource'] == 'installed-directory'


def test_missing_local_license_fetches_matching_release(mock_node, monkeypatch):
    node, dest = mock_node
    asked = []
    def get_license(version):
        asked.append(version)
        return LICENSE
    monkeypatch.setattr(packaging, '_upstream_license', get_license)
    result = packaging.package_node(dest, node_path=node)
    assert asked == ['v22.23.5']
    assert result['licenseSource'] == 'official-matching-release'
    assert (dest / 'NODE_LICENSE.txt').read_bytes() == LICENSE


def test_download_uses_exact_official_tag_url(monkeypatch):
    class FakeResponse:
        def __init__(self, url): self.url = url
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return self.url
        def read(self, size): return LICENSE
    seen = []
    def open_url(url, timeout):
        seen.append((url, timeout))
        return FakeResponse(url)
    monkeypatch.setattr(packaging, 'urlopen', open_url)
    assert packaging._upstream_license('v22.23.5') == LICENSE
    assert seen == [('https://raw.githubusercontent.com/nodejs/node/v22.23.5/LICENSE', 25)]


def test_download_redirect_to_unrelated_host_is_rejected(monkeypatch):
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return 'https://untrusted.example/LICENSE'
    monkeypatch.setattr(packaging, 'urlopen', lambda *args, **kw: FakeResponse())
    with pytest.raises(packaging.NodePackageError, match='REDIRECT_REJECTED'):
        packaging._upstream_license('v22.23.5')


def test_missing_license_offline_refuses_release(mock_node, monkeypatch):
    node, dest = mock_node
    monkeypatch.setattr(packaging, '_upstream_license', lambda *_:
                        (_ for _ in ()).throw(packaging.NodePackageError('NO_NETWORK')))
    with pytest.raises(packaging.NodePackageError, match='NO_NETWORK'):
        packaging.package_node(dest, node_path=node)
    assert not (dest / 'node.exe').exists()
    assert not (dest / 'NODE_LICENSE.txt').exists()


def test_invalid_local_license_is_not_distributed(mock_node, monkeypatch):
    node, dest = mock_node
    (node.parent / 'LICENSE').write_text('NOT A LICENSE', encoding='utf-8')
    monkeypatch.setattr(packaging, '_upstream_license', lambda *_:
                        (_ for _ in ()).throw(packaging.NodePackageError('OFFLINE')))
    with pytest.raises(packaging.NodePackageError, match='OFFLINE'):
        packaging.package_node(dest, node_path=node)
    assert not (dest / 'NODE_LICENSE.txt').exists()


@pytest.mark.parametrize('version', ['v20.19.0', 'v22.5.0-pre', 'v0.0.0',
                                     '22.1.0', 'v22.1.0\nhttps://example.com', ''])
def test_bad_versions_are_rejected(version):
    with pytest.raises(packaging.NodePackageError):
        packaging.validate_version(version)


def test_windows_batch_and_ci_both_use_same_safe_packager():
    root = Path(__file__).resolve().parents[1]
    batch = (root / 'build_windows.bat').read_bytes().decode('ascii')
    workflow = (root / '.github/workflows/windows-build.yml').read_text(encoding='utf-8')
    assert '-m app.node_packaging "dist\\MABStudio"' in batch
    assert 'python -m app.node_packaging dist/MABStudio' in workflow
    assert '%%~dpiLICENSE' not in batch
    assert 'Split-Path $nodePath -Parent' not in workflow
