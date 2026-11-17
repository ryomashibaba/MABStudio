"""Canvas 3D-ish voxel preview of the source-proven scene. No game connection."""
from __future__ import annotations
import math
import tkinter as tk

PALETTE = ['#9ba4a7', '#e1d8ce', '#718391', '#6dcce2', '#b78b5b', '#ffe18b', '#e88777']


def shade(color: str, factor: float) -> str:
    rgb = [int(color[i:i+2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(c*factor))) for c in rgb)


def visible_section(points: list, max_y: int | None) -> list:
    """Display-only cross section, preserving original SOURCE primitives verbatim.

    A newly exposed face at the slice boundary is an *illustrative section face*,
    never evidence of a real roof, floor, Minecraft block, or construction plan.
    """
    if max_y is None:
        return points
    if type(max_y) is not int:
        raise ValueError('Section limit must be an integer or None')
    return [p for p in points if p[1] <= max_y]


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
    LIMIT = 2300
    DETAIL_LIMITS = {'なめらか（全体）': None, '標準（2,300）': 2300, '精細（8,000）': 8000,
                     '高精細（15,000）': 15000,
                     '全体メッシュ（境界面）': None}
    def __init__(self, master, **kw):
        super().__init__(master, bg='#101b2b', highlightthickness=0, **kw)
        self.scene = None
        self.angle = math.pi * 3 / 4
        self.zoom = 1.0
        self.elevation = math.radians(27)
        self.pan_x = self.pan_y = 0.0
        self.pressed_at = None
        self.pan_pressed = None
        self.show_edges = False
        self.detail_limit = self.LIMIT
        self.mesh_mode = True
        self.cut_level = None  # Visual-only Y upper bound; never modifies the design
        self._mesh_cache = {}
        self.on_scene_changed = None
        self.bind('<Configure>', lambda e: self.draw())
        self.bind('<ButtonPress-1>', lambda e: setattr(self, 'pressed_at', (e.x, e.y, self.angle, self.elevation)))
        self.bind('<B1-Motion>', self._drag)
        self.bind('<MouseWheel>', self._wheel)
        self.bind('<Button-4>', lambda e: self._zoom_by(1.12))
        self.bind('<Button-5>', lambda e: self._zoom_by(1/1.12))
        self.bind('<ButtonPress-2>', lambda e: setattr(self,'pan_pressed',(e.x,e.y,self.pan_x,self.pan_y)))
        self.bind('<B2-Motion>', self._pan)
        self.bind('<Double-Button-1>', lambda _: self.reset_view())

    def _drag(self, e):
        if self.pressed_at:
            x, y, angle, elevation = self.pressed_at
            self.angle = angle + (e.x-x)*0.009
            self.elevation = max(math.radians(12), min(math.radians(74),
                                  elevation + (e.y-y)*0.006))
            self.draw()

    def _pan(self, e):
        if self.pan_pressed:
            x,y,px,py = self.pan_pressed
            self.pan_x, self.pan_y = px + e.x - x, py + e.y - y
            self.draw()

    def _zoom_by(self, factor):
        self.zoom = max(.25, min(6.0, self.zoom*factor))
        self.draw()

    def _wheel(self, e):
        self._zoom_by(1.12 if e.delta > 0 else 1/1.12)

    def reset_view(self):
        """Fit SOURCE geometry in the viewport; no SOURCE edits."""
        self.zoom = 1.0
        self.angle = math.pi*3/4
        self.elevation = math.radians(27)
        self.pan_x = self.pan_y = 0.0
        self.draw()

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
        if max_y is not None and type(max_y) is not int:
            raise ValueError('Section level must be integer')
        if self.cut_level == max_y:
            return
        self.cut_level = max_y
        self._mesh_cache.clear()
        self.draw()
        if callable(self.on_scene_changed):
            self.on_scene_changed()

    def show(self, scene):
        self.scene = scene
        self._mesh_cache.clear()
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
        # Large scenes are bounded; never describe a sampled rendering as full.
        render_limit=15000
        if len(quads)>render_limit:
            quads=sorted(quads,key=lambda q:q[3],reverse=True)[:render_limit]
        def depth(q):
            corners=q[0]
            x=sum(p[0] for p in corners)/4
            y=sum(p[1] for p in corners)/4
            z=sum(p[2] for p in corners)/4
            return sa*x+ca*z-.1*y
        quads=sorted(quads,key=depth)
        for corners,mat,light,area in quads:
            color=palette[mat] if 0<=mat<len(palette) else '#dddddd'
            coords=[]
            for xyz in corners:
                coords.extend(proj(*xyz))
            self.create_polygon(*coords,fill=shade(color,light),
                                outline='#243c55' if self.show_edges else '',
                                width=.6 if self.show_edges else 0, tags=('geometry',))
        label=self.scene.get('previewLabel') or f'3D SOURCEプレビュー  {len(points):,}セル'
        label+=f' | 境界面メッシュ {len(quads):,}/{total_faces:,}面'
        if self.cut_level is not None:
            label+=f' | Y≤{self.cut_level} 断面（仮想切断面）'
        if total_faces>render_limit:
            label+='（描画上限。全形状は表示されていません）'
        self.create_rectangle(0,0,w,29,fill='#192a40',outline='')
        self.create_text(13,15,text=label,fill='#e4efff',anchor='w',font=('Segoe UI',10))
        self.create_text(w-12,h-10,text='ドラッグ：回転・上下 / ホイール：拡大 / ダブルクリック：全体表示',fill='#91a4c4',anchor='e')

    def draw(self):
        self.delete('all')
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        self.create_text(w//2,h//2,fill='#91a4c4',text='生成した建築のプレビューがここに表示されます' if not self.scene else '描画中...')
        if not self.scene:
            return
        pts = visible_section(self.scene.get('primitives') or [], self.cut_level)
        palette = self.scene.get('previewPalette', PALETTE)
        if not pts:
            self.delete('all')
            self.create_text(w//2, h//2, text='このY断面に表示するセルはありません。上限を上げてください。',
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
            return sa*x+ca*z-0.1*y
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
                                    outline='#243c55' if self.show_edges else '',
                                    width=.6 if self.show_edges else 0, tags=('geometry',))
        label=self.scene.get('previewLabel') or f'3D SOURCEプレビュー   {total:,}表示セル'
        if self.cut_level is not None:
            label+=f' | Y≤{self.cut_level} 断面（仮想切断面）'
        if skipped:
            label+=f'  |  簡易描画（可視セル {len(visible):,}/{len(visible)+skipped:,}）'
        self.create_rectangle(0,0,w,29,fill='#192a40',outline='')
        self.create_text(13,15,text=label,fill='#e4efff',anchor='w',font=('Segoe UI',10))
        self.create_text(w-12,h-10,text='ドラッグ：回転・上下 / ホイール：拡大 / ダブルクリック：全体表示',fill='#91a4c4',anchor='e')
