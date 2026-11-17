"""Browserless native 3D: no simulation of Minecraft world permissions."""
import copy
import math
import os
import pytest
from tests.test_gui_review_flow import studio

from app.native_viewer import (Surface, exposed_surfaces, native_mesh_scene,
                               colored_surface, native_dependencies_available)


def example():
    return {'primitives': [[0, 0, 0, 1, 99], [1, 0, 0, 1, 77], [0, 1, 0, 3, 0]],
            'previewPalette': ['#aaaaaa', '#b9a999', '#586b81', '#60ddeb'],
            'sourceSha256': 'NEVER_USE_AS_RENDER_INPUT',
            'worldWriteCredential': 'SENSITIVE_DO_NOT_RENDER'}


def test_exact_six_faces_for_solid_box_and_all_outward_normals():
    scene = [[x, y, z, 0] for x in range(7) for y in range(5) for z in range(9)]
    faces = exposed_surfaces(scene)
    assert len(faces) == 6
    assert sum(f.area for f in faces) == 2*(7*5 + 7*9 + 5*9)
    assert {f.normal for f in faces} == {(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)}
    for face in faces:
        a,b,_,d=face.corners
        cross=((b[1]-a[1])*(d[2]-a[2])-(b[2]-a[2])*(d[1]-a[1]),
               (b[2]-a[2])*(d[0]-a[0])-(b[0]-a[0])*(d[2]-a[2]),
               (b[0]-a[0])*(d[1]-a[1])-(b[1]-a[1])*(d[0]-a[0]))
        assert sum(cross[i]*face.normal[i] for i in range(3))>0


def test_material_boundary_preserved_and_cut_is_display_only():
    src = example()
    before=copy.deepcopy(src)
    data,visible,faces=native_mesh_scene(src,cut_level=0)
    assert len(visible)==2 and all(p[1] <= 0 for p in visible)
    assert len(faces)==6  # two merged adjacent same-material cells
    assert len(data['voxels'])==3
    assert src==before
    assert data['palette'][3]=='#60ddeb'


def test_cavity_hidden_shared_faces_and_one_cell():
    assert len(exposed_surfaces([[0,0,0,1]]))==6
    faces=exposed_surfaces([[0,0,0,1], [1,0,0,2]])
    assert sum(f.area for f in faces)==10
    assert len(faces)==10  # different palettes never merged


def test_duplicate_voxels_never_painted_over_and_malformed_palette_rejected():
    from app.native_viewer import NativeViewerError
    with pytest.raises(NativeViewerError, match='DUPLICATE'):
        exposed_surfaces([[0,0,0,0],[0,0,0,1]])
    src=example();src['previewPalette'][0]='not a color'
    with pytest.raises(ValueError):
        native_mesh_scene(src)


def test_shading_is_six_directional_and_does_not_alter_source_color():
    colors = {d: colored_surface('#91a5b9',d) for d in
              [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]}
    assert len(set(colors.values()))==6
    assert all(all(0<=c<=255 for c in rgb) for rgb in colors.values())


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk display unavailable')
def test_native_tk_embedded_renderer_rotation_and_section():
    if not native_dependencies_available():
        pytest.skip('optional VTK and Pillow not installed')
    import tkinter as tk
    from app.native_viewer import NativeVoxelPreview
    root=tk.Tk()
    root.geometry('700x560')
    try:
        widget=NativeVoxelPreview(root)
        widget.pack(fill='both',expand=True)
        root.update()
        scene={'primitives': [[x,y,z,0] for x in range(5) for y in range(5) for z in range(5)],
               'previewPalette': ['#98bcd2']}
        original=copy.deepcopy(scene)
        widget.show(scene)
        widget.draw()
        assert widget._visible_count==125
        assert widget._rendered_size[0]>=650
        for camera in widget.VIEWS:
            widget.set_view(camera)
        widget.set_cut_level(2)
        assert widget._visible_count==75
        assert len(widget._surfaces)==6
        widget.reset_view()
        widget._zoom_at(1.25,(200,200))
        widget.draw()
        assert widget._photo is not None
        assert scene==original
        assert widget._window.GetClassName().endswith('RenderWindow')
    finally:
        root.destroy()

@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk display unavailable')
def test_native_multiple_frames_reuse_capture_and_expose_in_memory_perf():
    if not native_dependencies_available():
        pytest.skip('VTK and Pillow missing')
    import tkinter as tk
    from app.native_viewer import NativeVoxelPreview
    root=tk.Tk()
    root.geometry('580x430')
    try:
        widget=NativeVoxelPreview(root)
        widget.pack(fill='both', expand=True)
        root.update()
        scene={'primitives': [[x,y,z,(x+y+z)%2] for x in range(9)
                              for y in range(6) for z in range(7)],
               'previewPalette':['#c9b397','#548e9e']}
        before=copy.deepcopy(scene)
        widget.show(scene)
        widget.draw()
        capture=widget._capture
        photo=widget._photo
        widget.angle+=.3
        widget.draw()
        assert widget._capture is capture
        assert widget._photo is photo
        assert widget._material_boundary_count > 0
        metrics=widget.render_statistics()
        assert metrics['samples']>=2
        assert metrics['lastFrameMs']['total']>0
        assert metrics['medianMs']>0 and metrics['p95Ms']>0
        assert metrics['framebuffer'][0]>=500
        widget.set_cut_level(2)
        widget.draw()
        assert widget._visible_count==9*3*7
        assert scene==before
    finally:
        root.destroy()


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk display unavailable')
def test_native_popup_uses_same_renderer_and_inherits_camera(studio):
    if not native_dependencies_available():
        pytest.skip('VTK and Pillow missing')
    from app.native_viewer import NativeVoxelPreview
    app, _ = studio
    assert app.preview.scene is not None
    app.open_gpu_preview()
    assert isinstance(app.preview, NativeVoxelPreview)
    app.preview.set_view('右側')
    app.preview.zoom = 1.6
    app.preview.pan_x = 12
    app.preview.pan_y = -8
    app.preview.set_cut_level(1)
    initial = app.history.head()['revisionId']
    app.open_large_preview()
    app.update_idletasks()
    popup = app._large_preview
    assert isinstance(popup, NativeVoxelPreview)
    assert popup.scene is app.preview.scene
    assert (popup.angle, popup.elevation) == (app.preview.angle, app.preview.elevation)
    assert (popup.zoom, popup.pan_x, popup.pan_y) == (1.6, 12, -8)
    assert popup.cut_level==1
    assert popup._image.winfo_width()>100
    popup.draw()
    assert popup._photo is not None
    app.preview.set_cut_level(None)
    assert popup.cut_level is None
    assert app.history.head()['revisionId']==initial
    # Changing renderer with popup open must also change enlarged renderer.
    app.open_gpu_preview()
    from app.preview import VoxelPreview
    assert isinstance(app.preview, VoxelPreview)
    assert isinstance(app._large_preview, VoxelPreview)
    assert app._large_preview.scene is app.preview.scene
    assert app.history.head()['revisionId']==initial
    app._large_preview_window.destroy()
    app._large_preview = app._large_preview_window = None
