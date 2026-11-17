"""Regression for camera-rotation see-through walls/roofs in Tk SOURCE preview.

These tests use fixed display-only faces, never a model, engine, or world write.
"""
import json
import math
import os

import pytest

from app.preview import (VoxelPreview, camera_depth, greedy_visible_mesh,
                         ordered_mesh_quads)


def _screen(vertex, angle, elevation):
    x, y, z = vertex
    return (math.cos(angle) * x - math.sin(angle) * z,
            math.sin(elevation) * (math.sin(angle) * x + math.cos(angle) * z)
            - math.cos(elevation) * y)


def _point_in_quad(quad, position, angle, elevation):
    # Orthographic projection maps each rectangular face to a convex polygon.
    xy = [_screen(v, angle, elevation) for v in quad[0]]
    signs = []
    for i, point in enumerate(xy):
        next_point = xy[(i + 1) % len(xy)]
        cross = ((next_point[0] - point[0]) * (position[1] - point[1])
                 - (next_point[1] - point[1]) * (position[0] - point[0]))
        if abs(cross) > 1e-7:
            signs.append(cross > 0)
    return bool(signs) and all(value == signs[0] for value in signs)


def test_camera_depth_accounts_for_height_and_camera_side():
    angle = math.pi * 3 / 4
    elevation = math.radians(27)
    assert camera_depth(0, 4, 0, angle, elevation) > camera_depth(0, 0, 0, angle, elevation)
    assert camera_depth(5, 0, -5, angle, elevation) > camera_depth(-5, 0, 5, angle, elevation)


def test_tall_merged_face_does_not_cover_nearer_roof_when_view_rotates():
    # This roof/side pair was drawn in the wrong order by the old "greedy
    # rectangle midpoint" painter. At the given pixel the roof is actually
    # in front of the tall wall even though the wall's midpoint is closer.
    angle = math.pi * 3 / 4
    elevation = math.radians(27)
    roof = (((5, 1, 2), (5, 4, 2), (5, 4, 3), (5, 1, 3)), 0, .82, 3)
    tall_wall = (((3, 1, 4), (3, 20, 4), (4, 20, 4), (4, 1, 4)), 0, .69, 19)
    original = json.dumps([roof, tall_wall])
    tiles, mode = ordered_mesh_quads([roof, tall_wall], angle, elevation)
    assert mode == 'exact' and len(tiles) == 22
    assert sum(tile[3] for tile in tiles) == 22
    pixel = (-5.303300858899107, -1.9064965495108166)
    covering = [tile for tile in tiles if _point_in_quad(tile, pixel, angle, elevation)]
    assert len(covering) == 2
    distances = [sum(camera_depth(*vertex, angle, elevation)
                     for vertex in tile[0]) / 4 for tile in covering]
    assert distances[0] < distances[1]  # Canvas draws the nearer face last.
    assert covering[-1][2] == .82       # Correctly visible near-side material.
    assert json.dumps([roof, tall_wall]) == original


def test_all_quadrants_preserve_exact_exposed_face_area_and_material():
    pts = []
    for x in range(8):
        for z in range(8):
            height = 2 + ((x * 5 + z * 3) % 6)
            for y in range(height):
                pts.append([x, y, z, (x + y + z) % 3, 0])
    original = json.dumps(pts)
    for angle in (math.pi/4, 3*math.pi/4, 5*math.pi/4, 7*math.pi/4):
        x_face = 1 if math.sin(angle) > 0 else -1
        z_face = 1 if math.cos(angle) > 0 else -1
        mesh = greedy_visible_mesh(pts, x_face, z_face)
        faces, mode = ordered_mesh_quads(mesh, angle, math.radians(35))
        assert mode == 'exact'
        assert sum(t[3] for t in faces) == sum(q[3] for q in mesh)
        assert {m for _, m, _, _ in faces} == {m for _, m, _, _ in mesh}
        assert all(t[3] == 1 for t in faces)
        depths = [sum(camera_depth(*v, angle, math.radians(35))
                      for v in face[0])/4 for face in faces]
        assert all(a <= b + 1e-10 for a, b in zip(depths, depths[1:]))
    assert json.dumps(pts) == original


def test_big_solid_cuboid_retains_fast_greedy_three_face_path():
    pts = [[x, y, z, 0, 0] for x in range(40) for y in range(40) for z in range(40)]
    merged = greedy_visible_mesh(pts, 1, 1)
    tiles, mode = ordered_mesh_quads(merged, math.pi/4, math.radians(27))
    assert mode == 'exact'
    assert len(tiles) == 3
    assert sum(tile[3] for tile in tiles) == 40 * 40 * 3


def test_large_render_budget_must_report_approximation_or_partial():
    sample = [(((0, 1, 0), (20, 1, 0), (20, 1, 20), (0, 1, 20)), 0, 1.12, 400),
              (((25, 1, 0), (45, 1, 0), (45, 1, 20), (25, 1, 20)), 1, 1.12, 400)]
    drawn, mode = ordered_mesh_quads(sample, .6, .5, max_quads=30)
    assert mode == 'coarse'
    assert len(drawn) <= 30
    assert sum(f[3] for f in drawn) == 800
    isolated = [(((i, 0, 0), (i+1, 0, 0), (i+1, 0, 1), (i, 0, 1)), 0, .8, 1)
                for i in range(12)]
    drawn, mode = ordered_mesh_quads(isolated, .6, .5, max_quads=6)
    assert mode == 'partial' and len(drawn) == 6


@pytest.mark.skipif(os.name != 'nt' and not os.getenv('DISPLAY'), reason='Tk display required')
def test_canvas_rotates_across_all_quadrants_without_source_modifications():
    import tkinter as tk
    root = tk.Tk()
    root.geometry('830x580')
    try:
        canvas = VoxelPreview(root, width=800, height=540)
        canvas.pack(fill='both', expand=True)
        root.update_idletasks()
        pts = [[x, y, z, (x+y+z)%3, 0]
               for x in range(10) for z in range(10)
               for y in range(2 + (x*3+z*7)%12)]
        scene = {'primitives':pts, 'previewLabel':'回転テスト'}
        original = json.dumps(scene, ensure_ascii=False)
        canvas.show(scene)
        for angle in (math.pi/4, 3*math.pi/4, 5*math.pi/4, 7*math.pi/4):
            canvas.angle = angle
            canvas.draw()
            assert len(canvas.find_withtag('geometry')) > 100
            assert canvas.bbox('geometry') is not None
            assert len(canvas._mesh_cache) <= 4
        assert json.dumps(scene, ensure_ascii=False) == original
    finally:
        root.destroy()
