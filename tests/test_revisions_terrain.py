"""Safe source revision and exported-world intake tests. No live Minecraft."""
import copy
import json
import os
from pathlib import Path
import zipfile
from dataclasses import dataclass
import pytest

from app import core
from app.examples import example_library
from app.revisions import RevisionStore, compare_scenes, diff_preview_scene
from app.terrain import inspect_mcworld, read_mcworld_terrain, create_overlay_scene, _sample_reader

pytestmark = pytest.mark.real_engine  # use actual hash-verified B143, not conftest fake module
ENGINE = os.environ.get('MAB_B143_VERIFIED_ENGINE')


def real_engine():
    if not ENGINE:
        pytest.skip('True B143 source not configured')
    return Path(ENGINE)


def scenes():
    doc1 = example_library()
    doc2 = copy.deepcopy(doc1)
    doc2['elements'][1]['size'] = [14, 4, 1]
    return doc1, core.run_bridge(real_engine(), doc1), doc2, core.run_bridge(real_engine(), doc2)


def test_real_diff_detects_glass_expansion_and_unchanged_walls():
    a, scene_a, b, scene_b = scenes()
    diff = compare_scenes(scene_a, scene_b)
    assert diff['sourceOnly'] and diff['worldWriteAllowed'] is False
    assert diff['worldCoordinates'] is None
    # The enlarged glass replaces an existing stone wall rather than growing volume.
    assert diff['counts']['added'] == 0
    assert diff['counts']['unchanged'] > 0
    assert diff['counts']['removed'] == 0
    assert diff['counts']['replaced'] == 14
    assert sum(diff['counts'][k] for k in ('added', 'replaced', 'unchanged')) == scene_b['stats']['voxels']
    pv = diff_preview_scene(diff)
    assert pv['previewOnly'] and len(pv['primitives']) == diff['counts']['replaced']


def test_revision_chain_and_safe_restore(tmp_path):
    a, scene_a, b, scene_b = scenes()
    folder = tmp_path / 'studio' / 'projects'
    store = RevisionStore(folder)
    one = store.commit(a, scene_a, engine_commit=core.BASELINE_SHA)
    two = store.commit(b, scene_b, engine_commit=core.BASELINE_SHA,
                       operation='revision', previous_id=one['revisionId'])
    assert store.head()['revisionId'] == two['revisionId']
    assert store.read_revision(one['revisionId'])['geometrySha256'] == scene_a['geometrySha256']
    with pytest.raises(core.StudioError, match='更新競合'):
        store.commit(a, scene_a, engine_commit=core.BASELINE_SHA,
                     operation='revision', previous_id=one['revisionId'])
    restored = store.restore_previous(engine_commit=core.BASELINE_SHA,
                    verify=lambda design: core.run_bridge(real_engine(), design))
    assert restored['geometrySha256'] == scene_a['geometrySha256']
    assert store.head()['revisionId'] == restored['revisionId']
    assert store.read_revision(two['revisionId'])['geometrySha256'] == scene_b['geometrySha256']
    assert restored['worldWrites'] == 0
    assert not (tmp_path / 'studio' / 'minecraftWorlds').exists()


def test_history_tamper_detected(tmp_path):
    a, scene_a, *_ = scenes()
    store=RevisionStore(tmp_path / 'projects')
    item=store.commit(a, scene_a, engine_commit=core.BASELINE_SHA)
    record=tmp_path/'projects'/'revisions'/ (item['revisionId']+'.json')
    data=json.loads(record.read_text(encoding='utf-8'))
    data['design']['title']='some tampering'
    record.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(core.StudioError, match='改変'):
        store.head()


def test_prevent_unsafe_scene_replacement():
    _, scene, *_ = scenes()
    altered = copy.deepcopy(scene)
    altered['BuildPlan']['operations'] = [{'type':'setblock'}]
    with pytest.raises(core.StudioError):
        compare_scenes(scene, altered)
    altered = copy.deepcopy(scene)
    altered['stats']['constructionApproved'] = True
    with pytest.raises(core.StudioError):
        compare_scenes(scene, altered)
    altered = copy.deepcopy(scene)
    altered['primitives'].append(copy.deepcopy(altered['primitives'][0]))
    altered['stats']['voxels'] += 1
    with pytest.raises(core.StudioError, match='重複セル'):
        compare_scenes(scene, altered)


