"""Browser-free, Tk-embedded native 3D SOURCE preview (optional VTK backend).

VTK renders directly through a local OpenGL context. Its RGB framebuffer is
presented by Tk/Pillow, inside the *existing* Studio window: no browser,
WebView, network requests, imported world, or Minecraft write APIs.

VTK/Pillow are deliberately optional, and are loaded only on native-viewer
activation. Mesh validation uses the same restricted scene projection as the
previous offline renderer, not raw design or model output.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from collections import defaultdict, deque
from dataclasses import dataclass

from app.gpu_viewer import GPUViewerError, compact_display_data
from app.preview import VoxelPreview


class NativeViewerError(RuntimeError):
    pass


def native_dependencies_available() -> bool:
    try:
        import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
        from vtkmodules.vtkRenderingCore import vtkRenderWindow  # noqa: F401
        from vtkmodules.util.numpy_support import vtk_to_numpy  # noqa: F401
        from PIL import Image, ImageTk  # noqa: F401
        return True
    except (ImportError, OSError):
        return False


@dataclass(frozen=True)
class Surface:
    corners: tuple[tuple[float, float, float], ...]
    material: int
    normal: tuple[int, int, int]
    area: int


def exposed_surfaces(voxels: list[list[int]], max_quads: int = 250_000) -> list[Surface]:
    """Greedy merge all *six* SOURCE-exposed face directions, respecting material.

    No hidden faces, phantom blocks, or doubled boundaries. This is a *view*
    mesh, never a build plan. It is camera-independent (full orbit is safe).
    """
    if not isinstance(max_quads, int) or max_quads < 1:
        raise ValueError('Invalid face limit')
    occupied = {(x, y, z): mat for x, y, z, mat in voxels}
    if len(occupied) != len(voxels):
        raise NativeViewerError('NATIVE_DUPLICATE_CELL')
    groups: dict[tuple[int, int, int, int], set[tuple[int, int]]] = defaultdict(set)
    # (axis, normal sign, plane, material) -> (u, v) lattice squares
    for (x, y, z), mat in occupied.items():
        for axis, sign in ((0, -1), (0, 1), (1, -1), (1, 1), (2, -1), (2, 1)):
            delta = [0, 0, 0]
            delta[axis] = sign
            if (x+delta[0], y+delta[1], z+delta[2]) in occupied:
                continue
            coord = (x, y, z)
            u, v = ({0: (y, z), 1: (x, z), 2: (x, y)})[axis]
            groups[(axis, sign, coord[axis]+(sign > 0), mat)].add((u, v))
    result: list[Surface] = []
    for (axis, sign, plane, mat), squares in groups.items():
        remain = squares.copy()
        for u, v in sorted(squares, key=lambda p: (p[1], p[0])):
            if (u, v) not in remain:
                continue
            w = 1
            while (u+w, v) in remain:
                w += 1
            h = 1
            while all((uu, v+h) in remain for uu in range(u, u+w)):
                h += 1
            for vv in range(v, v+h):
                for uu in range(u, u+w):
                    remain.remove((uu, vv))
            if axis == 0:
                verts = ((plane, u, v), (plane, u+w, v),
                         (plane, u+w, v+h), (plane, u, v+h))
                natural_sign = 1
            elif axis == 1:
                verts = ((u, plane, v), (u+w, plane, v),
                         (u+w, plane, v+h), (u, plane, v+h))
                natural_sign = -1
            else:
                verts = ((u, v, plane), (u+w, v, plane),
                         (u+w, v+h, plane), (u, v+h, plane))
                natural_sign = 1
            if natural_sign != sign:
                verts = (verts[0], verts[3], verts[2], verts[1])
            normal = tuple(sign if i == axis else 0 for i in range(3))
            result.append(Surface(verts, mat, normal, w*h))
            if len(result) > max_quads:
                raise NativeViewerError('NATIVE_FACE_BUDGET_EXCEEDED')
    return result



def material_boundary_segments(voxels: list[list[int]]) -> list[tuple]:
    """Exact SOURCE material borders on coplanar *exposed* unit faces.

    Merged quads may meet only along part of an edge (T junction); comparing
    full rectangle edges silently loses these borders. Use unit face lattice
    coordinates to preserve boundaries without adding any Minecraft blocks.
    Returns (start_xyz, end_xyz, face_normal) for each unique coplanar border.
    """
    occupied = {(x, y, z): mat for x, y, z, mat in voxels}
    if len(occupied) != len(voxels):
        raise NativeViewerError('NATIVE_DUPLICATE_CELL')
    faces = {}
    for (x, y, z), material in occupied.items():
        for axis in range(3):
            for sign in (-1, 1):
                neighbor = [x, y, z]
                neighbor[axis] += sign
                if tuple(neighbor) in occupied:
                    continue
                u, v = ((y, z), (x, z), (x, y))[axis]
                plane = (x, y, z)[axis] + (sign > 0)
                faces[(axis, sign, plane, u, v)] = material
    segments = []
    for (axis, sign, plane, u, v), material in faces.items():
        normal = tuple(sign if i == axis else 0 for i in range(3))
        for du, dv in ((1, 0), (0, 1)):
            if faces.get((axis, sign, plane, u+du, v+dv), material) == material:
                continue
            if du:
                a, b = (u+1, v), (u+1, v+1)
            else:
                a, b = (u, v+1), (u+1, v+1)
            def xyz(uv):
                return ((plane, uv[0], uv[1]),
                        (uv[0], plane, uv[1]),
                        (uv[0], uv[1], plane))[axis]
            segments.append((xyz(a), xyz(b), normal))
    return segments


def native_mesh_scene(scene: dict, cut_level: int | None = None,
                      section_axis: str = 'y', section_level: int | None = None,
                      section_side: str = 'low'):
    """Enforce inert, bounded display input before any rendering operation."""
    if section_level is None and cut_level is not None:
        section_axis, section_level = 'y', cut_level
    if section_axis not in ('x', 'y', 'z'):
        raise NativeViewerError('NATIVE_3D_SECTION_AXIS_INVALID')
    if section_side not in ('low', 'high'):
        raise NativeViewerError('NATIVE_3D_SECTION_SIDE_INVALID')
    # Validate the uncut display source once; apply either half-space below.
    validated = compact_display_data(scene)
    voxels = validated['voxels']
    coordinate = {'x': 0, 'y': 1, 'z': 2}[section_axis]
    if section_level is not None:
        bounds_key = section_axis.upper()
        if type(section_level) is not int or not (
                validated['bounds']['min'+bounds_key]-1 <= section_level <=
                validated['bounds']['max'+bounds_key]):
            raise NativeViewerError('NATIVE_3D_SECTION_LEVEL_INVALID')
    if section_level is None:
        visible = voxels
    elif section_side == 'low':
        visible = [p for p in voxels if p[coordinate] <= section_level]
    else:
        visible = [p for p in voxels if p[coordinate] >= section_level]
    return validated, visible, exposed_surfaces(visible) if visible else []


def interactive_frame_size(width: int, height: int, dragging: bool) -> tuple[int, int]:
    """Lower *only* the temporary drag framebuffer; keep the display full-size.

    The SOURCE mesh, camera, section, material data, and resting image remain
    unchanged. This reduces expensive OpenGL readback while orbiting a large
    scene through the VTK -> Pillow -> Tk bridge.
    """
    if not dragging or min(width, height) < 420:
        return width, height
    return max(1, round(width * .72)), max(1, round(height * .72))


def camera_fit_distance(bounds: tuple[int, int, int, int, int, int],
                        direction: tuple[float, float, float],
                        right: tuple[float, float, float],
                        up: tuple[float, float, float],
                        aspect: float, *, fov_degrees: float = 34.0,
                        margin: float = .90) -> float:
    """Fit all eight SOURCE bounds corners to the perspective viewport.

    A circumscribed sphere wastes space for long or shallow buildings. Fit the
    actual oriented bounds in camera space while keeping a small crop margin.
    """
    if not 0.0 < margin < 1.0 or aspect <= 0.0:
        raise ValueError('Invalid camera fit parameters')
    half = ((bounds[1]-bounds[0])*.5,
            (bounds[3]-bounds[2])*.5,
            (bounds[5]-bounds[4])*.5)
    tangent_y = math.tan(math.radians(fov_degrees)*.5)
    tangent_x = tangent_y * aspect

    def dot(a, b):
        return sum(a[i]*b[i] for i in range(3))

    required = 0.0
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                corner = (sx*half[0], sy*half[1], sz*half[2])
                depth = dot(corner, direction)
                horizontal = abs(dot(corner, right))
                vertical = abs(dot(corner, up))
                required = max(required,
                               depth + horizontal/(tangent_x*margin),
                               depth + vertical/(tangent_y*margin))
    return max(6.0, required)


def normal_shade(normal: tuple[int, int, int]) -> float:
    # Intentionally *different* from the old flat palette renderer:
    # directional sun/sky lighting over six side-specific visible surfaces.
    x, y, z = normal
    return {1: 1.12, -1: .49}.get(y, 0) if y else (.81 if x > 0 else .64) if x else (.92 if z > 0 else .70)


def colored_surface(color: str, normal: tuple[int, int, int]) -> tuple[int, int, int]:
    from app.preview import shade
    v = shade(color, normal_shade(normal))
    return tuple(int(v[i:i+2], 16) for i in (1, 3, 5))


def _vtk_runtime():
    """Lazy import: keep the original lightweight Studio usable without VTK."""
    try:
        from vtkmodules.vtkCommonCore import vtkPoints, vtkUnsignedCharArray
        from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
        from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
        from vtkmodules.vtkRenderingCore import (vtkRenderer, vtkRenderWindow,
                    vtkPolyDataMapper, vtkActor, vtkLight, vtkCellPicker)
        import vtkmodules.vtkRenderingOpenGL2  # register OpenGL2 implementation
        from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
        from vtkmodules.util.numpy_support import vtk_to_numpy
        from PIL import Image, ImageTk
    except (ImportError, OSError) as exc:
        raise NativeViewerError('NATIVE_3D_DEPENDENCY_MISSING: VTK と Pillow が必要です') from exc
    return locals()


class NativeVoxelPreview(tk.Frame):
    """Drop-in scene/section/camera counterpart of VoxelPreview; Tk owns UI.

    Render only on scene/camera changes, never in an uncontrolled 60fps loop.
    Offscreen OpenGL bitmap transfer prevents another OS window from opening.
    """
    VIEWS = VoxelPreview.VIEWS
    DETAIL_LIMITS = VoxelPreview.DETAIL_LIMITS

    def __init__(self, master, **kw):
        super().__init__(master, bg='#f2f5f8', **kw)
        self._vtk = _vtk_runtime()
        self.scene = None
        self.cut_level = None
        self.section_axis = 'y'
        self.section_level = None
        self.section_side = 'low'
        self.angle, self.elevation = self.VIEWS['斜め']
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.on_scene_changed = None
        self._after = None
        self._photo = None
        self._surfaces = []
        self._bounds = None
        self._visible_count = 0
        self._rendered_size = (0, 0)  # actual VTK framebuffer size
        self._photo_size = (0, 0)  # displayed Tk image size (full resolution)
        self._interactive_preview = False
        self._last_frame_ms = {}
        self._frame_ms = deque(maxlen=60)
        self._drag = None
        self._closed = False
        self._edge_actor = None
        self._material_actor = None
        self._material_boundary_count = 0
        self._seam_actor = None
        self._floor_actors = []
        self._axes_actor = None
        self._scene_node = None
        self._scene_origin = (0.0, 0.0, 0.0)
        self._selection_actor = None
        self.edit_scope = None
        self.edit_pick_mode = False
        self.on_cell_picked = None
        self._render = self._vtk['vtkRenderer']()
        self._render.GradientBackgroundOn()
        self._render.SetBackground(0.91, 0.935, 0.96)
        self._render.SetBackground2(0.985, 0.99, 1.0)
        self._window = self._vtk['vtkRenderWindow']()
        self._window.SetOffScreenRendering(True)
        self._window.SetMultiSamples(4)
        self._window.AddRenderer(self._render)
        # Reuse the VTK readback pipeline; recreating a filter per orbit frame
        # forces allocation and obscures whether VTK or Tk is the bottleneck.
        self._capture = self._vtk['vtkWindowToImageFilter']()
        self._capture.SetInput(self._window)
        self._capture.SetInputBufferTypeToRGB()
        self._capture.ReadFrontBufferOff()
        self._render.GetActiveCamera().SetViewAngle(34.0)
        # Local directional sky light + diffuse fill. Renderer never touches world.
        for position, intensity, color in (
            ((-18, 32, 23), 0.95, (1.0, 0.95, 0.82)),
            ((23, 12, -16), 0.55, (0.49, 0.71, 1.0)),
        ):
            light = self._vtk['vtkLight']()
            light.SetPositional(False)
            light.SetPosition(*position)
            light.SetFocalPoint(0, 0, 0)
            light.SetColor(*color)
            light.SetIntensity(intensity)
            self._render.AddLight(light)
        self._image = tk.Label(self, bg='#f2f5f8', bd=0, highlightthickness=0,
                               takefocus=True, cursor='fleur')
        self._image.pack(fill='both', expand=True)
        self._image.bind('<Configure>', lambda _: self._schedule(55, coalesce=True))
        self._image.bind('<ButtonPress-1>', self._start_rotate)
        self._image.bind('<B1-Motion>', self._move_left)
        for button in (2, 3):
            self._image.bind(f'<ButtonPress-{button}>', self._start_pan)
            self._image.bind(f'<B{button}-Motion>', self._move_pan)
        self._image.bind('<ButtonRelease-1>', self._end_drag)
        self._image.bind('<ButtonRelease-2>', self._end_drag)
        self._image.bind('<ButtonRelease-3>', self._end_drag)
        # Alt+Tab, popup focus or losing the drag widget can swallow release.
        # Recover full-detail rendering rather than staying in 72% preview.
        self._image.bind('<FocusOut>', self._end_drag)
        self._image.bind('<MouseWheel>', self._wheel)
        self._image.bind('<Button-4>', lambda e: self._zoom_at(1.12, (e.x, e.y)))
        self._image.bind('<Button-5>', lambda e: self._zoom_at(1/1.12, (e.x, e.y)))
        self._image.bind('<Double-Button-1>', lambda _: self.reset_view())
        for key in ('f', 'F'):
            self._image.bind(f'<KeyPress-{key}>', lambda _: self.reset_view())
        for number, view in enumerate(self.VIEWS, 1):
            self._image.bind(f'<KeyPress-{number}>', lambda _, label=view: self.set_view(label))

    def _start_rotate(self, e):
        self._image.focus_set()
        if self.edit_pick_mode and not (e.state & 0x0001):
            self.pick_source_cell(e.x, e.y)
            return 'break'
        self._drag = ('pan' if e.state & 0x0001 else 'rotate', e.x, e.y,
                      self.angle, self.elevation, self.pan_x, self.pan_y)

    def set_edit_pick_mode(self, enabled):
        self.edit_pick_mode = bool(enabled)
        self._image.configure(cursor='crosshair' if self.edit_pick_mode else 'fleur')

    def set_edit_scope(self, scope):
        self.edit_scope = scope
        self._add_edit_scope_overlay()
        self.draw()

    def pick_source_cell(self, x, y):
        """Pick a SOURCE voxel through the existing offscreen VTK scene."""
        if self._scene_node is None or not self.scene:
            return None
        picker = self._vtk['vtkCellPicker']()
        picker.SetTolerance(.002)
        picker.PickFromListOn()
        picker.AddPickList(self._scene_node)
        height = max(1, self._image.winfo_height())
        if not picker.Pick(float(x), float(height-y), 0.0, self._render):
            return None
        if picker.GetActor() != self._scene_node:
            return None
        position = picker.GetPickPosition()
        normal = picker.GetPickNormal()
        coordinate = tuple(math.floor(position[i] + self._scene_origin[i]
                                      - normal[i]*1e-4) for i in range(3))
        voxels = {tuple(cell[:3]) for cell in self.scene.get('primitives', [])}
        if coordinate not in voxels:
            return None
        if callable(self.on_cell_picked):
            self.on_cell_picked(coordinate)
        return coordinate

    def _start_pan(self, e):
        self._image.focus_set()
        self._drag = ('pan', e.x, e.y,
                      self.angle, self.elevation, self.pan_x, self.pan_y)

    def _move_left(self, e):
        self._drag_to(e)

    def _move_pan(self, e):
        self._drag_to(e)

    def _drag_to(self, e):
        if not self._drag:
            return
        mode, x, y, angle, elevation, px, py = self._drag
        if mode == 'pan':
            self.pan_x, self.pan_y = px + e.x-x, py + e.y-y
        else:
            # Keep the grabbed near-side feature under the pointer: screen X
            # motion is opposite the camera azimuth's positive rotation.
            self.angle = angle - (e.x-x)*.009
            self.elevation = max(-math.radians(88.5), min(math.radians(88.5),
                                 elevation + (e.y-y)*.007))
        # Coalesce, do NOT debounce: frequent pointer events must not starve
        # rendering until the mouse stops moving.
        self._schedule(16, coalesce=True)

    def _end_drag(self, e=None):
        if self._drag is None:
            return
        self._drag = None
        # Cancel any pending low-resolution drag frame; finish with full detail.
        self._schedule(12)

    def _zoom_at(self, ratio, cursor):
        old = self.zoom
        self.zoom = max(.20, min(10.0, old*ratio))
        actual = self.zoom / old
        w = max(1, self._image.winfo_width())
        h = max(1, self._image.winfo_height())
        self.pan_x += (1-actual)*(cursor[0] - w/2 - self.pan_x)
        self.pan_y += (1-actual)*(cursor[1] - h/2 - self.pan_y)
        # Scroll wheels/trackpads may send many events per frame; use the
        # latest zoom without postponing the pending render indefinitely.
        self._schedule(16, coalesce=True)

    def _wheel(self, e):
        if e.delta:
            self._zoom_at(1.12**max(-5, min(5, e.delta/120)), (e.x, e.y))

    def set_view(self, name):
        if name not in self.VIEWS:
            raise ValueError('Unknown camera view')
        self.angle, self.elevation = self.VIEWS[name]
        if name == '上面':
            self.elevation = math.radians(89.5)
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.draw()

    def reset_view(self):
        self.set_view('斜め')

    def set_detail(self, value):
        if value not in self.DETAIL_LIMITS:
            raise ValueError('Unknown preview detail level')
        # Exact GPU mesh always; no arbitrary clipping or decimation.
        self._schedule(80)

    def set_cut_level(self, level):
        self.set_section('y', level)

    def set_section(self, axis, level, side='low'):
        axis = str(axis).lower()
        if axis not in ('x', 'y', 'z'):
            raise ValueError('Section axis must be x, y or z')
        side = str(side).lower()
        if side not in ('low', 'high'):
            raise ValueError('Section side must be low or high')
        if level is not None and type(level) is not int:
            raise ValueError('Section level must be int or None')
        if (self.section_axis == axis and self.section_level == level
                and self.section_side == side):
            return
        self.section_axis, self.section_level, self.section_side = axis, level, side
        self.cut_level = level if axis == 'y' else None
        self._rebuild()
        if callable(self.on_scene_changed):
            self.on_scene_changed()

    def view_section_face(self, axis=None, side=None):
        """Face the newly exposed cut surface without changing SOURCE or section."""
        axis = str(axis or self.section_axis).lower()
        side = str(side or self.section_side).lower()
        if axis not in ('x', 'y', 'z') or side not in ('low', 'high'):
            raise ValueError('Invalid section view direction')
        positive = side == 'low'
        if axis == 'z':
            self.angle, self.elevation = (0.0 if positive else math.pi), 0.0
        elif axis == 'x':
            self.angle, self.elevation = (math.pi / 2 if positive else -math.pi / 2), 0.0
        else:
            self.angle, self.elevation = 0.0, math.radians(89.5 if positive else -89.5)
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self._end_drag()
        self.draw()

    def show(self, scene, *, preserve_camera: bool = False):
        self.scene = scene
        # Rebuild the new SOURCE mesh without discarding the user's current
        # zoom/pan during before/after review. No camera retention across
        # unrelated building opens unless the caller explicitly opts in.
        if not preserve_camera:
            self.zoom = 1.0
            self.pan_x = self.pan_y = 0.0
        self._rebuild()
        if callable(self.on_scene_changed):
            self.on_scene_changed()

    def set_edges(self, enabled):
        if self._edge_actor:
            self._edge_actor.SetVisibility(bool(enabled))
        self.draw()

    def _rebuild(self):
        if self._selection_actor:
            self._render.RemoveActor(self._selection_actor)
            self._selection_actor = None
        if self._scene_node:
            self._render.RemoveActor(self._scene_node)
            self._scene_node = None
        if self._edge_actor:
            self._render.RemoveActor(self._edge_actor)
            self._edge_actor = None
        if self._material_actor:
            self._render.RemoveActor(self._material_actor)
            self._material_actor = None
        self._material_boundary_count = 0
        for item in self._floor_actors:
            self._render.RemoveActor(item)
        self._floor_actors.clear()
        if self._seam_actor:
            self._render.RemoveActor(self._seam_actor)
            self._seam_actor = None
        if not self.scene:
            self._surfaces = []
            self._bounds = None
            self._visible_count = 0
            self._schedule(40)
            return
        try:
            data, visible, faces = native_mesh_scene(
                self.scene, section_axis=self.section_axis, section_level=self.section_level,
                section_side=self.section_side)
        except (GPUViewerError, NativeViewerError) as exc:
            self._photo = None
            self._image.configure(text=f'3D表示を安全に停止しました: {exc}', fg='#ecbec6',
                                  image='', bg='#f2f5f8')
            self._bounds = None
            return
        self._surfaces = faces
        self._visible_count = len(visible)
        if not visible:
            self._bounds = None
            self._schedule(40)
            return
        self._bounds = (min(v[0] for v in visible), max(v[0] for v in visible)+1,
                        min(v[1] for v in visible), max(v[1] for v in visible)+1,
                        min(v[2] for v in visible), max(v[2] for v in visible)+1)
        self._add_mesh(faces, data['palette'], visible)
        self._add_floor_grid()
        self._add_edit_scope_overlay()
        self._schedule(40)

    def _add_mesh(self, faces, palette, visible):
        v = self._vtk
        points = v['vtkPoints']()
        poly_cells = v['vtkCellArray']()
        colors = v['vtkUnsignedCharArray']()
        colors.SetNumberOfComponents(3)
        colors.SetName('SOURCE surface RGB')
        # Center-local coordinates maintain GPU float precision for remote XYZ.
        b = self._bounds
        ox, oy, oz = (b[0]+b[1])/2, (b[2]+b[3])/2, (b[4]+b[5])/2
        self._scene_origin = (ox, oy, oz)
        edge_points = v['vtkPoints']()
        edge_cells = v['vtkCellArray']()
        for surf in faces:
            idx = [points.InsertNextPoint(x-ox, y-oy, z-oz) for x,y,z in surf.corners]
            poly_cells.InsertNextCell(4)
            for p in idx:
                poly_cells.InsertCellPoint(p)
            color = palette[surf.material] if 0<=surf.material<len(palette) else '#dddddd'
            colors.InsertNextTuple3(*colored_surface(color, surf.normal))
            # Edge overlays follow SOURCE-exposed surfaces; no fake geometry.
            corners = surf.corners
            for j in range(4):
                a, bpt = corners[j], corners[(j+1)%4]
                off = tuple(n*.012 for n in surf.normal)
                p = edge_points.InsertNextPoint(*(a[i]-(ox,oy,oz)[i]+off[i] for i in range(3)))
                q = edge_points.InsertNextPoint(*(bpt[i]-(ox,oy,oz)[i]+off[i] for i in range(3)))
                edge_cells.InsertNextCell(2)
                edge_cells.InsertCellPoint(p)
                edge_cells.InsertCellPoint(q)
        mesh = v['vtkPolyData']()
        mesh.SetPoints(points)
        mesh.SetPolys(poly_cells)
        mesh.GetCellData().SetScalars(colors)
        normals = v['vtkPolyDataNormals']()
        normals.SetInputData(mesh)
        normals.ComputeCellNormalsOn()
        normals.ComputePointNormalsOff()
        normals.ConsistencyOff()
        normals.Update()
        mapper = v['vtkPolyDataMapper']()
        mapper.SetInputConnection(normals.GetOutputPort())
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToDirectScalars()
        actor = v['vtkActor']()
        actor.SetMapper(mapper)
        actor.GetProperty().SetAmbient(.48)
        actor.GetProperty().SetDiffuse(.65)
        actor.GetProperty().SetSpecular(.22)
        actor.GetProperty().SetSpecularPower(30)
        actor.GetProperty().SetInterpolationToFlat()
        self._render.AddActor(actor)
        self._scene_node = actor
        outlines = v['vtkPolyData']()
        outlines.SetPoints(edge_points)
        outlines.SetLines(edge_cells)
        edge_mapper = v['vtkPolyDataMapper']()
        edge_mapper.SetInputData(outlines)
        edge_actor = v['vtkActor']()
        edge_actor.SetMapper(edge_mapper)
        edge_actor.GetProperty().SetColor(.07,.13,.21)
        edge_actor.GetProperty().SetOpacity(.42)
        edge_actor.GetProperty().SetLineWidth(1.0)
        edge_actor.GetProperty().LightingOff()
        self._render.AddActor(edge_actor)
        self._edge_actor = edge_actor
        # Exact partial-edge boundaries from exposed SOURCE unit faces.
        # This also handles T-junctions created by greedy rectangular merging.
        material_boundaries = material_boundary_segments(visible)
        self._material_boundary_count = len(material_boundaries)
        if material_boundaries:
            accent_points = v['vtkPoints']()
            accent_cells = v['vtkCellArray']()
            for a, bpt, normal in material_boundaries:
                ids = [accent_points.InsertNextPoint(*(
                    point[i]-(ox,oy,oz)[i]+normal[i]*.030 for i in range(3)))
                       for point in (a,bpt)]
                accent_cells.InsertNextCell(2)
                for idx in ids:
                    accent_cells.InsertCellPoint(idx)
            accent = v['vtkPolyData']()
            accent.SetPoints(accent_points)
            accent.SetLines(accent_cells)
            accent_mapper = v['vtkPolyDataMapper']()
            accent_mapper.SetInputData(accent)
            actor = v['vtkActor']()
            actor.SetMapper(accent_mapper)
            actor.GetProperty().SetColor(.025,.065,.11)
            actor.GetProperty().SetOpacity(.75)
            actor.GetProperty().SetLineWidth(1.6)
            actor.GetProperty().LightingOff()
            self._render.AddActor(actor)
            self._material_actor = actor
        self._add_seams(faces, (ox,oy,oz))

    def _add_edit_scope_overlay(self):
        if self._selection_actor:
            self._render.RemoveActor(self._selection_actor)
            self._selection_actor = None
        if not self.edit_scope or self._bounds is None:
            return
        low, high = self.edit_scope.get('min'), self.edit_scope.get('max')
        if (not isinstance(low, list) or len(low) != 3
                or not isinstance(high, list) or len(high) != 3):
            return
        ox, oy, oz = self._scene_origin
        minimum = [low[i] - (ox, oy, oz)[i] - .025 for i in range(3)]
        maximum = [high[i] + 1 - (ox, oy, oz)[i] + .025 for i in range(3)]
        corners = [(minimum[0] if not (bits & 1) else maximum[0],
                    minimum[1] if not (bits & 2) else maximum[1],
                    minimum[2] if not (bits & 4) else maximum[2]) for bits in range(8)]
        points, lines = self._vtk['vtkPoints'](), self._vtk['vtkCellArray']()
        for point in corners:
            points.InsertNextPoint(*point)
        for a, b in ((0,1),(2,3),(4,5),(6,7),(0,2),(1,3),
                     (4,6),(5,7),(0,4),(1,5),(2,6),(3,7)):
            lines.InsertNextCell(2)
            lines.InsertCellPoint(a)
            lines.InsertCellPoint(b)
        mesh = self._vtk['vtkPolyData']()
        mesh.SetPoints(points)
        mesh.SetLines(lines)
        mapper = self._vtk['vtkPolyDataMapper']()
        mapper.SetInputData(mesh)
        actor = self._vtk['vtkActor']()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(1.0, .67, .12)
        actor.GetProperty().SetLineWidth(3.0)
        actor.GetProperty().LightingOff()
        self._render.AddActor(actor)
        self._selection_actor = actor

    def _add_seams(self, faces, offset):
        """Subtle block joints, unlike the previous monolithic flat geometry."""
        v = self._vtk
        pts, lines = v['vtkPoints'](), v['vtkCellArray']()
        area = sum(face.area for face in faces)
        step = 1 if area <= 36000 else (2 if area <= 90000 else 4)
        count = 0
        for face in faces:
            a, b, _, d = face.corners
            w = round(math.dist(a,b))
            h = round(math.dist(a,d))
            if not w or not h:
                continue
            delta_u = tuple((b[i]-a[i])/w for i in range(3))
            delta_v = tuple((d[i]-a[i])/h for i in range(3))
            def add_line(p, q):
                nonlocal count
                if count >= 46000:
                    return
                n = face.normal
                p1 = pts.InsertNextPoint(*(p[i]-offset[i]+n[i]*.020 for i in range(3)))
                p2 = pts.InsertNextPoint(*(q[i]-offset[i]+n[i]*.020 for i in range(3)))
                lines.InsertNextCell(2)
                lines.InsertCellPoint(p1)
                lines.InsertCellPoint(p2)
                count += 1
            for u in range(step, w, step):
                start = tuple(a[i]+delta_u[i]*u for i in range(3))
                end = tuple(start[i]+delta_v[i]*h for i in range(3))
                add_line(start,end)
            for vv in range(step, h, step):
                start = tuple(a[i]+delta_v[i]*vv for i in range(3))
                end = tuple(start[i]+delta_u[i]*w for i in range(3))
                add_line(start,end)
            if count >= 46000:
                break
        if not count:
            return
        grid = v['vtkPolyData']()
        grid.SetPoints(pts)
        grid.SetLines(lines)
        mapper = v['vtkPolyDataMapper']()
        mapper.SetInputData(grid)
        actor = v['vtkActor']()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(.12,.18,.27)
        actor.GetProperty().SetOpacity(.19)
        actor.GetProperty().SetLineWidth(1.0)
        actor.GetProperty().LightingOff()
        self._render.AddActor(actor)
        self._seam_actor = actor

    def _add_floor_grid(self):
        """A visual reference plane below SOURCE bounds, not a constructed floor."""
        b = self._bounds
        if b is None:
            return
        v = self._vtk
        from vtkmodules.vtkFiltersSources import vtkPlaneSource
        minx, maxx = (b[0]-((b[0]+b[1])/2)-3, b[1]-((b[0]+b[1])/2)+3)
        minz, maxz = (b[4]-((b[4]+b[5])/2)-3, b[5]-((b[4]+b[5])/2)+3)
        y = (b[2]-b[3])/2-.18
        ground = vtkPlaneSource()
        ground.SetOrigin(minx,y,minz)
        ground.SetPoint1(maxx,y,minz)
        ground.SetPoint2(minx,y,maxz)
        floor_mapper = v['vtkPolyDataMapper']()
        floor_mapper.SetInputConnection(ground.GetOutputPort())
        floor_actor = v['vtkActor']()
        floor_actor.SetMapper(floor_mapper)
        floor_actor.GetProperty().SetColor(.72,.78,.84)
        floor_actor.GetProperty().SetOpacity(.32)
        floor_actor.GetProperty().LightingOff()
        self._render.AddActor(floor_actor)
        self._floor_actors.append(floor_actor)
        # Bound lines to a predictable number even for immense scenes.
        count_x = maxx-minx
        count_z = maxz-minz
        step = max(1, math.ceil(max(count_x,count_z)/110))
        ps, cells = v['vtkPoints'](), v['vtkCellArray']()
        def line(a,c):
            i = ps.InsertNextPoint(*a)
            j = ps.InsertNextPoint(*c)
            cells.InsertNextCell(2)
            cells.InsertCellPoint(i)
            cells.InsertCellPoint(j)
        n = math.floor(minx)
        while n <= math.ceil(maxx):
            line((n,y+.025,minz),(n,y+.025,maxz))
            n += step
        n = math.floor(minz)
        while n <= math.ceil(maxz):
            line((minx,y+.025,n),(maxx,y+.025,n))
            n += step
        mesh = v['vtkPolyData']()
        mesh.SetPoints(ps)
        mesh.SetLines(cells)
        mapper = v['vtkPolyDataMapper']()
        mapper.SetInputData(mesh)
        actor = v['vtkActor']()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(.40,.59,.70)
        actor.GetProperty().SetOpacity(.23)
        actor.GetProperty().SetLineWidth(.75)
        actor.GetProperty().LightingOff()
        self._render.AddActor(actor)
        self._floor_actors.append(actor)

    def _schedule(self, ms=30, *, coalesce=False):
        if self._closed:
            return
        if self._after is not None:
            if coalesce:
                # Pending frame renders the most recent camera state. Repeated
                # mouse/resize events never postpone it indefinitely.
                return
            try: self.after_cancel(self._after)
            except tk.TclError: pass
        self._after = self.after(ms, self.draw)

    def draw(self):
        # Explicit draw() calls during previews/QA must not leave an older
        # Tk.after callback behind, causing a duplicate costly framebuffer copy.
        pending = self._after
        self._after = None
        if pending is not None:
            try: self.after_cancel(pending)
            except tk.TclError: pass
        if self._closed or not self.winfo_exists():
            return
        w, h = max(1, self._image.winfo_width()), max(1, self._image.winfo_height())
        if w < 20 or h < 20:
            return
        if self._bounds is None:
            self._photo = None
            self._image.configure(text='表示するSOURCE建築がありません' if not self.scene else
                                  'この断面には表示セルがありません', image='',
                                  bg='#f2f5f8', fg='#526174')
            return
        b = self._bounds
        fov = math.radians(34.0)
        aspect = max(.15, w/max(1,h))
        direction = (math.sin(self.angle)*math.cos(self.elevation), math.sin(self.elevation),
                     math.cos(self.angle)*math.cos(self.elevation))
        # Physical panning: right and camera-up axes, in world units.
        right = (math.cos(self.angle), 0, -math.sin(self.angle))
        up = (-math.sin(self.elevation)*math.sin(self.angle), math.cos(self.elevation),
              -math.sin(self.elevation)*math.cos(self.angle))
        distance = camera_fit_distance(b, direction, right, up, aspect,
                                       fov_degrees=math.degrees(fov))
        radius = math.sqrt((b[1]-b[0])**2 + (b[3]-b[2])**2 + (b[5]-b[4])**2)*.5
        pixel_world = (2 * math.tan(math.radians(34/2)) * distance /
                       max(1, h) / self.zoom)
        target = tuple(-right[i]*self.pan_x*pixel_world + up[i]*self.pan_y*pixel_world for i in range(3))
        camera = self._render.GetActiveCamera()
        camera.SetFocalPoint(*target)
        camera.SetPosition(*(target[i] + direction[i]*distance/self.zoom for i in range(3)))
        camera.SetViewUp(*up)
        camera.OrthogonalizeViewUp()
        camera.SetClippingRange(max(.01, distance/self.zoom-radius*1.6),
                                distance/self.zoom+radius*3.0+10)
        rw, rh = interactive_frame_size(w, h, self._drag is not None)
        tick = time.perf_counter()
        if self._rendered_size != (rw, rh):
            self._window.SetSize(rw, rh)
        self._window.Render()
        rendered = time.perf_counter()
        self._capture.Modified()  # VTK must grab the newly rendered framebuffer.
        self._capture.Update()
        pixels = self._vtk['vtk_to_numpy'](self._capture.GetOutput().GetPointData().GetScalars())
        from PIL import Image
        bitmap = Image.fromarray(pixels.reshape(rh, rw, 3), 'RGB').transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if (rw, rh) != (w, h):
            # Stretch just the transient drag image, not the SOURCE geometry.
            # A full-resolution render follows on mouse release.
            bitmap = bitmap.resize((w, h), Image.Resampling.BILINEAR)
        captured = time.perf_counter()
        # Reuse a full-display-size Tk image even when the underlying OpenGL
        # framebuffer changes between drag and idle frames.
        if self._photo is not None and self._photo_size == (w, h):
            self._photo.paste(bitmap)
        else:
            self._photo = self._vtk['ImageTk'].PhotoImage(bitmap, master=self._image)
        self._image.configure(image=self._photo, text='', bg='#f2f5f8')
        presented = time.perf_counter()
        self._rendered_size = rw, rh
        self._photo_size = w, h
        self._interactive_preview = (rw, rh) != (w, h)
        self._last_frame_ms = {'vtkRender': round((rendered-tick)*1000, 2),
                               'readback': round((captured-rendered)*1000, 2),
                               'tkPresentation': round((presented-captured)*1000, 2),
                               'total': round((presented-tick)*1000, 2)}
        self._frame_ms.append(self._last_frame_ms['total'])

    def render_statistics(self) -> dict:
        """Bounded in-memory timings; not FPS, hardware-GPU or quality proof."""
        samples = sorted(self._frame_ms)
        return {'samples': len(samples), 'materialBoundaryEdges': self._material_boundary_count,
                'lastFrameMs': dict(self._last_frame_ms),
                'medianMs': samples[len(samples)//2] if samples else None,
                'p95Ms': samples[min(len(samples)-1, math.ceil(len(samples)*.95)-1)] if samples else None,
                'framebuffer': list(self._rendered_size),
                'displaySize': list(self._photo_size),
                'interactivePreview': self._interactive_preview}

    def renderer_diagnostics(self) -> dict:
        """Report the active OpenGL renderer without inferring from installed GPUs."""
        try:
            capabilities = str(self._window.ReportCapabilities() or '')
        except (AttributeError, RuntimeError):
            capabilities = ''
        wanted = ('vendor', 'renderer', 'version')
        lines = {}
        for line in capabilities.splitlines():
            low = line.lower()
            if any(key in low for key in wanted) and ':' in line:
                label, value = line.split(':', 1)
                lines[label.strip()] = value.strip()[:240]
        try:
            direct = bool(self._window.IsDirect())
        except (AttributeError, RuntimeError):
            direct = None
        renderer = next((v for k, v in lines.items() if 'renderer' in k.lower()), '')
        low_renderer = renderer.lower()
        software = any(token in low_renderer for token in
                       ('llvmpipe', 'softpipe', 'software rasterizer', 'swiftshader',
                        'microsoft basic render', 'gdi generic', 'mesa offscreen'))
        physical_name = any(token in low_renderer for token in
                            ('nvidia', 'geforce', 'quadro', 'amd', 'radeon',
                             'intel arc', 'intel(r) graphics', 'intel(r) uhd', 'intel(r) iris'))
        if software:
            classification = 'SOFTWARE_RENDERER'
        elif direct is True and physical_name:
            classification = 'PHYSICAL_GPU_RENDERER'
        else:
            classification = 'UNKNOWN_RENDERER'
        return {'windowClass': str(self._window.GetClassName()),
                'directContext': direct, 'classification': classification,
                'capabilityStrings': lines}

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        if self._after is not None:
            try: self.after_cancel(self._after)
            except tk.TclError: pass
            self._after = None
        if hasattr(self, '_window'):
            self._window.Finalize()
        # A parent/window test harness may already have cancelled a Tk.after
        # callback from outside this widget. Tk's base destroy assumes every
        # registered Tcl command still exists; omit externally-deleted names.
        if self._tclCommands:
            self._tclCommands = [name for name in self._tclCommands
                                 if self.tk.call('info', 'commands', name)]
        super().destroy()
