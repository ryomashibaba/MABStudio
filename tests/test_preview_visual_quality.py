"""High-clarity renderer tests; SOURCE and Minecraft remain untouched."""
from __future__ import annotations
import json
import math
import os
import tkinter as tk
import pytest
from app.preview import VoxelPreview, projected_camera, scene_bounds
from app.windows_dpi import enable_windows_dpi_awareness
from tests.test_gui_review_flow import studio


def test_projection_fits_actual_projected_extents_not_longest_3d_dimension():
    bounds = dict(minX=0,maxX=9,minY=0,maxY=29,minZ=0,maxZ=9)
    project = projected_camera(bounds,1100,650,3*math.pi/4,math.radians(27))
    projected = [project(x,y,z) for x in (0,10) for y in (0,30) for z in (0,10)]
    xs,ys=zip(*projected)
    assert min(xs)>50 and max(xs)<1050
    assert min(ys)>34 and max(ys)<625
    assert max(ys)-min(ys)>440  # noticeably larger than old tiny 3D view
    center=projected_camera(bounds,1100,650,3*math.pi/4,math.radians(27),zoom=2)
    a=project(0,0,0); b=project(10,0,0)
    c=center(0,0,0); d=center(10,0,0)
    assert math.dist(c,d)==pytest.approx(2*math.dist(a,b))


def test_section_bounds_are_display_only():
    pts = [[0,y,0,1,0] for y in range(20)]
    snapshot=json.dumps(pts)
    assert scene_bounds(pts)['maxY']==19
    assert scene_bounds(pts[:8])['maxY']==7
    assert json.dumps(pts)==snapshot


def test_nonwindows_dpi_setup_is_safe():
    if os.name != 'nt':
        assert enable_windows_dpi_awareness() is False


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk needs desktop')
def test_default_smooth_mesh_larger_and_section_and_popup_sync():
    # Use a plain Tk canvas for camera accuracy. Full Studio popup is tested below.
    root=tk.Tk()
    root.geometry('1100x750')
    try:
        preview=VoxelPreview(root,width=1000,height=650)
        preview.pack(fill='both',expand=True)
        root.update()
        pts=[[x,y,z,0,0] for x in range(12) for y in range(30) for z in range(12)]
        sc={'primitives':pts,'bounds':dict(minX=0,maxX=11,minY=0,maxY=29,minZ=0,maxZ=11)}
        before=json.dumps(sc,sort_keys=True)
        preview.show(sc)
        assert preview.mesh_mode
        geom=preview.bbox('geometry')
        assert geom is not None
        assert geom[3]-geom[1]>430
        # Merging removes thousands of per-block grid lines, not SOURCE.
        assert len(preview.find_withtag('geometry'))<=10
        preview.set_cut_level(6)
        section=preview.bbox('geometry')
        assert section is not None
        assert preview.cut_level==6
        preview.zoom=3
        preview.reset_view()
        assert preview.zoom==1 and preview.pan_x==0
        assert json.dumps(sc,sort_keys=True)==before
        preview.set_detail('標準（2,300）')
        assert not preview.mesh_mode
    finally:
        root.destroy()


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk needs desktop')
def test_studio_popup_does_not_write_project_and_reflects_section(studio):
    app,_=studio
    before=app.history.head()['revisionId']
    assert app.detail_mode.get()=='なめらか（全体）'
    app.open_large_preview()
    app.update_idletasks()
    assert app._large_preview is not None
    assert app._large_preview.scene is app.preview.scene
    app.section_enabled.set(True)
    app._section_changed()
    app.section_level.set(app.section_level.get()-1)
    app._section_changed()
    assert app._large_preview.cut_level==app.preview.cut_level
    app._large_preview_window.destroy()
    assert app.history.head()['revisionId']==before


def test_source_launcher_avoids_stale_packaged_exe():
    from pathlib import Path
    raw=(Path(__file__).resolve().parents[1]/'RUN_STUDIO_SOURCE.bat').read_bytes()
    assert raw.startswith(b'@echo off\r\n')
    assert b'py -3 -m app.main' in raw
    assert b'start_windows.bat' not in raw
    assert b'MABStudio.exe' not in raw


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk needs desktop')
def test_camera_presets_and_zoom_anchor_do_not_change_source():
    root=tk.Tk()
    root.geometry('900x600')
    try:
        preview=VoxelPreview(root,width=880,height=550)
        preview.pack(fill='both',expand=True)
        root.update()
        scene={'primitives':[[x,y,z,(x+z)%2,0]
              for x in range(9) for z in range(6) for y in range(3+(x+z)%5)]}
        original=json.dumps(scene)
        preview.show(scene)
        bounds=scene_bounds(scene['primitives'])
        vertex=(4,5,2)
        before=projected_camera(bounds,preview.winfo_width(),preview.winfo_height(),
                                preview.angle,preview.elevation,preview.zoom,
                                preview.pan_x,preview.pan_y)(*vertex)
        preview._zoom_by(1.12,before)
        after=projected_camera(bounds,preview.winfo_width(),preview.winfo_height(),
                               preview.angle,preview.elevation,preview.zoom,
                               preview.pan_x,preview.pan_y)(*vertex)
        assert after==pytest.approx(before,abs=1e-7)
        for name,(angle,elevation) in preview.VIEWS.items():
            preview.set_view(name)
            assert (preview.angle,preview.elevation)==(angle,elevation)
            assert preview.zoom==1 and preview.pan_x==0 and preview.pan_y==0
            assert preview.bbox('geometry') is not None
        with pytest.raises(ValueError,match='Unknown camera'):
            preview.set_view('other')
        assert json.dumps(scene)==original
    finally:
        root.destroy()


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk needs desktop')
def test_studio_camera_toolbar_and_large_view_inherits_camera(studio):
    app,_=studio
    initial=app.history.head()['revisionId']
    assert set(app.camera_buttons)==set(VoxelPreview.VIEWS)
    app.camera_buttons['上面'].invoke()
    assert app.preview.elevation==math.pi/2
    app.camera_buttons['右側'].invoke()
    assert app.preview.angle==math.pi/2
    app.preview._zoom_by(1.3,(300,280))
    app.open_large_preview()
    assert app._large_preview.angle==app.preview.angle
    assert app._large_preview.zoom==app.preview.zoom
    assert app.history.head()['revisionId']==initial
    app._large_preview_window.destroy()
