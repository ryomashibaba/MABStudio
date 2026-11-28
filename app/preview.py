"""Canvas 3D-ish voxel preview of the source-proven scene. No game connection."""
from __future__ import annotations
import math
import tkinter as tk

PALETTE = ['#9ba4a7', '#e1d8ce', '#718391', '#6dcce2', '#b78b5b', '#ffe18b', '#e88777']


def shade(color: str, factor: float) -> str:
    rgb = [int(color[i:i+2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(c*factor))) for c in rgb)


def visible_section(points: list, max_y: int | None, axis: str = 'y',
                    side: str = 'low') -> list:
    """Display-only cross section, preserving original SOURCE primitives verbatim.

    A newly exposed face at the slice boundary is an *illustrative section face*,
    never evidence of a real roof, floor, Minecraft block, or construction plan.
    """
    if axis not in ('x', 'y', 'z'):
        raise ValueError('Section axis must be x, y or z')
    if side not in ('low', 'high'):
        raise ValueError('Section side must be low or high')
    if max_y is None:
        return points
    if type(max_y) is not int:
        raise ValueError('Section limit must be an integer or None')
    coordinate = {'x': 0, 'y': 1, 'z': 2}[axis]
    if side == 'low':
        return [p for p in points if p[coordinate] <= max_y]
    return [p for p in points if p[coordinate] >= max_y]


def greedy_visible_mesh(points, x_face: int, z_face: int) -> list[tuple]:
    """Create exact, material-aware visible quad union for an orthographic view.

    This is display-only: rectangles cover existing exposed voxel faces only,
    never invent voxels or authorize Minecraft placement. Each tuple contains
    (3D corners, material_index, light_factor, face_tile_area).
    """
    if x_face not in (-1, 1) or z_face not in (-1, 1):
        raise ValueError('Invalid camera quadrant')
    materials = {(p[0],p[1],p[2]):p[3] for p in points}
    # axis, world-plane, material -> lattice of visible unit squares.
    groups = {}
    for (x,y,z), mat in materials.items():
        if (x,y+1,z) not in materials:
            groups.setdefault(('y', y+1, mat), set()).add((x,z))
        if (x+x_face,y,z) not in materials:
            groups.setdefault(('x', x+(x_face>0),mat), set()).add((y,z))
        if (x,y,z+z_face) not in materials:
            groups.setdefault(('z', z+(z_face>0),mat), set()).add((x,y))
    quads=[]
    for (axis, plane, mat), squares in groups.items():
        remaining=squares.copy()
        for u,v in sorted(squares,key=lambda p:(p[1],p[0])):
            if (u,v) not in remaining:
                continue
            width=1
            while (u+width,v) in remaining:
                width+=1
            height=1
            while all((uu,v+height) in remaining for uu in range(u,u+width)):
                height+=1
            for vv in range(v,v+height):
                for uu in range(u,u+width):
                    remaining.remove((uu,vv))
            if axis=='y':
                corners=((u,plane,v),(u+width,plane,v),
                         (u+width,plane,v+height),(u,plane,v+height))
                light=1.12
            elif axis=='x':
                # u=y and v=z, so width extends along y, height along z.
                corners=((plane,u,v),(plane,u+width,v),
                         (plane,u+width,v+height),(plane,u,v+height))
                light=.82
            else:
                corners=((u,v,plane),(u,v+height,plane),
                         (u+width,v+height,plane),(u+width,v,plane))
                light=.69
            quads.append((corners,mat,light,width*height))
    return quads


def camera_depth(x: float, y: float, z: float, angle: float, elevation: float) -> float:
    """Orthographic distance *toward* the viewer (larger means nearer).

    The projection uses horizontal view direction (sin(angle), cos(angle))
    and an above-ground elevation. Sorting only by X/Z, or subtracting Y,
    paints some roof/facade surfaces behind objects that are actually farther.
    """
    return (math.cos(elevation) * (math.sin(angle) * x + math.cos(angle) * z)
            + math.sin(elevation) * y)


def _three_face_solid_box(quads: list[tuple]) -> bool:
    """True only when three complete exposed faces form one rectangular box."""
    if len(quads) != 3:
        return False
    all_vertices = [vertex for quad in quads for vertex in quad[0]]
    low = tuple(min(v[axis] for v in all_vertices) for axis in range(3))
    high = tuple(max(v[axis] for v in all_vertices) for axis in range(3))
    covered_axes = set()
    for corners, _, _, _ in quads:
        stationary = [axis for axis in range(3)
                      if len({v[axis] for v in corners}) == 1]
        if len(stationary) != 1:
            return False
        axis = stationary[0]
        if axis in covered_axes:
            return False
        covered_axes.add(axis)
        if axis == 1 and corners[0][axis] != high[axis]:
            return False  # Top of the bounding box must be visible.
        for other_axis in range(3):
            if other_axis != axis and ({v[other_axis] for v in corners}
                                       != {low[other_axis], high[other_axis]}):
                return False
    return covered_axes == {0, 1, 2}


