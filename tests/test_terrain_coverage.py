"""Terrain overlay coverage is observation-backed, never placement authority."""
from copy import deepcopy
import pytest

from app.core import StudioError
from app.terrain import _sample_reader, create_overlay_scene


class Block:
    namespace='minecraft'
    def __init__(self,name):self.base_name=name


class Reader:
    def has_chunk(self,x,z,dimension):return x==0 and z==0
    def get_block(self,x,y,z,dimension):
        if (x,y,z)==(8,64,8): return Block('stone')
        if (x,y,z)==(9,64,8): raise RuntimeError('corrupt block')
        return Block('air')


def model(points):
    xs=[p[0] for p in points];ys=[p[1] for p in points];zs=[p[2] for p in points]
    return dict(designOnly=True,readiness='NOT_PREPARED',worldCoordinates=None,
                worldContacts=0,BuildPlan={'operations':[]},
                layerNames=['stone'],stats={'voxels':len(points),'constructionApproved':False},
                sourceSha256='a'*64,geometrySha256='b'*64,
                bounds=dict(minX=min(xs),maxX=max(xs),minY=min(ys),maxY=max(ys),
                            minZ=min(zs),maxZ=max(zs)),
                primitives=[[x,y,z,0,0] for x,y,z in points])


def test_exact_projected_coverage_separates_solid_air_unknown_and_outside():
    terrain=_sample_reader(Reader(),(8,64,8),size=4,height=4)
    assert terrain['unknown']==1 and terrain['knownAir']==62
    assert len(terrain['cells'])==1
    # All 4 model blocks project at y=64 to (7..10, 64, 8).
    scene=model([(0,0,0),(1,0,0),(2,0,0),(3,0,0)])
    image, report=create_overlay_scene(terrain,scene,(8,64,8))
    assert image['previewOnly']
    # 7 SOURCE colors, 10 terrain colors, then overlap/unknown/outside.
    assert len(image['previewPalette'])==21
    assert [c[3] for c in image['primitives']]==[0,17,18,19]
    assert report['potentialTerrainIntersections']==1
    assert report['projectedKnownAir']==1
    assert report['projectedUnreadable']==1
    assert report['projectedOutsideSample']==1
    assert report['projectedLegacyUnverified']==0
    assert not report['coverageFullyObserved'] and not report['placementAuthorized']
    assert sum(report[k] for k in ('potentialTerrainIntersections','projectedKnownAir',
        'projectedUnreadable','projectedOutsideSample')) == report['projectedSourceVoxels']


def test_partition_is_tamper_evident_and_incomplete_solid_overlap_rejected():
    terrain=_sample_reader(Reader(),(8,64,8),size=4,height=4)
    scene=model([(0,0,0)])
    variations=[]
    t=deepcopy(terrain);t['knownAirPositions'].append(t['cells'][0][:3]); variations.append(t)
    t=deepcopy(terrain);t['unknownPositions'].clear(); variations.append(t)
    t=deepcopy(terrain);t['knownAirPositions'][0]=[999,64,999];variations.append(t)
    t=deepcopy(terrain);t['cells'].clear();variations.append(t)
    t=deepcopy(terrain);t['sampled']+=1;variations.append(t)
    t=deepcopy(terrain);t['origin'][0]+=1;variations.append(t)
    for altered in variations:
        with pytest.raises(StudioError):create_overlay_scene(altered,scene,(8,64,8))


def test_legacy_snapshot_cannot_claim_unsampled_air():
    legacy=dict(schema='mab-studio-terrain-snapshot-v1',cells=[[8,64,8,'minecraft:stone']],
                unknown=0,complete=True,worldWrites=0)
    scene=model([(0,0,0),(1,0,0)])
    _,report=create_overlay_scene(legacy,scene,(8,64,8))
    assert report['projectedKnownAir']==0
    assert report['projectedLegacyUnverified'] >= 1
    assert not report['coverageFullyObserved']


def test_missing_chunk_has_explicit_coordinates_never_becomes_air():
    terrain=_sample_reader(Reader(),(-1,64,-1),size=4,height=4)
    assert terrain['unknown']>0
    assert terrain['unknown']==len(terrain['unknownPositions'])
    assert terrain['knownAir']+terrain['unknown']+len(terrain['cells'])==terrain['sampled']


def test_planned_anchor_is_distinct_from_vertical_sample_center(tmp_path):
    import zipfile
    from app.terrain import inspect_mcworld, read_mcworld_terrain
    archive=tmp_path/'export.mcworld'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('level.dat',b'fake')
        z.writestr('db/CURRENT',b'fake')
    snapshot=inspect_mcworld(archive)
    class Level(Reader):
        def close(self):pass
        def save(self):raise AssertionError('World save forbidden')
    terrain=read_mcworld_terrain(snapshot,(8,76,8),size=4,height=4,
              loader=lambda _path:Level(), planned_anchor=(8,64,8))
    assert terrain['center']==[8,76,8]
    assert terrain['plannedAnchor']==[8,64,8]
    model_doc=model([(0,0,0)])
    _,report=create_overlay_scene(terrain,model_doc,(8,64,8))
    assert report['projectedOutsideSample']==1
    with pytest.raises(StudioError,match='予定位置'):
        create_overlay_scene(terrain,model_doc,(8,65,8))
