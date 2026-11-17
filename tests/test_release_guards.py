import hashlib
import io
import json
from pathlib import Path
import shutil
import zipfile

import pytest
from app import core
from tests.test_core import FAKE_MODULE, fake_engine, make_archive


def test_zip_root_directory_is_supported(tmp_path):
    sha = 'a'*40
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('MinecraftAIBuilder-'+sha+'/', '')
        z.writestr('MinecraftAIBuilder-'+sha+'/'+core.ENGINE_FILE, FAKE_MODULE)
    core._safe_extract_zip(stream.getvalue(), tmp_path, expected_commit=sha)
    assert (tmp_path/core.ENGINE_FILE).is_file()


def test_zip_commit_cannot_mismatch(tmp_path):
    with pytest.raises(core.StudioError, match='アーカイブとコミット'):
        core._safe_extract_zip(make_archive({core.ENGINE_FILE: FAKE_MODULE}, 'b'*40),
                               tmp_path, expected_commit='a'*40)


@pytest.mark.parametrize('path', [
    'file:stream', 'con.txt', 'aux', 'com1.log', 'trailing. ',
    'trailing.', 'nested/../escape.txt', 'nested\\inside.txt',
])
def test_zip_refuses_windows_unsafe_names(tmp_path, path):
    with pytest.raises(core.StudioError):
        core._safe_extract_zip(make_archive({path:'bad'}), tmp_path)


def test_zip_windows_case_aliases_rejected(tmp_path):
    with pytest.raises(core.StudioError, match='重複'):
        core._safe_extract_zip(make_archive({'README.md':'a','readme.md':'b'}),tmp_path)


def test_unreviewed_engine_change_refused(tmp_path):
    fake_engine(tmp_path)
    assert core.compatibility_check(tmp_path)['voxels']==192
    p=tmp_path/core.ENGINE_FILE
    p.write_text(p.read_text(encoding='utf-8') + '\n// a change not approved\n', encoding='utf-8')
    with pytest.raises(core.StudioError, match='コードが安全確認済み版と異なります'):
        core.compatibility_check(tmp_path)


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed')
def test_node_permission_blocks_unauthorized_filesystem_write(tmp_path, monkeypatch):
    # Even if developer approves a modified module during isolated testing,
    # Node's OS-level permission model independently refuses unrelated writes.
    target=tmp_path/'important-world-sentinel.txt'
    target.write_text('DO NOT MODIFY',encoding='utf-8')
    source=FAKE_MODULE + '\nimport fs from "node:fs";fs.writeFileSync('+json.dumps(str(target))+',"OVERWRITTEN");\n'
    engine=tmp_path/'engine'
    p=engine/core.ENGINE_FILE
    p.parent.mkdir(parents=True)
    p.write_text(source,encoding='utf-8')
    monkeypatch.setattr(core,'APPROVED_B143_SOURCE_SHA256',hashlib.sha256(source.encode()).hexdigest())
    with pytest.raises(core.StudioError):
        core.compatibility_check(engine)
    assert target.read_text(encoding='utf-8') == 'DO NOT MODIFY'


def test_unknown_provider_is_rejected():
    with pytest.raises(core.StudioError):
        core.llm_models('unknown')
