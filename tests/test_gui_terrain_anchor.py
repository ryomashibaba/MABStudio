"""Visible overlay must not stay on screen after placement XYZ changes."""
import os
import pytest

from app import main as studio_main
from app.core import BASELINE_SHA, DEFAULT_BRANCH


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='GUI needs display')
def test_editing_anchor_immediately_hides_stale_terrain(tmp_path,monkeypatch):
    class Manager:
        root=tmp_path
        active_sha=None
        active_path=None
        state={'tracked_branch':DEFAULT_BRANCH}
    monkeypatch.setattr(studio_main,'EngineManager',Manager)
    root=studio_main.Studio()
    try:
        root.scene={'primitives':[[0,0,0,0,0]],
                    'bounds':{'minX':0,'maxX':0,'minY':0,'maxY':0,'minZ':0,'maxZ':0}}
        root.terrain={'schema':'mab-studio-terrain-snapshot-v1','center':[0,64,0]}
        root.terrain_report={'previous':'stale'}
        root.view_mode.set('地形＋建築')
        root.preview.show({'primitives':[[88,88,88,0,0]],
                           'bounds':{'minX':88,'maxX':88,'minY':88,'maxY':88,'minZ':88,'maxZ':88}})
        root.place_xyz.set('1 64 1')
        assert root.terrain_report is None
        assert root.preview.scene == root.scene
        assert '古い地形' in root.status.get()
    finally:
        root.destroy()
