"""Canvas 3D-ish voxel preview of the source-proven scene. No game connection."""
from __future__ import annotations
import math
import tkinter as tk

PALETTE = ['#9ba4a7', '#e1d8ce', '#718391', '#6dcce2', '#b78b5b', '#ffe18b', '#e88777']


def shade(color: str, factor: float) -> str:
    rgb = [int(color[i:i+2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(c*factor))) for c in rgb)


class VoxelPreview(tk.Canvas):
    LIMIT = 2300
    DETAIL_LIMITS = {'標準（2,300）': 2300, '精細（8,000）': 8000,
                     '高精細（15,000）': 15000}
    def __init__(self, master, **kw):
        super().__init__(master, bg='#101b2b', highlightthickness=0, **kw)
        self.scene = None
        self.angle = math.pi * 3 / 4
        self.zoom = 1.0
        self.pressed_at = None
        self.detail_limit = self.LIMIT
        self.bind('<Configure>', lambda e: self.draw())
        self.bind('<ButtonPress-1>', lambda e: setattr(self, 'pressed_at', (e.x, self.angle)))
        self.bind('<B1-Motion>', self._drag)
        self.bind('<MouseWheel>', self._wheel)

    def _drag(self, e):
        if self.pressed_at:
            x, angle = self.pressed_at
            self.angle = angle + (e.x-x)*0.009
            self.draw()

    def _wheel(self, e):
        self.zoom = max(.4, min(4.0, self.zoom * (1.12 if e.delta > 0 else .89)))
        self.draw()

    def set_detail(self, label: str) -> None:
        if label not in self.DETAIL_LIMITS:
            raise ValueError('Unknown preview detail level')
        self.detail_limit = self.DETAIL_LIMITS[label]
        self.draw()

    def show(self, scene):
        self.scene = scene
        self.zoom = 1.0
        self.draw()

    def draw(self):
        self.delete('all')
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        self.create_text(w//2,h//2,fill='#91a4c4',text='生成した建築のプレビューがここに表示されます' if not self.scene else '描画中...')
        if not self.scene:
            return
        pts = self.scene.get('primitives') or []
        palette = self.scene.get('previewPalette', PALETTE)
        if not pts:
            return
        self.delete('all')
        total = len(pts)
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
        b = self.scene['bounds']
        mx=(b['minX']+b['maxX']+1)/2
        mz=(b['minZ']+b['maxZ']+1)/2
        my=(b['minY']+b['maxY']+1)/2
        extent=max(b['maxX']-b['minX']+1,b['maxZ']-b['minZ']+1,(b['maxY']-b['minY']+1)*1.2,8)
        scale=min(w, h)*.62/extent*self.zoom
        ca, sa = math.cos(self.angle), math.sin(self.angle)
        def proj(x,y,z):
            a,b = x-mx,z-mz
            return (w*.5+(ca*a-sa*b)*scale,
                    h*.52+(.42*(sa*a+ca*b)-(y-my)*.85)*scale)
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
                self.create_polygon(*coords,fill=shade(color,light),outline='#162335',width=.3)
        label=self.scene.get('previewLabel') or f'3D SOURCEプレビュー   {total:,}セル'
        if skipped:
            label+=f'  |  簡易描画（可視セル {len(visible):,}/{len(visible)+skipped:,}）'
        self.create_rectangle(0,0,w,29,fill='#192a40',outline='')
        self.create_text(13,15,text=label,fill='#e4efff',anchor='w',font=('Segoe UI',10))
        self.create_text(w-12,h-10,text='ドラッグ：回転 / ホイール：拡大',fill='#91a4c4',anchor='e')
