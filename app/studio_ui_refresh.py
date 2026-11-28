"""MAB Studio v0.3: B143-inspired, native Tk presentation-only refresh.

No renderer replacement, SOURCE edits, model calls, or construction logic.
Usage on Windows (review first):
    python studio_ui_refresh.py --install <MABStudio checkout>
The installer adds this module and one guarded integration point to app/main.py.
It does not change Git refs, commit, push, or replace existing unrelated changes.
"""
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from app.core import StudioError
from app.revisions import normalize_edit_scope

BG = '#f5f7fa'
PANEL = '#ffffff'
FIELD = '#f5f7fa'
INK = '#17212b'
MUTED = '#526174'
LINE = '#e2e7ed'
BUTTON = '#ffffff'
HOVER = '#edf3fa'
ACCENT = '#1763c6'
MINT = '#146b52'
DISABLED = '#778392'
CAUTION = '#8a5411'
_RGB = re.compile(r'^#[0-9a-fA-F]{6}$')

# Only remap UI chrome, not building palette, selection colors or renderer pixels.
OLD = {
    '#f3f6fb': BG, '#e9eef5': BG, '#ffffff': PANEL,
    '#13253d': INK, '#617389': MUTED, '#dce5ee': LINE,
    '#2563eb': ACCENT, '#eaf2ff': BUTTON, '#9a5b22': CAUTION,
    '#f5f8fd': FIELD, '#3d5876': MUTED, '#395573': ACCENT,
}


def _walk(widget, excluded):
    for child in widget.winfo_children():
        if child is excluded:
            continue
        yield child
        yield from _walk(child, excluded)


def _configure_style(studio):
    style = ttk.Style(studio)
    style.configure('App.TFrame', background=BG)
    for name in ('TFrame', 'Panel.TFrame'):
        style.configure(name, background=PANEL)
    style.configure('TLabel', background=PANEL, foreground=INK)
    style.configure('Muted.TLabel', background=PANEL, foreground=MUTED)
    style.configure('PageTitle.TLabel', background=BG, foreground=INK,
                    font=('Yu Gothic UI', 18, 'bold'))
    style.configure('Section.TLabel', background=PANEL, foreground=INK)
    style.configure('TButton', background=BUTTON, foreground=INK,
                    bordercolor=LINE, lightcolor=BUTTON, darkcolor=BUTTON,
                    focuscolor=ACCENT, padding=(9, 6))
    style.map('TButton', background=[('disabled', PANEL), ('active', HOVER)],
              foreground=[('disabled', DISABLED), ('active', INK)])
    style.configure('Primary.TButton', background=ACCENT, foreground='#ffffff',
                    bordercolor=ACCENT, padding=(10, 8))
    style.map('Primary.TButton', background=[('disabled', '#e6ebf1'), ('active', '#0e4fa8')],
              foreground=[('disabled', DISABLED), ('active', INK)])
    style.configure('Revision.TButton', background='#eaf5ef', foreground=MINT,
                    bordercolor='#a9d5bf', padding=(10, 8),
                    font=('Yu Gothic UI', 10, 'bold'))
    style.map('Revision.TButton', background=[('disabled', '#f0f2f4'), ('active', '#d9ede2')],
              foreground=[('disabled', DISABLED), ('active', MINT)])
    style.configure('Quiet.TButton', background=PANEL, foreground=ACCENT,
                    padding=(6, 4))
    style.map('Quiet.TButton', background=[('disabled', PANEL), ('active', HOVER)],
              foreground=[('disabled', DISABLED), ('active', INK)])
    for name in ('TCombobox', 'TEntry', 'TSpinbox'):
        style.configure(name, fieldbackground=FIELD, background=BUTTON,
                        foreground=INK, bordercolor=LINE, lightcolor=LINE,
                        darkcolor=LINE, insertcolor=INK, arrowcolor=INK, padding=4)
        style.map(name, fieldbackground=[('disabled', PANEL), ('readonly', FIELD)],
                  foreground=[('disabled', DISABLED), ('readonly', INK)],
                  selectbackground=[('readonly', HOVER)], selectforeground=[('readonly', INK)])
    for name in ('TCheckbutton', 'TRadiobutton'):
        style.configure(name, background=PANEL, foreground=INK, indicatorcolor=FIELD)
        style.map(name, background=[('active', PANEL)],
                  foreground=[('disabled', DISABLED)],
                  indicatorcolor=[('selected', MINT), ('!selected', FIELD)])
    style.configure('TSeparator', background=LINE)
    style.configure('TLabelframe', background=PANEL, bordercolor=LINE)
    style.configure('TLabelframe.Label', background=PANEL, foreground=INK)
    style.configure('Treeview', background=FIELD, fieldbackground=FIELD, foreground=INK)
    style.map('Treeview', background=[('selected', HOVER)], foreground=[('selected', INK)])
    style.configure('Treeview.Heading', background=BUTTON, foreground=INK)
    style.map('Treeview.Heading', background=[('active', HOVER)])
    studio.option_add('*TCombobox*Listbox.background', FIELD)
    studio.option_add('*TCombobox*Listbox.foreground', INK)
    studio.option_add('*TCombobox*Listbox.selectBackground', HOVER)
    studio.option_add('*TCombobox*Listbox.selectForeground', INK)


