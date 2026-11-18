"""Browserless native 3D: no simulation of Minecraft world permissions."""
import copy
import math
import os
import pytest
from tests.test_gui_review_flow import studio
from app.preview import visible_section

from app.native_viewer import (Surface, exposed_surfaces, native_mesh_scene,
                               colored_surface, native_dependencies_available,
                               interactive_frame_size, NativeVoxelPreview)


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


def test_vertical_sections_select_axis_without_changing_source():
    src=example()
    before=copy.deepcopy(src)
    _,visible_x,_=native_mesh_scene(src,section_axis='x',section_level=0)
    _,visible_z,_=native_mesh_scene(src,section_axis='z',section_level=0)
    assert [p[:3] for p in visible_x]==[[0,0,0],[0,1,0]]
    assert [p[:3] for p in visible_z]==[[0,0,0],[1,0,0],[0,1,0]]
    assert src==before


def test_section_can_keep_either_side_of_plane_without_changing_source():
    src=example()
    before=copy.deepcopy(src)
    _,low,_=native_mesh_scene(src,section_axis='x',section_level=0,section_side='low')
    _,high,_=native_mesh_scene(src,section_axis='x',section_level=0,section_side='high')
    assert {tuple(p[:3]) for p in low}=={(0,0,0),(0,1,0)}
    assert {tuple(p[:3]) for p in high}=={(0,0,0),(1,0,0),(0,1,0)}
    assert src==before
    assert {tuple(p[:3]) for p in visible_section(src['primitives'],0,'x','low')} == {
        (0,0,0),(0,1,0)}
    assert {tuple(p[:3]) for p in visible_section(src['primitives'],0,'x','high')} == {
        (0,0,0),(1,0,0),(0,1,0)}


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


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk display unavailable')
def test_vertical_section_and_cut_face_sync_between_main_and_large_view(studio):
    app,_=studio
    if native_dependencies_available():
        app.open_gpu_preview()
    source=copy.deepcopy(app.scene)
    app.section_enabled.set(True)
    app.section_axis.set('Y')
    app.section_level.set(0)
    app.section_axis.set('Z')
    app._sync_section_controls()
    mid=(app.scene['bounds']['minZ']+app.scene['bounds']['maxZ'])//2
    assert app.section_level.get()==mid
    app._section_changed()
    expected=sum(p[2]<=mid for p in app.scene['primitives'])
    if hasattr(app.preview,'_visible_count'):
        assert app.preview._visible_count==expected
    else:
        assert len(visible_section(app.scene['primitives'],mid,'z','low'))==expected
    app.open_large_preview();app.update_idletasks()
    popup=app._large_preview
    assert popup.section_axis=='z'
    assert popup.section_level==mid
    app.section_side.set('高い側')
    app._section_changed()
    assert popup.section_side=='high'
    app._view_section_face()
    assert (app.preview.angle,app.preview.elevation)==(math.pi,0.0)
    assert (popup.angle,popup.elevation)==(math.pi,0.0)
    app.section_axis.set('X')
    app.section_level.set((app.scene['bounds']['minX']+app.scene['bounds']['maxX'])//2)
    app._section_changed()
    assert popup.section_axis=='x'
    assert popup.section_level==app.section_level.get()
    assert popup.section_side=='high'
    assert app.scene==source
    assert app.history.head()['revisionId']==app.current_snapshot['revisionId']
    app._large_preview_window.destroy()
    app._large_preview=app._large_preview_window=None


def test_material_boundaries_include_partial_edges_inside_merged_faces():
    """A small contrasting square inside a large surface has four real edges.

    Greedy rectangles may have T junctions, so comparing only identical merged
    quad endpoints would miss some or all of these exposed material seams.
    """
    from app.native_viewer import material_boundary_segments
    cells = [[x, 0, z, int(x == 1 and z == 1)]
             for x in range(4) for z in range(4)]
    before = copy.deepcopy(cells)
    boundaries = material_boundary_segments(cells)
    assert len([item for item in boundaries if item[2] == (0, 1, 0)]) == 4
    assert len([item for item in boundaries if item[2] == (0, -1, 0)]) == 4
    assert len(boundaries) == 8
    assert cells == before
    assert material_boundary_segments([[x, 0, z, 0]
                                      for x in range(4) for z in range(4)]) == []


def test_extended_native_qa_fixture_is_pure_synthetic_and_exact_size():
    from app.native_selfcheck import _extended_scene
    scene = _extended_scene()
    assert len(scene['primitives']) == 3128
    assert len({(x,y,z) for x,y,z,_ in scene['primitives']}) == 3128
    assert len(scene['previewPalette']) == 11
    assert not any(k in scene for k in ('model', 'design', 'world', 'credentials'))


def test_interactive_frame_budget_keeps_small_viewers_crisp_and_inputs_bounded():
    assert interactive_frame_size(960, 730, False) == (960, 730)
    assert interactive_frame_size(960, 730, True) == (691, 526)
    assert interactive_frame_size(480, 400, True) == (480, 400)
    assert interactive_frame_size(1920, 1080, True) == (1382, 778)


def test_mouse_motion_coalesces_without_ever_debouncing_indefinitely():
    class Scheduled:
        _closed = False
        _after = None
        def __init__(self):
            self.queue = {}
            self.created = 0
            self.cancelled = []
        def after(self, ms, callback):
            self.created += 1
            name = str(self.created)
            self.queue[name] = (ms, callback)
            return name
        def after_cancel(self, name):
            self.cancelled.append(name)
            self.queue.pop(name, None)
        def draw(self):
            self._after = None

    fake = Scheduled()
    # Rapid mouse events must keep the original 16ms slot instead of
    # cancelling and restarting it 120 times per second.
    for i in range(120):
        NativeVoxelPreview._schedule(fake, 16, coalesce=True)
    assert fake.created == 1 and fake.cancelled == []
    assert fake.queue['1'][0] == 16
    # Releasing the mouse preempts the low-resolution pending draw.
    NativeVoxelPreview._schedule(fake, 12)
    assert fake.created == 2 and fake.cancelled == ['1']
    fake.queue['2'][1]()
    NativeVoxelPreview._schedule(fake, 16, coalesce=True)
    assert fake.created == 3


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk display unavailable')
def test_native_drag_frame_reduced_then_restored_without_changing_source():
    if not native_dependencies_available():
        pytest.skip('VTK and Pillow unavailable')
    from types import SimpleNamespace
    import tkinter as tk
    root = tk.Tk()
    root.geometry('800x600')
    widget = NativeVoxelPreview(root)
    widget.pack(fill='both', expand=True)
    try:
        root.update()
        scene={'primitives': [[x, y, z, (x+z)%2] for x in range(8)
                              for y in range(5) for z in range(9)],
               'previewPalette': ['#b1a18e', '#65879c']}
        snapshot=copy.deepcopy(scene)
        widget.show(scene)
        widget.draw()
        full_size=widget._rendered_size
        full_photo=widget._photo
        assert full_size==(800,600)
        widget._start_rotate(SimpleNamespace(x=100,y=100,state=0))
        widget._drag_to(SimpleNamespace(x=140,y=125))
        widget.draw()
        assert widget._interactive_preview
        assert widget._rendered_size[0]<full_size[0]
        assert widget._rendered_size[1]<full_size[1]
        assert widget._photo_size==full_size
        assert widget._photo is full_photo
        widget._end_drag()
        widget.draw()
        assert widget._rendered_size==full_size
        assert widget._photo_size==full_size
        assert not widget._interactive_preview
        assert widget._photo is full_photo
        assert scene==snapshot
    finally:
        root.destroy()

@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk display unavailable')
def test_preview_switch_rolls_back_if_first_visible_frame_fails(studio, monkeypatch):
    """A bad Windows GPU initialization must not destroy the usable preview.

    The failing new widget needs to survive its initial *hidden* draws, then
    fail after packing, just like a driver failing to present the first frame.
    """
    from app import main as studio_main
    from app.preview import VoxelPreview
    app, _ = studio
    old = app.preview
    source = copy.deepcopy(old.scene)
    original_revision = app.history.head()['revisionId']
    old.set_view('右側')
    old.zoom, old.pan_x, old.pan_y = 1.75, 26, -13
    app.open_large_preview()
    popup = app._large_preview
    errors = []

    class BrokenNative(VoxelPreview):
        def draw(self):
            if self.winfo_manager() == 'pack':
                raise RuntimeError('GPU_FIRST_FRAME_FAILED_TEST')
            return super().draw()

    monkeypatch.setattr(studio_main, 'native_dependencies_available', lambda: True)
    monkeypatch.setattr(studio_main, 'NativeVoxelPreview', BrokenNative)
    monkeypatch.setattr(studio_main.messagebox, 'showerror', lambda *args: errors.append(args))
    app.open_gpu_preview()
    assert app.preview is old and old.winfo_exists() and old.winfo_manager() == 'pack'
    assert len(app.viewer_host.winfo_children()) == 1
    assert app._large_preview is popup  # no invisible popup replacement
    assert (old.zoom, old.pan_x, old.pan_y) == (1.75, 26, -13)
    assert old.scene == source
    assert app.history.head()['revisionId'] == original_revision
    assert len(errors) == 1 and 'GPU_FIRST_FRAME_FAILED_TEST' in errors[0][1]
    app._large_preview_window.destroy()
    app._large_preview_window = app._large_preview = None


@pytest.mark.skipif(os.name!='nt' and not os.getenv('DISPLAY'),reason='Tk display unavailable')
def test_focus_loss_after_native_drag_restores_full_quality():
    if not native_dependencies_available():
        pytest.skip('VTK and Pillow unavailable')
    from types import SimpleNamespace
    import tkinter as tk
    root = tk.Tk()
    root.geometry('730x530')
    widget = NativeVoxelPreview(root)
    widget.pack(fill='both', expand=True)
    try:
        root.update()
        scene={'primitives': [[x,y,z,(x+z)%2] for x in range(8)
                              for y in range(4) for z in range(8)],
               'previewPalette': ['#b1a18e','#65879c']}
        widget.show(scene)
        widget.draw()
        full = widget._rendered_size
        widget._start_rotate(SimpleNamespace(x=100,y=90,state=0))
        widget._drag_to(SimpleNamespace(x=132,y=121))
        widget.draw()
        assert widget._interactive_preview and widget._drag is not None
        assert widget._image.bind('<FocusOut>')
        # The same handler is bound to actual focus loss (Alt+Tab/window switch).
        widget._end_drag(SimpleNamespace())
        widget.draw()
        assert widget._drag is None and not widget._interactive_preview
        assert widget._rendered_size == full
        assert len(scene['primitives']) == 256
    finally:
        root.destroy()