def ordered_mesh_quads(quads: list[tuple], angle: float, elevation: float,
                       max_quads: int = 15000) -> tuple[list[tuple], str]:
    """Order visible Canvas faces without hiding a nearer roof behind a wall.

    Large greedy rectangles span different depths and must not be sorted by
    centroid alone: subdivide into true 1x1 SOURCE face tiles, then draw from
    far to near. For a solid box, its three faces meet only at edges and may
    stay merged. Extremely large scenes keep a bounded, explicitly labelled
    approximate/partial display; nothing changes the SOURCE or stored design.

    Returns (paintable faces, 'exact' | 'coarse' | 'partial').
    """
    if max_quads < 1:
        raise ValueError('Invalid Canvas face budget')
    dimensions = []
    for quad in quads:
        a, b, _, d = quad[0]
        width = sum(abs(b[i] - a[i]) for i in range(3))
        height = sum(abs(d[i] - a[i]) for i in range(3))
        if width < 1 or height < 1 or width * height != quad[3]:
            raise ValueError('Malformed SOURCE-facing preview rectangle')
        dimensions.append((quad, width, height))

    if _three_face_solid_box(quads):
        tiles = list(quads)
        mode = 'exact'
    else:
        step = 1
        def tile_count(size):
            return sum(math.ceil(w / size) * math.ceil(h / size)
                       for _, w, h in dimensions)

        while tile_count(step) > max_quads and step < 1024:
            step *= 2
        mode = 'exact' if step == 1 else 'coarse'
        tiles = []
        for (corners, material, light, _), width, height in dimensions:
            a, b, _, d = corners
            du = tuple((b[i] - a[i]) // width for i in range(3))
            dv = tuple((d[i] - a[i]) // height for i in range(3))
            def at(u, v):
                return tuple(a[i] + u * du[i] + v * dv[i]
                             for i in range(3))
            for u in range(0, width, step):
                u2 = min(width, u + step)
                for v in range(0, height, step):
                    v2 = min(height, v + step)
                    tiles.append(((at(u, v), at(u2, v),
                                   at(u2, v2), at(u, v2)),
                                  material, light, (u2 - u) * (v2 - v)))

        if len(tiles) > max_quads:
            # More isolated faces than the budget: explicitly mark incomplete.
            tiles.sort(key=lambda q: q[3], reverse=True)
            tiles = tiles[:max_quads]
            mode = 'partial'

    # Precompute the camera vector once: this runs on every mouse-drag frame.
    horizontal = math.cos(elevation)
    dx = horizontal * math.sin(angle)
    dy = math.sin(elevation)
    dz = horizontal * math.cos(angle)
    tiles.sort(key=lambda q: sum(dx*x + dy*y + dz*z for x, y, z in q[0]))
    return tiles, mode


def projected_camera(bounds: dict, w: int, h: int, angle: float,
                     elevation: float, zoom: float = 1.0,
                     pan_x: float = 0.0, pan_y: float = 0.0):
    """Auto-fit true SOURCE bounds, independently of voxel detail sampling.

    Pure display transformation; neither creates voxels nor changes SOURCE.
    Unlike using the longest 3D extent, this fits the actual projected width
    and height (including tall/narrow buildings) to the available viewport.
    """
    if w <= 0 or h <= 0 or not math.isfinite(zoom) or zoom <= 0:
        raise ValueError('Invalid camera viewport')
    ca, sa = math.cos(angle), math.sin(angle)
    sine, cosine = math.sin(elevation), math.cos(elevation)
    mx = (bounds['minX'] + bounds['maxX'] + 1) / 2
    my = (bounds['minY'] + bounds['maxY'] + 1) / 2
    mz = (bounds['minZ'] + bounds['maxZ'] + 1) / 2
    def raw(x, y, z):
        a, b = x-mx, z-mz
        return ca*a-sa*b, sine*(sa*a+ca*b)-cosine*(y-my)
    corners = [raw(x,y,z) for x in (bounds['minX'], bounds['maxX']+1)
                          for y in (bounds['minY'], bounds['maxY']+1)
                          for z in (bounds['minZ'], bounds['maxZ']+1)]
    left, right = min(p[0] for p in corners), max(p[0] for p in corners)
    top, bottom = min(p[1] for p in corners), max(p[1] for p in corners)
    # Reserve space for the visible SOURCE label and camera hint.
    usable_h = max(1, h-65)
    scale = min(w*0.86/max(right-left,1e-8),
                usable_h*0.84/max(bottom-top,1e-8))*zoom
    cx = w/2 - (left+right)*scale/2 + pan_x
    cy = 33 + usable_h/2 - (top+bottom)*scale/2 + pan_y
    def project(x,y,z):
        xx, yy = raw(x,y,z)
        return cx+xx*scale, cy+yy*scale
    return project


def scene_bounds(points: list) -> dict:
    """True visible bounds, so a section view fits the exposed lower floors."""
    return {'minX':min(p[0] for p in points), 'maxX':max(p[0] for p in points),
            'minY':min(p[1] for p in points), 'maxY':max(p[1] for p in points),
            'minZ':min(p[2] for p in points), 'maxZ':max(p[2] for p in points)}


class VoxelPreview(tk.Canvas):
    # Fixed, SOURCE-independent camera orientations. In this viewer +Z is front,
    # +X is right, and +Y is above; these labels describe SOURCE axes only.
    VIEWS = {
        '斜め': (math.pi * 3 / 4, math.radians(27)),
        '正面': (0.0, 0.0),
        '背面': (math.pi, 0.0),
        '左側': (-math.pi / 2, 0.0),
        '右側': (math.pi / 2, 0.0),
        '上面': (0.0, math.pi / 2),
    }
    LIMIT = 2300
    DETAIL_LIMITS = {'なめらか（全体）': None, '標準（2,300）': 2300, '精細（8,000）': 8000,
                     '高精細（15,000）': 15000,
                     '全体メッシュ（境界面）': None}
    def __init__(self, master, **kw):
        super().__init__(master, bg='#f2f5f8', highlightthickness=0, **kw)
        self.scene = None
        self.angle, self.elevation = self.VIEWS['斜め']
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.pressed_at = None
        self.pan_pressed = None
        self._shift_pan = False
        self.show_edges = False
        self.detail_limit = self.LIMIT
        self.mesh_mode = True
        self.cut_level = None  # Visual-only Y upper bound; never modifies the design
        self.section_axis = 'y'
        self.section_level = None
        self.section_side = 'low'
        self._mesh_cache = {}
        self.on_scene_changed = None
        self.bind('<Configure>', lambda e: self.draw())
        self.bind('<ButtonPress-1>', self._start_left_drag)
        self.bind('<B1-Motion>', self._drag)
        self.bind('<ButtonRelease-1>', self._end_drag)
        self.bind('<MouseWheel>', self._wheel)
        self.bind('<Button-4>', lambda e: self._zoom_by(1.12, (e.x, e.y)))
        self.bind('<Button-5>', lambda e: self._zoom_by(1/1.12, (e.x, e.y)))
        for button in (2, 3):
            self.bind(f'<ButtonPress-{button}>', self._start_pan)
            self.bind(f'<B{button}-Motion>', self._pan)
            self.bind(f'<ButtonRelease-{button}>', self._end_drag)
        self.bind('<Double-Button-1>', lambda _: self.reset_view())
        self.bind('<KeyPress-f>', lambda _: self.reset_view())
        self.bind('<KeyPress-F>', lambda _: self.reset_view())

    def _start_left_drag(self, e):
        self.focus_set()
        self._shift_pan = bool(e.state & 0x0001)
        self.pressed_at = (e.x, e.y, self.angle, self.elevation)
        if self._shift_pan:
            self._start_pan(e)

    def _start_pan(self, e):
        self.focus_set()
        self.pan_pressed = (e.x, e.y, self.pan_x, self.pan_y)

    def _end_drag(self, _event=None):
        self.pressed_at = self.pan_pressed = None
        self._shift_pan = False

    def _drag(self, e):
        if self._shift_pan:
            self._pan(e)
        elif self.pressed_at:
            x, y, angle, elevation = self.pressed_at
            # Match the native viewer so a rightward drag moves the near side
            # of the model rightward on screen.
            self.angle = angle - (e.x-x)*0.009
            self.elevation = max(-math.radians(89), min(math.radians(89),
                                  elevation + (e.y-y)*0.006))
            self.draw()

    def _pan(self, e):
        if self.pan_pressed:
            x,y,px,py = self.pan_pressed
            self.pan_x, self.pan_y = px + e.x - x, py + e.y - y
            self.draw()

    def _zoom_by(self, factor, cursor=None):
        """Keep the world point under the cursor stationary when zooming."""
        before = self.zoom
        self.zoom = max(.25, min(6.0, before*factor))
        if cursor is not None and self.zoom != before:
            ratio = self.zoom / before
            w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
            base_x = w / 2
            base_y = 33 + max(1, h-65) / 2
            self.pan_x += (1-ratio)*(cursor[0]-base_x-self.pan_x)
            self.pan_y += (1-ratio)*(cursor[1]-base_y-self.pan_y)
        self.draw()

    def _wheel(self, e):
        if e.delta:
            self._zoom_by(1.12**max(-5, min(5, e.delta/120)), (e.x, e.y))

    def set_view(self, view: str):
        """Select one SOURCE-axis view and auto-fit, without editing scene data."""
        if view not in self.VIEWS:
            raise ValueError('Unknown camera view')
        self.angle, self.elevation = self.VIEWS[view]
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self._end_drag()
        self.draw()

    def reset_view(self):
        """Fit SOURCE geometry in the initial isometric view."""
        self.set_view('斜め')

    def set_edges(self, show: bool) -> None:
        self.show_edges = bool(show)
        self.draw()

    def set_detail(self, label: str) -> None:
        if label not in self.DETAIL_LIMITS:
            raise ValueError('Unknown preview detail level')
        self.mesh_mode = self.DETAIL_LIMITS[label] is None
        self.detail_limit = self.DETAIL_LIMITS[label] if not self.mesh_mode else self.LIMIT
        self.draw()

    def set_cut_level(self, max_y: int | None) -> None:
        """Show only cells at/below a Y level without mutating the SOURCE scene."""
        self.set_section('y', max_y)

    def set_section(self, axis: str, level: int | None, side: str = 'low') -> None:
        axis = str(axis).lower()
        if axis not in ('x', 'y', 'z'):
            raise ValueError('Section axis must be x, y or z')
        side = str(side).lower()
        if side not in ('low', 'high'):
            raise ValueError('Section side must be low or high')
        if level is not None and type(level) is not int:
            raise ValueError('Section level must be integer')
        if (self.section_axis == axis and self.section_level == level
                and self.section_side == side):
            return
        self.section_axis, self.section_level, self.section_side = axis, level, side
        self.cut_level = level if axis == 'y' else None
        self._mesh_cache.clear()
        self.draw()
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
        self.draw()

    def show(self, scene, *, preserve_camera: bool = False):
        self.scene = scene
        self._mesh_cache.clear()
        # Keep the same inspection angle/zoom when comparing an AI draft with
        # its approved parent. Opening a different building still auto-fits.
        if not preserve_camera:
            self.zoom = 1.0
            self.pan_x = self.pan_y = 0.0
        self.draw()
        if callable(self.on_scene_changed):
            self.on_scene_changed()

    def _draw_mesh(self, points, w, h, palette):
        proj=projected_camera(scene_bounds(points), w, h,
                              self.angle, self.elevation, self.zoom,
                              self.pan_x, self.pan_y)
        ca, sa = math.cos(self.angle), math.sin(self.angle)
        sx=1 if sa>0 else -1
        sz=1 if ca>0 else -1
        key=(sx,sz)
        if key not in self._mesh_cache:
            self._mesh_cache[key] = greedy_visible_mesh(points,sx,sz)
        quads=self._mesh_cache[key]
        total_faces=len(quads)
        render_quads, render_mode = ordered_mesh_quads(quads, self.angle, self.elevation)
        for corners,mat,light,area in render_quads:
            color=palette[mat] if 0<=mat<len(palette) else '#dddddd'
            coords=[]
            for xyz in corners:
                coords.extend(proj(*xyz))
            self.create_polygon(*coords,fill=shade(color,light),
                                outline='#65758a' if self.show_edges else '',
                                width=.6 if self.show_edges else 0, tags=('geometry',))
        label=self.scene.get('previewLabel') or f'3D SOURCEプレビュー  {len(points):,}セル'
        label+=f' | 境界面メッシュ {total_faces:,}面'
        if self.section_level is not None:
            op = '≤' if self.section_side == 'low' else '≥'
            label+=f' | {self.section_axis.upper()}{op}{self.section_level} 断面（仮想切断面）'
        if render_mode == 'partial':
            label+='（描画上限・一部省略。GPUで全体を確認）'
        elif render_mode == 'coarse':
            label+='（高速表示・重なりはGPUで確認）'
        self.create_rectangle(0,0,w,29,fill='#e9edf2',outline='')
        self.create_text(13,15,text=label,fill='#17212b',anchor='w',font=('Segoe UI',10))
        self.create_text(w-12,h-10,text='ドラッグ：回転 / Shift・右ドラッグ：移動 / ホイール：拡大 / ダブルクリック：全体',fill='#526174',anchor='e')

    def draw(self):
        self.delete('all')
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        self.create_text(w//2,h//2,fill='#526174',text='生成した建築のプレビューがここに表示されます' if not self.scene else '描画中...')
        if not self.scene:
            return
        pts = visible_section(self.scene.get('primitives') or [], self.section_level,
                              self.section_axis, self.section_side)
        palette = self.scene.get('previewPalette', PALETTE)
        if not pts:
            self.delete('all')
            self.create_text(w//2, h//2, text='この断面に表示するセルはありません。位置を調整してください。',
                             fill='#afc2df', font=('Segoe UI', 11))
            return
        self.delete('all')
        total = len(pts)
        if self.mesh_mode:
            self._draw_mesh(pts,w,h,palette)
            return
        # Occupancy is solely for 3D visibility; construction decisions remain MAB-owned.
        occupied = {(p[0],p[1],p[2]) for p in pts}
        bypos = {(p[0],p[1],p[2]):p[3] for p in pts}
        visible = [p for p in pts if any((p[0]+dx,p[1]+dy,p[2]+dz) not in occupied
                   for dx,dy,dz in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)))]
        limit = self.detail_limit
        skipped = max(0,len(visible)-limit)
        # Decimated preview is explicit, never claimed to show all voxels.
        if skipped:
            priority = {tuple(p) for p in self.scene.get('priorityPositions', [])}
            selected = [p for p in visible if tuple(p[:3]) in priority]
            if len(selected) >= limit:
                visible = selected[:limit]
            else:
                other = [p for p in visible if tuple(p[:3]) not in priority]
                quota = limit - len(selected)
                stride = math.ceil(len(other)/max(1, quota))
                visible = selected + other[::stride][:quota]
        proj=projected_camera(scene_bounds(pts), w, h,
                              self.angle, self.elevation, self.zoom,
                              self.pan_x, self.pan_y)
        ca, sa = math.cos(self.angle), math.sin(self.angle)
        def depth(p):
            x,y,z=p[:3]
            return camera_depth(x+.5, y+.5, z+.5,
                                self.angle, self.elevation)
        # Draw back-to-front, all exposed top and two camera-facing sides.
        cells=sorted(visible,key=depth)
        sx=1 if sa>0 else -1
        sz=1 if ca>0 else -1
        for p in cells:
            x,y,z,mat=p[:4]
            color=palette[mat] if 0<=mat<len(palette) else '#dddddd'
            faces=[]
            if (x,y+1,z) not in occupied:
                faces.append(([(x,y+1,z),(x+1,y+1,z),(x+1,y+1,z+1),(x,y+1,z+1)],1.12))
            if (x+sx,y,z) not in occupied:
                xx=x+1 if sx>0 else x
                faces.append(([(xx,y,z),(xx,y+1,z),(xx,y+1,z+1),(xx,y,z+1)],.82))
            if (x,y,z+sz) not in occupied:
                zz=z+1 if sz>0 else z
                faces.append(([(x,y,zz),(x,y+1,zz),(x+1,y+1,zz),(x+1,y,zz)],.69))
            for corners,light in faces:
                coords=[]
                for xyz in corners: coords.extend(proj(*xyz))
                self.create_polygon(*coords,fill=shade(color,light),
                                    outline='#65758a' if self.show_edges else '',
                                    width=.6 if self.show_edges else 0, tags=('geometry',))
        label=self.scene.get('previewLabel') or f'3D SOURCEプレビュー   {total:,}表示セル'
        if self.section_level is not None:
            op = '≤' if self.section_side == 'low' else '≥'
            label+=f' | {self.section_axis.upper()}{op}{self.section_level} 断面（仮想切断面）'
        if skipped:
            label+=f'  |  簡易描画（可視セル {len(visible):,}/{len(visible)+skipped:,}）'
        self.create_rectangle(0,0,w,29,fill='#e9edf2',outline='')
        self.create_text(13,15,text=label,fill='#17212b',anchor='w',font=('Segoe UI',10))
        self.create_text(w-12,h-10,text='ドラッグ：回転 / Shift・右ドラッグ：移動 / ホイール：拡大 / ダブルクリック：全体',fill='#526174',anchor='e')