def _shorten_pack_padding(widget, pady):
    if widget.winfo_manager() == 'pack':
        widget.pack_configure(pady=pady)


def _bind_assistant_summaries(studio):
    """Keep the dock compact while showing only current, real app state."""
    pairs = ((studio.caption, studio._assistant_caption_text),
             (studio.status, studio._assistant_status_text))
    for source, target in pairs:
        def update(*_, source=source, target=target):
            value = ' '.join(str(source.get() or '').split())
            target.set(value if len(value) <= 52 else value[:51] + '…')
        source.trace_add('write', update)
        update()


def describe_source_cell(scene, coordinate, *, is_source_view):
    """Read-only palette detail for one unique cell in the bound SOURCE scene."""
    if not is_source_view or not isinstance(scene, dict):
        return None
    if not isinstance(coordinate, (tuple, list)) or len(coordinate) != 3:
        return None
    if any(type(v) is not int for v in coordinate):
        return None
    cells = scene.get('primitives')
    if not isinstance(cells, list):
        return None
    xyz = tuple(coordinate)
    match = None
    for p in cells:
        if not isinstance(p, (list, tuple)) or len(p) < 4:
            continue
        if any(type(v) is not int for v in p[:3]) or tuple(p[:3]) != xyz:
            continue
        if match is not None:
            return None
        match = p
    if match is None or type(match[3]) is not int or not 0 <= match[3] < 256:
        return None
    material = match[3]
    palette = scene.get('previewPalette')
    color = None
    if isinstance(palette, list) and material < len(palette):
        value = palette[material]
        if isinstance(value, str) and _RGB.fullmatch(value):
            color = value.lower()
    return {'coordinate': xyz, 'paletteIndex': material, 'previewColor': color}


def cell_inspector_text(info):
    if info is None:
        return ''
    x, y, z = info['coordinate']
    color = info['previewColor']
    return f'選択セル XYZ {x}, {y}, {z} ｜ 表示材質 {info["paletteIndex"]}' + (
        f' ({color})' if color else '')


def _bound_source_scene(studio):
    mode = studio.view_mode.get()
    if mode == '建築のみ':
        expected = studio.scene
    elif mode == '修正案（未承認）' and studio.pending_revision is not None:
        expected = studio.pending_revision.get('scene')
    else:
        return None
    displayed = getattr(studio.preview, 'scene', None)
    return displayed if isinstance(expected, dict) and displayed is expected else None


def _refresh_selected_source_cell(studio):
    label = getattr(studio, '_b143_cell_info_label', None)
    swatch = getattr(studio, '_b143_cell_swatch', None)
    if label is None or swatch is None or not label.winfo_exists():
        return
    scope = getattr(studio, 'edit_scope', None)
    coordinate = scope.get('selected') if isinstance(scope, dict) else None
    scene = _bound_source_scene(studio)
    selected_scene = getattr(studio, '_b143_selected_source_scene', None)
    info = None
    if scene is selected_scene and coordinate is not None:
        axis = {'x': 0, 'y': 1, 'z': 2}.get(
            str(getattr(studio.preview, 'section_axis', 'y')).lower(), 1)
        level = getattr(studio.preview, 'section_level',
                        getattr(studio.preview, 'cut_level', None))
        side = getattr(studio.preview, 'section_side', 'low')
        visible_in_section = (level is None or not isinstance(coordinate, (tuple, list))
                              or len(coordinate) != 3
                              or (coordinate[axis] <= level if side == 'low'
                                  else coordinate[axis] >= level))
        if visible_in_section:
            info = describe_source_cell(scene, coordinate, is_source_view=True)
    if info is None:
        label.pack_forget()
        swatch.pack_forget()
        # A view/section transition invalidates the old physical pick. Require
        # a new pick before showing details again, even if the same cell exists.
        studio._b143_selected_source_scene = None
        return
    label.configure(text=cell_inspector_text(info))
    if info['previewColor']:
        swatch.configure(background=info['previewColor'])
        swatch.pack(side='right', before=label, padx=(0, 5))
    else:
        swatch.pack_forget()
    label.pack(side='right', before=studio.clear_edit_scope_btn, padx=(0, 7))


