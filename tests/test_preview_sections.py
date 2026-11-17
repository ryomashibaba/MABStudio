"""Layer-section visualization checks; real Tk canvas, no SOURCE or world writes."""
import copy
import json
import os
import tkinter as tk
import pytest

from app.preview import VoxelPreview, greedy_visible_mesh, visible_section
from tests.test_gui_review_flow import studio


def test_section_filters_voxel_levels_without_editing_source():
    points=[[x,y,z,0,0] for x in range(5) for y in range(6) for z in range(5)]
    original=copy.deepcopy(points)
    visible=visible_section(points,2)
    assert len(visible)==5*3*5
    assert all(p[1]<=2 for p in visible)
    assert visible_section(points,None) is points
    assert points==original
    # Newly exposed cut top is render-only: it must NOT become source primitives.
    quads=greedy_visible_mesh(visible,1,1)
    top=[quad for quad in quads if all(p[1]==3 for p in quad[0])]
    assert len(top)==1 and top[0][3]==25
    assert len(points)==150
    with pytest.raises(ValueError):
        visible_section(points,2.5)


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk display required')
def test_canvas_full_mesh_section_updates_geometry_and_cache():
    root=tk.Tk()
    try:
        preview=VoxelPreview(root,width=720,height=560)
        preview.pack()
        root.update_idletasks()
        points=[[x,y,z,0,0] for x in range(10) for y in range(10) for z in range(10)]
        scene={'primitives':points,
               'bounds':dict(minX=0,maxX=9,minY=0,maxY=9,minZ=0,maxZ=9)}
        src=json.dumps(scene,sort_keys=True)
        preview.show(scene)
        preview.set_detail('全体メッシュ（境界面）')
        assert sum(q[3] for q in list(preview._mesh_cache.values())[0])==300
        preview.set_cut_level(3)
        assert preview.cut_level==3
        assert sum(q[3] for q in list(preview._mesh_cache.values())[0])==180
        assert json.dumps(scene,sort_keys=True)==src
        preview.set_cut_level(None)
        assert sum(q[3] for q in list(preview._mesh_cache.values())[0])==300
        # A cut below every cell shows no fabricated geometry.
        preview.set_cut_level(-2)
        assert preview.find_all()
        assert not preview._mesh_cache
        assert json.dumps(scene,sort_keys=True)==src
    finally:
        root.destroy()


def test_gui_section_changes_only_rendering_and_preserves_project(studio):
    app,_=studio
    before=app.history.head()
    backup=(app.manager.root/'outputs'/'latest-design.json').read_bytes()
    # Extend only the canvas scene for the visual test (stored SOURCE untouched).
    app.scene['primitives']=[[x,y,z,0,0] for x in range(4) for y in range(1,7) for z in range(4)]
    app.scene['bounds'].update(minY=1,maxY=6,minX=0,maxX=3,minZ=0,maxZ=3)
    app._refresh_view()
    assert app.section_scale.cget('state')=='disabled'
    app.section_enabled.set(True)
    app._section_changed()
    assert app.section_scale.cget('state')=='normal'
    app.section_level.set(3)
    app._section_changed()
    assert app.preview.cut_level==3
    assert len(visible_section(app.preview.scene['primitives'],app.preview.cut_level))==4*3*4
    app.view_mode.set('建築のみ')
    app._refresh_view()
    assert app.preview.cut_level==3
    app.section_enabled.set(False)
    app._section_changed()
    assert app.preview.cut_level is None
    assert app.history.head()['revisionId']==before['revisionId']
    assert (app.manager.root/'outputs'/'latest-design.json').read_bytes()==backup
    assert app.history.head()['worldWrites']==0
