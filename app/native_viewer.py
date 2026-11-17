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
import tkinter as tk
from collections import defaultdict
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


def native_mesh_scene(scene: dict, cut_level: int | None = None):
    """Enforce inert, bounded display input before any rendering operation."""
    validated = compact_display_data(scene, cut_level)
    voxels = validated['voxels']
    visible = [p for p in voxels if cut_level is None or p[1] <= cut_level]
    return validated, visible, exposed_surfaces(visible) if visible else []


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
                    vtkPolyDataMapper, vtkActor, vtkLight)
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
        super().__init__(master, bg='#101a2a', **kw)
        self._vtk = _vtk_runtime()
        self.scene = None
        self.cut_level = None
        self.angle, self.elevation = self.VIEWS['斜め']
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.on_scene_changed = None
        self._after = None
        self._photo = None
        self._surfaces = []
        self._bounds = None
        self._visible_count = 0
        self._rendered_size = (0, 0)
        self._drag = None
        self._closed = False
        self._edge_actor = None
        self._seam_actor = None
        self._floor_actors = []
        self._axes_actor = None
        self._scene_node = None
        self._render = self._vtk['vtkRenderer']()
        self._render.GradientBackgroundOn()
        self._render.SetBackground(0.065, 0.105, 0.19)
        self._render.SetBackground2(0.15, 0.24, 0.36)
        self._window = self._vtk['vtkRenderWindow']()
        self._window.SetOffScreenRendering(True)
        self._window.SetMultiSamples(4)
        self._window.AddRenderer(self._render)
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
        self._image = tk.Label(self, bg='#101a2a', bd=0, highlightthickness=0,
                               takefocus=True, cursor='fleur')
        self._image.pack(fill='both', expand=True)
        self._image.bind('<Configure>', lambda _: self._schedule(140))
        self._image.bind('<ButtonPress-1>', self._start_rotate)
        self._image.bind('<B1-Motion>', self._move_left)
        for button in (2, 3):
            self._image.bind(f'<ButtonPress-{button}>', self._start_pan)
            self._image.bind(f'<B{button}-Motion>', self._move_pan)
        self._image.bind('<ButtonRelease-1>', self._end_drag)
        self._image.bind('<ButtonRelease-2>', self._end_drag)
        self._image.bind('<ButtonRelease-3>', self._end_drag)
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
        self._drag = ('pan' if e.state & 0x0001 else 'rotate', e.x, e.y,
                      self.angle, self.elevation, self.pan_x, self.pan_y)

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
            self.angle = angle + (e.x-x)*.009
            self.elevation = max(-math.radians(88.5), min(math.radians(88.5),
                                 elevation + (e.y-y)*.007))
        self._schedule(25)

    def _end_drag(self, e=None):
        self._drag = None
        self._schedule(20)

    def _zoom_at(self, ratio, cursor):
        old = self.zoom
        self.zoom = max(.20, min(10.0, old*ratio))
        actual = self.zoom / old
        w = max(1, self._image.winfo_width())
        h = max(1, self._image.winfo_height())
        self.pan_x += (1-actual)*(cursor[0] - w/2 - self.pan_x)
        self.pan_y += (1-actual)*(cursor[1] - h/2 - self.pan_y)
        self._schedule(25)

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
        if level is not None and type(level) is not int:
            raise ValueError('Section level must be int or None')
        if self.cut_level == level:
            return
        self.cut_level = level
        self._rebuild()
        if callable(self.on_scene_changed):
            self.on_scene_changed()

    def show(self, scene):
        self.scene = scene
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
        if self._scene_node:
            self._render.RemoveActor(self._scene_node)
            self._scene_node = None
        if self._edge_actor:
            self._render.RemoveActor(self._edge_actor)
            self._edge_actor = None
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
            data, visible, faces = native_mesh_scene(self.scene, self.cut_level)
        except (GPUViewerError, NativeViewerError) as exc:
            self._image.configure(text=f'3D表示を安全に停止しました: {exc}', fg='#ecbec6',
                                  image='', bg='#101a2a')
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
        self._add_mesh(faces, data['palette'])
        self._add_floor_grid()
        self._schedule(40)

    def _add_mesh(self, faces, palette):
        v = self._vtk
        points = v['vtkPoints']()
        poly_cells = v['vtkCellArray']()
        colors = v['vtkUnsignedCharArray']()
        colors.SetNumberOfComponents(3)
        colors.SetName('SOURCE surface RGB')
        # Center-local coordinates maintain GPU float precision for remote XYZ.
        b = self._bounds
        ox, oy, oz = (b[0]+b[1])/2, (b[2]+b[3])/2, (b[4]+b[5])/2
        edge_points = v['vtkPoints']()
        edge_cells = v['vtkCellArray']()
        seen_edges = set()
        for surf in faces:
            idx = [points.InsertNextPoint(x-ox, y-oy, z-oz) for x,y,z in surf.corners]
            poly_cells.InsertNextCell(4)
            for p in idx:
                poly_cells.InsertCellPoint(p)
            color = palette[surf.material] if 0<=surf.material<len(palette) else '#dddddd'
            colors.InsertNextTuple3(*colored_surface(color, surf.normal))
            # Draw only the merged material boundary edges; no fake geometry.
            corners = surf.corners
            for j in range(4):
                a, bpt = corners[j], corners[(j+1)%4]
                identity = tuple(sorted((a, bpt)))
                if identity in seen_edges:
                    continue
                seen_edges.add(identity)
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
        self._add_seams(faces, (ox,oy,oz))

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
        floor_actor.GetProperty().SetColor(.14,.22,.30)
        floor_actor.GetProperty().SetOpacity(.50)
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

    def _schedule(self, ms=30):
        if self._closed:
            return
        if self._after is not None:
            try: self.after_cancel(self._after)
            except tk.TclError: pass
        self._after = self.after(ms, self.draw)

    def draw(self):
        self._after = None
        if self._closed or not self.winfo_exists():
            return
        w, h = max(1, self._image.winfo_width()), max(1, self._image.winfo_height())
        if w < 20 or h < 20:
            return
        if self._bounds is None:
            self._image.configure(text='表示するSOURCE建築がありません' if not self.scene else
                                  'この断面には表示セルがありません', image='',
                                  bg='#101a2a', fg='#b4cee4')
            return
        b = self._bounds
        size_x, size_y, size_z = b[1]-b[0], b[3]-b[2], b[5]-b[4]
        radius = math.sqrt(size_x*size_x+size_y*size_y+size_z*size_z)*.5
        fov = math.radians(34.0)
        aspect = max(.15, w/max(1,h))
        # Bounding-sphere fit in the *narrower* viewport dimension.
        effective_half_angle = math.atan(math.tan(fov/2)*min(1.0,aspect))
        distance = max(6.0, radius*1.16/math.sin(effective_half_angle))
        direction = (math.sin(self.angle)*math.cos(self.elevation), math.sin(self.elevation),
                     math.cos(self.angle)*math.cos(self.elevation))
        # Physical panning: right and camera-up axes, in world units.
        right = (math.cos(self.angle), 0, -math.sin(self.angle))
        up = (-math.sin(self.elevation)*math.sin(self.angle), math.cos(self.elevation),
              -math.sin(self.elevation)*math.cos(self.angle))
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
        self._window.SetSize(w, h)
        self._window.Render()
        capture = self._vtk['vtkWindowToImageFilter']()
        capture.SetInput(self._window)
        capture.SetInputBufferTypeToRGB()
        capture.ReadFrontBufferOff()
        capture.Update()
        pixels = self._vtk['vtk_to_numpy'](capture.GetOutput().GetPointData().GetScalars())
        from PIL import Image
        bitmap = Image.fromarray(pixels.reshape(h, w, 3), 'RGB').transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        photo = self._vtk['ImageTk'].PhotoImage(bitmap, master=self._image)
        self._photo = photo
        self._image.configure(image=photo, text='', bg='#101a2a')
        self._rendered_size = w, h

    def destroy(self):
        self._closed = True
        if self._after is not None:
            try: self.after_cancel(self._after)
            except tk.TclError: pass
            self._after = None
        if hasattr(self, '_window'):
            self._window.Finalize()
        super().destroy()