def _bind_preview_cell_pick(studio):
    preview = getattr(studio, 'preview', None)
    callback = getattr(preview, 'on_cell_picked', None)
    if not callable(callback) or getattr(preview, '_b143_cell_pick_bound', False):
        return

    def selected(cell):
        scene = getattr(preview, 'scene', None)
        studio._b143_selected_source_scene = scene if isinstance(scene, dict) else None
        callback(cell)
        _refresh_selected_source_cell(studio)

    preview.on_cell_picked = selected
    preview._b143_cell_pick_bound = True


def _wrap_cell_sync(studio, method_name):
    hooked = getattr(studio, '_b143_cell_sync_methods', set())
    if method_name in hooked:
        return
    original = getattr(studio, method_name)

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if method_name == '_preview_updated':
            # Native/lightweight preview replacement installs a fresh callback.
            studio.preview.on_scene_changed = studio._preview_updated
            _bind_preview_cell_pick(studio)
            _bind_preview_section(studio)
        _refresh_selected_source_cell(studio)
        return result

    setattr(studio, method_name, wrapped)
    hooked.add(method_name)
    studio._b143_cell_sync_methods = hooked


def _bind_preview_section(studio):
    preview = getattr(studio, 'preview', None)
    if preview is None or getattr(preview, '_b143_cell_section_bound', False):
        return
    method_name = 'set_section' if callable(getattr(preview, 'set_section', None)) else 'set_cut_level'
    original = getattr(preview, method_name, None)
    if not callable(original):
        return

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        _refresh_selected_source_cell(studio)
        return result

    setattr(preview, method_name, wrapped)
    preview._b143_cell_section_bound = True


def prompt_context(*, title='', draft=False, scope=None, can_revise=False, busy=False):
    """Describe the action that the existing revision button will target."""
    if busy:
        return 'AI処理中・入力内容は保持されます'
    if not can_revise:
        return '新規設計：日本語で建築を指示'
    title = ' '.join(str(title or '建築').split())
    if len(title) > 18:
        title = title[:17] + '…'
    target = ('未承認案：' if draft else '修正対象：') + title
    if scope:
        target += '｜' + ' '.join(str(scope).split())
    return target + '\n新規設計は別案・修正は対象案'


def _refresh_prompt_context(studio):
    label = getattr(studio, '_b143_prompt_context_label', None)
    if label is None or not label.winfo_exists():
        return
    if studio.busy:
        label.configure(text=prompt_context(busy=True))
        return

    if getattr(studio, 'prompt_mode', None) is None or studio.prompt_mode.get() != 'revise':
        label.configure(text=prompt_context())
        return
    can_revise = studio.revise_btn.instate(['!disabled'])
    pending = studio.pending_revision
    draft = bool(pending is not None and pending.get('kind') == 'new')
    design = (pending.get('design') if draft else studio.design) if can_revise else None
    title = design.get('title', '建築') if isinstance(design, dict) else ''

    # Match the exact scope source passed by revise_design / its new-draft path.
    scope_data = studio.edit_scope
    if draft:
        scope_data = scope_data or pending.get('editScope')
    scope_text = None
    if scope_data is not None:
        try:
            checked = normalize_edit_scope(scope_data)
        except (StudioError, TypeError, ValueError):
            checked = None
        if checked is not None:
            x, y, z = checked['selected']
            scope_text = f'セル {x}, {y}, {z} ±{checked["radius"]}'

    label.configure(text=prompt_context(title=title, draft=draft,
                                        scope=scope_text, can_revise=can_revise))


def _wrap_context_refresh(studio, method_name):
    original = getattr(studio, method_name)

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        _refresh_prompt_context(studio)
        return result

    setattr(studio, method_name, wrapped)