@pytest.mark.parametrize('provider', ['Ollama','LM Studio'])
def test_revision_prompt_includes_original_full_design(tmp_path, monkeypatch, provider):
    from tests.test_local_llm_flow import Handler
    from http.server import HTTPServer
    import threading
    server=HTTPServer(('127.0.0.1',0),Handler)
    server.RequestHandlerClass.requests=[]
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_address[1]}'
        monkeypatch.setattr(core,'OLLAMA_BASE',url)
        monkeypatch.setattr(core,'LM_STUDIO_BASE',url)
        doc=example_library()
        core.ask_llm(provider,'test-model','窓を1ブロック高く',revision_base=doc)
        content=server.RequestHandlerClass.requests[-1]['messages'][1]['content']
        assert '修正指示: 窓を1ブロック高く' in content
        assert json.dumps(doc,ensure_ascii=False,separators=(',',':')) in content
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def mcworld_zip(tmp_path, files=None):
    path=tmp_path/'safe.mcworld'
    source={'level.dat':b'\x00'*20, 'levelname.txt':'Test Terrain World'.encode(),
            'db/CURRENT':b'MANIFEST-000001\n', 'db/MANIFEST-000001':b'test'}
    source.update(files or {})
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for filename, data in source.items(): z.writestr(filename,data)
    return path


def test_mcworld_inspection_only_not_block_data(tmp_path):
    path=mcworld_zip(tmp_path)
    original=path.read_bytes()
    info=inspect_mcworld(path)
    assert info['worldName']=='Test Terrain World'
    assert info['blockStatus']=='NOT_READ'
    assert info['worldWrites']==0
    assert path.read_bytes()==original
    assert not (tmp_path/'db').exists()


@pytest.mark.parametrize('unsafe', ['../escape', '/root/absolute', 'db/../../bad', 'db/CON.txt',
                                      'db/file:alt', 'folder\\evil', 'db/trailing.'])
def test_mcworld_rejects_unsafe_paths(tmp_path,unsafe):
    path=mcworld_zip(tmp_path,{unsafe:b'bad'})
    with pytest.raises(core.StudioError): inspect_mcworld(path)


class FakeBlock:
    namespace='minecraft'
    def __init__(self, name): self.base_name=name


class FakeReader:
    def __init__(self): self.closed=False; self.calls=[]
    def has_chunk(self,x,z,dimension):
        self.calls.append(('has_chunk',x,z,dimension))
        return x==0 and z==0
    def get_block(self,x,y,z,dimension):
        self.calls.append(('get_block',x,y,z,dimension))
        return FakeBlock('grass_block' if y == 64 else 'air')
    def close(self): self.closed=True
    def save(self): raise AssertionError('world save must never be called')


def test_mcworld_adapter_read_from_temporary_copy_and_never_save(tmp_path):
    original=mcworld_zip(tmp_path).read_bytes()
    data=inspect_mcworld(tmp_path/'safe.mcworld')
    level=FakeReader()
    result=read_mcworld_terrain(data,(8,64,8),size=4,height=4,loader=lambda folder:level)
    assert level.closed
    assert result['complete']
    assert result['knownAir']>0
    assert len(result['cells'])==16
    assert all(call[0] in ('has_chunk','get_block') for call in level.calls)
    assert (tmp_path/'safe.mcworld').read_bytes()==original
    assert not (tmp_path/'db').exists()


def test_unknown_chunk_never_assumed_air():
    sample=_sample_reader(FakeReader(),(-1,64,-1),size=4,height=4)
    assert sample['unknown']>0
    assert sample['complete'] is False
    assert sample['knownAir']+sample['unknown']+len(sample['cells'])==64


def test_world_overlay_is_visual_only_and_reports_intersection():
    _, scene, *_ = scenes()
    # Synthetic, explicitly not a real-world sample. The scene anchor itself hits terrain.
    voxel = scene['primitives'][0]
    b=scene['bounds']; anchor=(30,70,30)
    shift=(anchor[0]-(b['minX']+b['maxX'])//2,anchor[1]-b['minY'],anchor[2]-(b['minZ']+b['maxZ'])//2)
    placed=[voxel[0]+shift[0],voxel[1]+shift[1],voxel[2]+shift[2]]
    terrain={'schema':'mab-studio-terrain-snapshot-v1','cells':[[*placed,'minecraft:stone']],
             'unknown':0,'complete':True,'worldWrites':0}
    overlay, report=create_overlay_scene(terrain,scene,anchor)
    assert report['potentialTerrainIntersections']==1
    assert report['placementAuthorized'] is False
    assert report['worldWrites']==0 and overlay['previewOnly']
    assert len(overlay['primitives'])==len(scene['primitives'])


def test_unknown_terrain_overlay_displays_only_known():
    _, scene, *_ = scenes()
    terrain={'schema':'mab-studio-terrain-snapshot-v1','cells':[[1,64,1,'minecraft:grass_block']],
             'unknown':400,'complete':False,'worldWrites':0}
    _, report=create_overlay_scene(terrain,scene,(8,64,8))
    assert report['unreadableCells']==400


def test_terrain_anchor_change_rejected_after_sampling():
    _, scene, *_ = scenes()
    terrain={'schema':'mab-studio-terrain-snapshot-v1','cells':[[8,64,8,'minecraft:stone']],
             'center':[8,64,8], 'unknown':0,'complete':True,'worldWrites':0}
    with pytest.raises(core.StudioError, match='予定位置'):
        create_overlay_scene(terrain,scene,(100,64,100))
