import io
import json
from pathlib import Path
import shutil
import zipfile
import pytest

from app import core


FAKE_MODULE = r'''
export const SCHEMA = 'minecraftaibuilder-b143-building-dsl-v1';
export function validateDesign(d) {if (d?.schema !== SCHEMA || d?.designOnly !== true) throw Error('BAD_SCHEMA'); return true;}
export function compileDesign(d) {
  validateDesign(d);
  const primitives=[];
  for (let y=1; y<=3; y++) for (let z=2; z<=9; z++) for (let x=2; x<=9; x++) primitives.push([x,y,z,0,0]);
  return {primitives,stats:{voxels:primitives.length,constructionApproved:false},
    designOnly:true,readiness:'NOT_PREPARED',worldContacts:0,worldCoordinates:null,
    BuildPlan:{operations:[]},sourceSha256:'src',geometrySha256:'geo',
    bounds:{minX:2,maxX:9,minY:1,maxY:3,minZ:2,maxZ:9}, title:d.title};
}
export function verifyCompiled(d,s){return s?.primitives?.length===192;}
'''


def fake_engine(root: Path):
    p = root / core.ENGINE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(FAKE_MODULE.encode('utf-8'))  # Keep the fake SHA identical on Windows (no CRLF conversion).
    return root


def make_archive(paths, sha="a"*40):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w') as z:
        for path, body in paths.items():
            z.writestr('MinecraftAIBuilder-'+sha+'/'+path, body)
    return mem.getvalue()


def archive_engine(sha="a"*40):
    return make_archive({core.ENGINE_FILE: FAKE_MODULE, 'README.md':'example'}, sha=sha)


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed')
def test_bridge_contract_and_rejection(tmp_path):
    engine = fake_engine(tmp_path/'engine')
    report = core.compatibility_check(engine)
    assert report['voxels'] == 192 and report['readOnly'] is True
    with pytest.raises(core.StudioError):
        core.run_bridge(engine, {'schema':'unsafe'})


def test_safe_archive(tmp_path):
    core._safe_extract_zip(archive_engine(),tmp_path)
    assert (tmp_path/core.ENGINE_FILE).is_file()
    with pytest.raises(core.StudioError):
        core._safe_extract_zip(make_archive({'../bad.txt': 'oops'}),tmp_path/'bad')


@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed')
def test_install_pin_update_rollback_and_failure(tmp_path):
    mgr = core.EngineManager(tmp_path)
    a='a'*40
    b='b'*40
    mgr.install(a,archive=archive_engine())
    assert mgr.active_sha==a
    assert (mgr.root/'engines'/a/core.ENGINE_FILE).is_file()
    mgr.install(b,archive=archive_engine(b))
    assert mgr.active_sha==b
    assert mgr.state['previous']==a
    assert mgr.rollback()==a
    assert mgr.active_sha==a
    # A malformed update cannot change the active version.
    with pytest.raises(core.StudioError):
        mgr.install('c'*40,archive=make_archive({'README.md':'no engine'}, sha='c'*40))
    assert mgr.active_sha==a
    assert not (mgr.root/'engines'/('c'*40)).exists()
    assert not list((mgr.root/'engines').glob('.staging-*'))


def test_llm_json_is_data_only():
    doc={'schema':core.SCHEMA,'designOnly':True}
    assert core.parse_llm_json('```json\n'+json.dumps(doc)+'\n```')==doc
    with pytest.raises(core.StudioError):
        core.parse_llm_json('```python\nimport os\n```')
    with pytest.raises(core.StudioError):
        core.parse_llm_json('[1, 2]')


def test_branch_validation_no_urls(tmp_path):
    mgr = core.EngineManager(tmp_path)
    mgr.save_branch('feature/b143-universal-llm-building-dsl')
    assert mgr.state['tracked_branch'].startswith('feature/')
    with pytest.raises(core.StudioError):
        mgr.save_branch('../../etc/passwd')
    with pytest.raises(core.StudioError):
        mgr.save_branch('https://evil.example/')
    assert mgr.state['tracked_branch'].startswith('feature/')


def test_update_sha_only(tmp_path):
    with pytest.raises(core.StudioError):
        core.EngineManager(tmp_path).install('main',archive=archive_engine())
    with pytest.raises(core.StudioError):
        core.request_json('https://evil.example/thing')

@pytest.mark.skipif(shutil.which('node') is None, reason='Node needed')
def test_tamper_detection_before_launch(tmp_path):
    mgr=core.EngineManager(tmp_path)
    sha='d'*40
    mgr.install(sha,archive=archive_engine(sha))
    assert mgr.active_path is not None
    p = mgr.versions / sha / core.ENGINE_FILE
    p.write_text(p.read_text() + '\n// tampered', encoding='utf-8')
    assert mgr.active_path is None
    with pytest.raises(core.StudioError):
        mgr.install(sha,archive=archive_engine(sha))
    assert mgr.state['active']==sha  # no silent rewrite/activation


def test_fail_closed_bad_archive_paths(tmp_path):
    for path in ('../../evil', 'C:/evil.js', 'sub/../../evil'):
        with pytest.raises(core.StudioError):
            core._safe_extract_zip(make_archive({path:'no'}),tmp_path/'extract')