def apply_studio_ui_refresh(studio):
    """Called once after existing _make_ui; preserves widget identity and commands.

    Missing layout anchors are reported rather than guessing a different UI tree.
    The render host is never restyled or recreated, so future render adapters can
    plug into the existing scene boundary independently of this presentation code.
    """
    if getattr(studio, '_b143_ui_refresh_applied', False):
        return
    required = ('viewer_host', '_creation_actions', 'prompt', 'generate_btn',
                'revise_btn', 'preview')
    missing = [name for name in required if not hasattr(studio, name)]
    if missing:
        raise RuntimeError('Studio UI refresh: unsupported layout: ' + ', '.join(missing))
    left = studio._creation_actions.master
    right = studio.viewer_host.master
    body = left.master
    root = body.master
    if (right.master is not body or left.master is not body
            or str(left) not in tuple(map(str, body.panes()))
            or str(right) not in tuple(map(str, body.panes()))):
        raise RuntimeError('Studio UI refresh: unexpected panel layout; review local source')
    _configure_style(studio)
    studio.configure(background=BG)
    # Existing widget managers, bindings, callbacks and enable/disable logic stay.
    root.configure(padding=(12, 8, 12, 8))
    left.configure(padding=(12, 9, 12, 8))
    right.configure(padding=(8, 8, 8, 8))
    # Keep the previous generous width at high DPI; no clipped action buttons.
    left.configure(width=360)
    for widget in _walk(root, studio.viewer_host):
        if isinstance(widget, (tk.Label, tk.Text, tk.Scale, tk.Frame)):
            options = widget.keys()
            update = {}
            for key in ('background', 'foreground', 'insertbackground',
                        'highlightbackground', 'highlightcolor', 'activebackground',
                        'activeforeground', 'troughcolor'):
                if key in options:
                    original = str(widget.cget(key)).lower()
                    if original in OLD:
                        update[key] = OLD[original]
            if update:
                widget.configure(**update)
        if isinstance(widget, ttk.Label):
            # Match the original static copy only; never touch status or draft text.
            if not widget.cget('textvariable'):
                text = widget.cget('text')
                if text == 'どんな建築を作りたい？':
                    widget.configure(text='建築の指示・会話修正')
                elif text == '日本語で自由に指示できます':
                    widget.configure(text='新規設計と部分修正の指示をここに入力')
                    studio._b143_prompt_context_label = widget
                elif text == 'SOURCE検証・3D編集・Education施工':
                    widget.configure(text='SOURCEプレビュー')
    if not hasattr(studio, '_b143_prompt_context_label'):
        studio._b143_prompt_context_label = getattr(studio, 'prompt_context_label', None)
    if studio._b143_prompt_context_label is None:
        raise RuntimeError('Studio UI refresh: prompt context label not found')
    studio.prompt.configure(background=FIELD, foreground=INK,
                            insertbackground=INK, selectbackground=HOVER,
                            selectforeground=INK, highlightbackground=LINE,
                            highlightcolor=ACCENT, padx=10, pady=9)
    if not getattr(studio, '_b143_prompt_context_wrap_bound', False):
        def resize_context(event):
            width = max(180, int(event.width) - 24)
            if int(studio._b143_prompt_context_label.cget('wraplength') or 0) != width:
                studio._b143_prompt_context_label.configure(wraplength=width)
        left.bind('<Configure>', resize_context, add='+')
        studio._b143_prompt_context_wrap_bound = True
    studio.revise_btn.configure(style='Revision.TButton')
    # New distinct shortcut for revision. Existing Ctrl+Enter and its behavior
    # remain unchanged. invoke() preserves disabled state and existing handler.
    if not studio.prompt.bind('<Control-Shift-Return>'):
        def revise_shortcut(_event):
            studio.revise_btn.invoke()
            return 'break'
        studio.prompt.bind('<Control-Shift-Return>', revise_shortcut)
    # Compact margins around existing bars; leave order, visibility, and review
    # bar insertion/removal to the original application.
    for widget in right.pack_slaves():
        if widget is studio.viewer_host or widget is getattr(studio, 'reviewbar', None):
            continue
        if isinstance(widget, ttk.Frame):
            _shorten_pack_padding(widget, (0, 5))
    if not hasattr(studio, '_b143_cell_info_label'):
        editbar = studio.edit_scope_label.master
        studio._b143_cell_info_label = ttk.Label(
            editbar, text='', style='Muted.TLabel', justify='left')
        studio._b143_cell_swatch = tk.Label(
            editbar, text='', width=2, height=1, bd=1, relief='solid',
            highlightthickness=0, background=PANEL)
    studio._b143_selected_source_scene = None
    # Keep target context synchronized with the source app's authoritative
    # button and selection updates without changing app/main.py or callbacks.
    if not getattr(studio, '_b143_prompt_context_hooks', False):
        _wrap_context_refresh(studio, '_refresh_buttons')
        _wrap_context_refresh(studio, '_update_edit_scope_label')
        studio.prompt_mode.trace_add('write', lambda *_: _refresh_prompt_context(studio))
        studio._b143_prompt_context_hooks = True
    _wrap_cell_sync(studio, '_update_edit_scope_label')
    if hasattr(studio, '_preview_updated'):
        _wrap_cell_sync(studio, '_preview_updated')
        studio.preview.on_scene_changed = studio._preview_updated
    _bind_preview_cell_pick(studio)
    _bind_preview_section(studio)
    _bind_assistant_summaries(studio)
    _refresh_prompt_context(studio)
    _refresh_selected_source_cell(studio)
    # No calls to update(), redraw(), model or world APIs here.
    studio._b143_ui_refresh_applied = True


