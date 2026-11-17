"""3D renderer exact exposed-face meshing tests, never construction data."""
from app.preview import greedy_visible_mesh, VoxelPreview


def test_cube_exactly_three_face_quads_for_one_camera_quadrant():
    voxels=[[x,y,z,0,0] for x in range(8) for y in range(8) for z in range(8)]
    quads=greedy_visible_mesh(voxels,1,1)
    assert len(quads)==3
    assert sorted(q[3] for q in quads)==[64,64,64]
    assert sum(q[3] for q in quads)==8*8*3
    # All mesh plane vertices correspond to true voxel face bounds.
    assert all(all(all(type(x) is int for x in xyz) for xyz in q[0]) for q in quads)


def test_material_boundary_and_window_not_merged_as_one_surface():
    blocks=[[x,0,z,0,0] for x in range(5) for z in range(5)]
    blocks[12][3]=1
    quads=greedy_visible_mesh(blocks,1,1)
    top=[q for q in quads if all(v[1]==1 for v in q[0])]
    assert sum(q[3] for q in top)==25
    assert len(top)>1
    assert sum(q[3] for q in top if q[1]==1)==1


def test_hidden_top_face_and_cavity_remain_exact():
    voxels=[[x,0,z,0,0] for x in range(4) for z in range(4)]
    voxels += [[x,1,z,0,0] for x in range(4) for z in range(4) if (x,z)!=(1,1)]
    quads=greedy_visible_mesh(voxels,1,1)
    assert sum(q[3] for q in quads if all(v[1]==1 for v in q[0]))==1
    assert sum(q[3] for q in quads if all(v[1]==2 for v in q[0]))==15


def test_meshing_adds_full_geometry_mode_without_changing_legacy_limits():
    assert VoxelPreview.DETAIL_LIMITS['標準（2,300）']==2300
    assert VoxelPreview.DETAIL_LIMITS['全体メッシュ（境界面）'] is None


def test_gui_can_show_large_solid_without_voxel_decimation():
    import os
    import tkinter as tk
    import pytest
    if os.name!='nt' and not os.getenv('DISPLAY'):
        pytest.skip('GUI needs X server')
    root=tk.Tk()
    root.geometry('700x600')
    try:
        canvas=VoxelPreview(root,width=650,height=500)
        canvas.pack()
        root.update_idletasks()
        side=40
        points=[[x,y,z,0,0] for x in range(side) for y in range(side) for z in range(side)]
        scene={'primitives':points,'bounds':{'minX':0,'minY':0,'minZ':0,
               'maxX':side-1,'maxY':side-1,'maxZ':side-1}}
        canvas.show(scene)
        canvas.set_detail('全体メッシュ（境界面）')
        # Three entire visible sides + overlay header/captions, not 2,300 cells.
        assert len(canvas.find_all())<=10
        assert canvas.mesh_mode is True
        assert len(canvas._mesh_cache)==1
        canvas.angle+=.1
        canvas.draw()
        assert len(canvas._mesh_cache)==1  # drag reuses same quadrant mesh
    finally:
        root.destroy()