def install(checkout: Path) -> str:
    """Preserve every byte outside the single integration point; refuse drift."""
    path = checkout / 'app' / 'main.py'
    target = checkout / 'app' / 'studio_ui_refresh.py'
    old = path.read_bytes()
    encoding = 'utf-8-sig' if old.startswith(b'\xef\xbb\xbf') else 'utf-8'
    src = old.decode(encoding)
    module = Path(__file__).read_bytes()
    if target.exists() and target.read_bytes() != module:
        raise RuntimeError('Different app/studio_ui_refresh.py already exists; no overwrite')
    tree = ast.parse(src)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Studio']
    if len(classes) != 1:
        raise RuntimeError('Studio class not uniquely identified')
    methods = [n for n in classes[0].body if isinstance(n, ast.FunctionDef) and n.name == '__init__']
    if len(methods) != 1:
        raise RuntimeError('Studio.__init__ not uniquely identified')
    method = methods[0]
    existing = 'apply_studio_ui_refresh(self)'
    if existing in src:
        if target.exists() and target.read_bytes() == module:
            return 'Already installed; no changes'
        raise RuntimeError('Integration exists but module missing; inspect manually')
    anchors = [n for n in method.body if isinstance(n, ast.Expr)
               and isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Attribute)
               and isinstance(n.value.func.value, ast.Name)
               and n.value.func.value.id == 'self' and n.value.func.attr == '_make_ui'
               and not n.value.args and not n.value.keywords]
    if len(anchors) != 1:
        raise RuntimeError('Expected a unique self._make_ui() call; no changes')
    lines = src.splitlines(keepends=True)
    anchor = anchors[0]
    raw = lines[anchor.lineno - 1]
    if anchor.lineno != anchor.end_lineno or raw.strip() != 'self._make_ui()':
        raise RuntimeError('Unexpected integration line; no changes')
    newline = '\r\n' if raw.endswith('\r\n') else '\n'
    indent = raw[:len(raw)-len(raw.lstrip())]
    insertion = [indent + 'from app.studio_ui_refresh import apply_studio_ui_refresh' + newline,
                 indent + existing + newline]
    lines[anchor.end_lineno:anchor.end_lineno] = insertion
    updated = ''.join(lines).encode(encoding)
    compile(updated, str(path), 'exec')
    compile(module, str(target), 'exec')
    # Check for concurrent edits before writing. No branch/commit operations.
    if path.read_bytes() != old:
        raise RuntimeError('main.py changed during review; no changes')
    created = not target.exists()
    if created:
        with target.open('xb') as f:
            f.write(module)
    try:
        if path.read_bytes() != old:
            raise RuntimeError('main.py changed during install; no changes')
        # Exclusive temporary file, atomic replacement on the same filesystem.
        import tempfile
        import os
        fd, tmp = tempfile.mkstemp(prefix='.studio-ui-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(updated)
            if path.read_bytes() != old:
                raise RuntimeError('main.py changed during install; no changes')
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except BaseException:
        if created and target.exists() and target.read_bytes() == module:
            target.unlink()
        raise
    return 'Installed app/studio_ui_refresh.py and two integration lines in app/main.py'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', type=Path, required=True)
    print(install(parser.parse_args().install.resolve()))
