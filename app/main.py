"""MAB Studio Windows GUI. Offline-first, test-world only. No world-writing calls."""
from __future__ import annotations
import json
import os
import gc
from pathlib import Path
from datetime import datetime, timezone
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core import (
    BASELINE_SHA, DEFAULT_BRANCH, EngineManager, StudioError, ModelCancelled,
    ask_llm, branch_sha, compatibility_check, llm_models, unload_local_model, verify_loaded_model, run_bridge, atomic_json
)
from app.preview import VoxelPreview
from app.native_viewer import NativeViewerError, NativeVoxelPreview, native_dependencies_available
from app.windows_dpi import enable_windows_dpi_awareness
from app.preferences import read_local_choice, save_local_choice
from app.project_io import export_verified_source
from app.v11_source_import import read_external_b143_source
from app.v11_watch import newest_v11_ready_source
from app.diagnostics import check_local_setup, readiness_text
from app.education import import_b134_readback, parse_probe_coordinates, run_education_readonly
from app.examples import example_library
from app.revisions import RevisionStore, compare_scenes, diff_preview_scene, describe_design_changes
from app.terrain import inspect_mcworld, read_mcworld_terrain, create_overlay_scene


class Studio(tk.Tk):
    def __init__(self):
        enable_windows_dpi_awareness()
        super().__init__()
        self.title('MAB Studio — Minecraft Education 建築AI（安全プレビュー）')
        if os.environ.get('MAB_STUDIO_ONE_CLICK_FALLBACK') == '1':
            self.title('MAB Studio — 安全プレビュー（軽量3D・ネイティブ準備未完了）')
        self.geometry('1210x880')
        self.minsize(1020, 840)
        self.configure(bg='#e9eef5')
        self.manager = EngineManager()
        self.pending_sha = None
        self.pending_events = queue.Queue()
        self.busy = False
        self.scene = None
        self.design = None
        self.history = RevisionStore(self.manager.root / 'projects')
        self.current_snapshot = None
        self.delta = None
        # Includes both revisions and newly generated designs. B143 PASS is
        # not approval: the user must see the 3D building before committing.
        self.pending_revision = None  # in-memory DRAFT; NEVER a committed history state
        # SOURCE-rejected AI output is held only in memory for explicit one-shot
        # repair. Never save or preview it as an approved B143 model.
        self.failed_generation = None
        # One visible Design click allows at most one sequential AI repair after
        # a *B143 SOURCE* refusal. Never retry model/transport failures.
        self.auto_repair_invalid_generation = True
        self._education_running = False
        self._education_cancel = threading.Event()
        self._active_cancel_event = None
        self._closed = False
        self._section_after = None
        self.terrain = None
        self.terrain_archive = None
        self.terrain_report = None
        self.view_mode = tk.StringVar(value='建築のみ')
        self.detail_mode = tk.StringVar(value='なめらか（全体）')
        self.section_enabled = tk.BooleanVar(value=False)
        self.section_level = tk.IntVar(value=0)
        self.section_axis = tk.StringVar(value='Y')
        self.section_side = tk.StringVar(value='低い側')
        self.place_xyz = tk.StringVar(value='0 64 0')
        self.fast_revision_mode = tk.BooleanVar(value=True)
        provider_saved, model_saved = read_local_choice(self.manager.root)
        self.llm_provider = tk.StringVar(value=provider_saved)
        self.model = tk.StringVar(value=model_saved)
        self.branch = tk.StringVar(value=self.manager.state.get('tracked_branch', DEFAULT_BRANCH))
        self._make_ui()
        # The window manager must not silently discard an expensive, unsaved
        # local-model draft. Plain approved-project exits stay one click.
        self.protocol('WM_DELETE_WINDOW', self.request_close)
        self.place_xyz.trace_add('write', self._on_anchor_changed)
        self._poll_after = self.after(120, self._poll)
        self._startup_after = self.after(2000, self._startup)
        # Reviewed engine updates are not automatically installable. Do not
        # interrupt users with periodic remote checks; manual check stays available.
        self._check_after = None
        self._v11_seen_sha = ''
        self._v11_ready = None
        self._v11_watch_after = self.after(8000, self._check_v11_ready_source)

    def request_close(self):
        """Close only after warning about work that exists solely in memory.

        This is the window-manager X path, not a new persistence mechanism:
        never turn an unapproved draft into a committed SOURCE snapshot.
        """
        if self._closed:
            return
        # Non-AI workers may be committing/restoring SOURCE history. Do not
        # interrupt a write in progress or pretend it was cancelled.
        if self.busy and self._active_cancel_event is None:
            messagebox.showinfo('処理中',
                                'SOURCEの確認・保存などを処理中です。完了後に終了してください。',
                                parent=self)
            return
        has_draft = self.pending_revision is not None
        has_failed = self.failed_generation is not None
        has_ai_request = self.busy and self._active_cancel_event is not None
        if has_draft or has_failed or has_ai_request:
            notes = []
            if has_draft:
                notes.append('未承認の建築案は終了すると失われます。')
            if has_failed:
                notes.append('検証不合格のAI案も終了すると失われます。')
            if has_ai_request:
                notes.append('進行中のAI要求はキャンセルされ、回答は採用されません。')
            notes.append('承認済みの建築と保存履歴は変更しません。')
            notes.append('終了しますか？')
            if not messagebox.askyesno('未保存の作業を破棄して終了',
                                       '\n'.join(notes), parent=self):
                return
        self.destroy()

    def destroy(self):
        # Tk.after callbacks must not run after GUI disposal; shutdown an active
        # read-only listener without editing any world state.
        if self._closed:
            return
        self._closed = True
        self._education_cancel.set()
        # Closing the window discards any in-flight AI candidate and interrupts
        # this Studio request's socket; never leave a hidden long-running POST.
        if self._active_cancel_event is not None:
            self._active_cancel_event.set()
        for attr in ('_poll_after','_startup_after','_check_after','_section_after','_v11_watch_after'):
            handle=getattr(self,attr,None)
            if handle is not None:
                try:self.after_cancel(handle)
                except tk.TclError:pass
        # Free Tk Variable wrappers on the GUI thread rather than allowing
        # Python's GC to finalize them later from a local-AI worker thread.
        for attr,value in tuple(self.__dict__.items()):
            if isinstance(value,tk.Variable):
                setattr(self,attr,None)
        gc.collect()
        super().destroy()

    def _inherit_preview_state(self, viewer):
        """Copy only the display state, never SOURCE/history/construction data."""
        viewer.set_detail(self.detail_mode.get())
        viewer.show(self.preview.scene)
        viewer.angle, viewer.elevation = self.preview.angle, self.preview.elevation
        viewer.zoom = self.preview.zoom
        viewer.pan_x, viewer.pan_y = self.preview.pan_x, self.preview.pan_y
        if hasattr(viewer, 'set_section'):
            viewer.set_section(getattr(self.preview, 'section_axis', 'y'),
                               getattr(self.preview, 'section_level', self.preview.cut_level),
                               getattr(self.preview, 'section_side', 'low'))
        else:
            viewer.set_cut_level(self.preview.cut_level)
        viewer.draw()

    def open_large_preview(self):
        """Open the SAME renderer as the main viewer (native stays native)."""
        if self._large_preview_window is not None:
            try:
                if self._large_preview_window.winfo_exists():
                    self._large_preview_window.lift()
                    return
            except tk.TclError:
                pass
            self._large_preview_window = self._large_preview = None
        popup = tk.Toplevel(self)
        popup.title('MAB Studio — 3Dプレビュー（表示のみ）')
        popup.geometry('1320x900')
        popup.minsize(700, 500)
        frame = ttk.Frame(popup, padding=10)
        frame.pack(fill='both', expand=True)
        tools = ttk.Frame(frame)
        tools.pack(fill='x', pady=(0, 7))
        native = isinstance(self.preview, NativeVoxelPreview)
        label = ttk.Label(tools, text=('ネイティブ3D（表示のみ）' if native else
                                       '軽量3D（表示のみ）'))
        label.pack(side='left')
        viewer = None
        try:
            viewer = NativeVoxelPreview(frame) if native else VoxelPreview(frame)
            viewer.pack(fill='both', expand=True)
            popup.update_idletasks()  # Full-size native framebuffer on first open.
            self._inherit_preview_state(viewer)
        except (NativeViewerError, OSError, ValueError, RuntimeError, tk.TclError):
            if viewer is not None:
                viewer.destroy()
            if not native:
                popup.destroy()
                raise
            # Out-of-memory/driver failure: explicitly labelled lightweight
            # fallback, never report this as a successful native 3D render.
            label.configure(text='ネイティブ拡大表示に失敗：軽量3Dで表示（SOURCE不変）')
            viewer = VoxelPreview(frame)
            viewer.pack(fill='both', expand=True)
            self._inherit_preview_state(viewer)
        ttk.Button(tools, text='全体表示', command=viewer.reset_view).pack(side='right')
        for name in reversed(tuple(VoxelPreview.VIEWS)):
            ttk.Button(tools, text=name,
                       command=lambda selected=name: viewer.set_view(selected)).pack(
                           side='right', padx=(0, 4))
        self._large_preview_window = popup
        self._large_preview = viewer
        self._large_preview_target_mode = native
        def close():
            self._large_preview = None
            self._large_preview_window = None
            self._large_preview_target_mode = None
            popup.destroy()
        popup.protocol('WM_DELETE_WINDOW', close)

    def _sync_large_preview(self):
        viewer = self._large_preview
        if viewer is None:
            return
        try:
            if not viewer.winfo_exists():
                self._large_preview = self._large_preview_window = None
                return
            if self._large_preview_target_mode != isinstance(self.preview, NativeVoxelPreview):
                # Switching native/lightweight must also switch the popup.
                self._large_preview_window.destroy()
                self._large_preview = self._large_preview_window = None
                self.open_large_preview()
                return
            if (viewer.scene is self.preview.scene
                    and getattr(viewer, 'section_axis', 'y') == getattr(self.preview, 'section_axis', 'y')
                    and getattr(viewer, 'section_level', viewer.cut_level) ==
                        getattr(self.preview, 'section_level', self.preview.cut_level)
                    and getattr(viewer, 'section_side', 'low') ==
                        getattr(self.preview, 'section_side', 'low')):
                return  # Preserve independent popup orbit/zoom during UI updates.
            self._inherit_preview_state(viewer)
        except tk.TclError:
            self._large_preview = self._large_preview_window = None

    def open_gpu_preview(self):
        """Toggle *embedded* native 3D / lightweight Tk; never open a browser.

        Only SOURCE-verified scene primitives are used. The old widget is
        disposed after the new one is initialized, preserving safe fallback.
        """
        if self.busy or not self.preview.scene:
            messagebox.showinfo('高精細3D', '表示する設計がありません。先に設計を開いてください。')
            return
        old = self.preview
        native = isinstance(old, NativeVoxelPreview)
        if not native and not native_dependencies_available():
            messagebox.showinfo('アプリ内3Dの準備',
                                '高精細のネイティブ3DにはVTKが必要です。\n'
                                '同梱の RUN_STUDIO_NATIVE.bat を起動してください。\n'
                                'アプリ内に表示します（ブラウザーは使いません）。')
            return
        new_view = None
        old_hidden = False
        try:
            new_view = VoxelPreview(self.viewer_host) if native else NativeVoxelPreview(self.viewer_host)
            new_view.on_scene_changed = None
            new_view.set_detail(self.detail_mode.get())
            new_view.show(old.scene)
            new_view.angle, new_view.elevation = old.angle, old.elevation
            new_view.zoom = old.zoom
            new_view.pan_x, new_view.pan_y = old.pan_x, old.pan_y
            if hasattr(new_view, 'set_section'):
                new_view.set_section(getattr(old, 'section_axis', 'y'),
                                     getattr(old, 'section_level', old.cut_level),
                                     getattr(old, 'section_side', 'low'))
            else:
                new_view.set_cut_level(old.cut_level)
            # Rendering depends on the *visible* widget size. The old renderer
            # must remain alive until the replacement has drawn successfully;
            # a GPU/VTK error here must never leave the Studio preview blank.
            old.pack_forget()
            old_hidden = True
            new_view.pack(fill='both', expand=True)
            self.viewer_host.update_idletasks()
            new_view.draw()
        except (NativeViewerError, OSError, ValueError, RuntimeError, tk.TclError) as exc:
            if new_view is not None:
                try: new_view.destroy()
                except Exception: pass
            if old_hidden and old.winfo_exists():
                old.pack(fill='both', expand=True)
            # Preserve active renderer, camera, preview popup and SOURCE history.
            self.preview = old
            self._preview_updated()
            self.status.set('3D切替に失敗したため、元の表示を維持しました（SOURCE不変）')
            messagebox.showerror('3D表示を切替えられません', str(exc))
            return
        # Commit only AFTER a real, successful first frame.
        new_view.on_scene_changed = self._preview_updated
        self.preview = new_view
        old.destroy()
        self._preview_updated()
        self.status.set('3D表示をアプリ内で切替えました（SOURCE・ワールド未変更）')

    def _preview_updated(self):
        self._sync_large_preview()
        if hasattr(self, 'gpu_view_btn'):
            self.gpu_view_btn.configure(state='normal' if self.preview.scene and not self.busy else 'disabled',
                                        text='軽量3Dに切替' if isinstance(self.preview, NativeVoxelPreview) else '高精細3D（アプリ内）')

    def _make_ui(self):
        """Creation-first desktop layout. Technical controls live in a separate window."""
        c = {'bg': '#f3f6fb', 'panel': '#ffffff', 'ink': '#13253d',
             'muted': '#617389', 'line': '#dce5ee', 'accent': '#2563eb',
             'pale': '#eaf2ff', 'caution': '#9a5b22'}
        self.configure(bg=c['bg'])
        self.geometry('1270x830')
        self.minsize(990, 690)
        style = ttk.Style(self)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('App.TFrame', background=c['bg'])
        style.configure('Panel.TFrame', background=c['panel'])
        style.configure('TFrame', background=c['panel'])
        style.configure('TLabel', background=c['panel'], foreground=c['ink'],
                        font=('Yu Gothic UI', 10))
        style.configure('Muted.TLabel', background=c['panel'], foreground=c['muted'],
                        font=('Yu Gothic UI', 9))
        style.configure('PageTitle.TLabel', background=c['bg'], foreground=c['ink'],
                        font=('Yu Gothic UI', 20, 'bold'))
        style.configure('Section.TLabel', background=c['panel'], foreground=c['ink'],
                        font=('Yu Gothic UI', 12, 'bold'))
        style.configure('TButton', font=('Yu Gothic UI', 10), padding=(10, 8),
                        background=c['pale'], foreground=c['ink'], borderwidth=0)
        style.map('TButton', background=[('active', '#d9e7fc'), ('disabled', '#e9edf2')])
        style.configure('Primary.TButton', font=('Yu Gothic UI', 11, 'bold'),
                        padding=(13, 12), background=c['accent'], foreground='#fff')
        style.map('Primary.TButton', background=[('active', '#1d4ed8'),
                                                 ('disabled', '#dce4ee')],
                  foreground=[('disabled', '#8796a9')])
        style.configure('Quiet.TButton', font=('Yu Gothic UI', 9), padding=(7, 5),
                        background=c['panel'], foreground='#395573')
        style.configure('TCombobox', padding=6)
        style.configure('TEntry', padding=5)
        style.configure('TCheckbutton', background=c['panel'], foreground=c['ink'])
        style.configure('TSeparator', background=c['line'])

        root = ttk.Frame(self, style='App.TFrame', padding=(20, 13, 20, 13))
        root.pack(fill='both', expand=True)
        header = ttk.Frame(root, style='App.TFrame')
        header.pack(fill='x', pady=(0, 15))
        # The header labels use the root background, not the card surface.
        title = ttk.Frame(header, style='App.TFrame')
        title.pack(side='left', fill='x', expand=True)
        ttk.Label(title, text='MAB Studio', style='PageTitle.TLabel').pack(anchor='w')
        tk.Label(title, text='日本語で設計する  ·  ローカルAI  ·  Minecraft Education',
                 font=('Yu Gothic UI', 10), fg=c['muted'], bg=c['bg']).pack(anchor='w')
        ttk.Button(header, text='保存した建築', command=self.browse_saved_designs,
                   style='Quiet.TButton').pack(side='right', padx=(7, 0))
        ttk.Button(header, text='詳細設定・接続診断', command=self._open_advanced,
                   style='Quiet.TButton').pack(side='right')
        body = ttk.Frame(root, style='App.TFrame')
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, minsize=320, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, style='Panel.TFrame', padding=(18, 11, 18, 10), width=345)
        right = ttk.Frame(body, style='Panel.TFrame', padding=(14, 14, 14, 13))
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 13))
        left.pack_propagate(False)
        right.grid(row=0, column=1, sticky='nsew')
        left.columnconfigure(0, weight=1)

        # Packing the action bar FIRST reserves space at the bottom of the
        # fixed-width left panel, even with larger Windows text scaling.
        # The prompt is the only vertically flexible part of the panel.
        # Without this the Text widget's requested height pushed the actual
        # generate button beyond the frame (reported on Windows 11).
        self._creation_actions = ttk.Frame(left, style='Panel.TFrame')
        self._creation_actions.pack(side='bottom', fill='x')

        ttk.Label(left, text='AIモデル', style='Section.TLabel').pack(anchor='w')
        ttk.Label(left, text='モデルは事前ロードが必要（自動起動しません）',
                  style='Muted.TLabel').pack(anchor='w', pady=(2, 9))
        self.provider_box = ttk.Combobox(left, textvariable=self.llm_provider,
                              values=('Ollama', 'LM Studio'), state='readonly')
        self.provider_box.pack(fill='x')
        self.provider_box.bind('<<ComboboxSelected>>', lambda _: self._provider_changed())
        mrow = ttk.Frame(left, style='Panel.TFrame')
        mrow.pack(fill='x', pady=(8, 6))
        mrow.columnconfigure(0, weight=1)
        self.model_box = ttk.Combobox(mrow, textvariable=self.model)
        self.model_box.grid(row=0, column=0, sticky='ew', padx=(0, 5))
        self.model_box.bind('<<ComboboxSelected>>', lambda _: self._remember_model_choice())
        self.models_btn = ttk.Button(mrow, text='更新', command=self.refresh_models,
                                     style='Quiet.TButton')
        self.models_btn.grid(row=0, column=1)
        self.unload_btn = ttk.Button(left, text='選択モデルをアンロード（メモリ解放）',
                                     command=self.unload_selected_model,
                                     style='Quiet.TButton')
        self.unload_btn.pack(anchor='w', pady=(0, 10))
        ttk.Separator(left).pack(fill='x', pady=(2, 8))

        ttk.Label(left, text='どんな建築を作りたい？', style='Section.TLabel').pack(anchor='w')
        ttk.Label(left, text='日本語で自由に指示できます', style='Muted.TLabel').pack(anchor='w', pady=(2, 9))
        self.prompt = tk.Text(left, height=8, wrap='word', font=('Yu Gothic UI', 11),
                              relief='flat', borderwidth=0, bg='#f5f8fd', fg=c['ink'],
                              padx=12, pady=12, insertbackground=c['ink'],
                              highlightbackground=c['line'], highlightcolor=c['accent'],
                              highlightthickness=1, undo=True)
        self.prompt.pack(fill='both', expand=True, pady=(0, 8))
        self.prompt.insert('1.0', '未来的な4階建てのビルを作って。窓がたくさんあるように。')
        self.generate_btn = ttk.Button(self._creation_actions, text='AIで建築を設計する',
                                       command=self.generate, style='Primary.TButton')
        self.generate_btn.pack(fill='x', pady=(0, 3))
        ttk.Label(self._creation_actions,
                  text='SOURCE不合格時はAIが1回自動修正（最大2回推論）',
                  style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        self.repair_generation_btn = ttk.Button(
            self._creation_actions, text='検証不合格のAI案を修正（1回）',
            command=self.repair_failed_generation)
        # Only visible when one real generated proposal failed SOURCE validation.
        self.revise_btn = ttk.Button(self._creation_actions, text='この指示で今の建築を修正',
                                     command=self.revise_design, state='disabled')
        self.revise_btn.pack(fill='x', pady=(0, 6))
        self.sample_btn = ttk.Button(self._creation_actions, text='AIなしでサンプルを見る',
                                     command=self.show_sample, style='Quiet.TButton')
        self.sample_btn.pack(anchor='w')
        self.import_v11_btn = ttk.Button(
            self._creation_actions, text='V11のSOURCE/成果物JSONを読み込む',
            command=self.import_external_v11_source, style='Quiet.TButton')
        self.import_v11_btn.pack(anchor='w', pady=(3, 0))
        self.v11_ready_btn = ttk.Button(
            self._creation_actions, text='V11の新しい建築案を確認',
            command=self.import_latest_v11_source, style='Quiet.TButton')
        # Only appears when a producer has published a compatible SOURCE;
        # it is never accepted as a saved project without a separate click.
        # Keyboard alternative when Windows display scaling limits visible space.
        # ttk.Button.invoke() respects its disabled state, including during AI work.
        self.prompt.bind('<Control-Return>', self._submit_prompt_shortcut)
        # Construction is already labelled disabled in the status bar;
        # repeating a multi-line warning here used valuable vertical space.

        top = ttk.Frame(right, style='Panel.TFrame')
        top.pack(fill='x', pady=(0, 10))
        self._preview_title = ttk.Label(top, text='3Dプレビュー', style='Section.TLabel')
        self.gpu_view_btn = ttk.Button(top, text='高精細3D（アプリ内）', command=self.open_gpu_preview,
                                       style='Primary.TButton', state='disabled')
        # Keep actions in one frame so they can flow onto a second line on
        # narrower Windows displays, instead of overlapping each other.
        self._preview_actions = ttk.Frame(top, style='Panel.TFrame')
        ttk.Button(self._preview_actions, text='3Dを拡大', command=self.open_large_preview,
                   style='Quiet.TButton').pack(side='right', padx=(7, 0))
        self.export_btn = ttk.Button(self._preview_actions, text='SOURCE JSONを書き出す',
                                     command=self.export_design, style='Quiet.TButton', state='disabled')
        self.export_btn.pack(side='right', padx=(6, 0))
        self.restore_btn = ttk.Button(self._preview_actions, text='前に戻す',
                                      command=self.restore_previous, style='Quiet.TButton', state='disabled')
        self.restore_btn.pack(side='right')

        tools = ttk.Frame(right, style='Panel.TFrame')
        tools.pack(fill='x', pady=(0, 9))
        self.view_box = ttk.Combobox(tools, textvariable=self.view_mode, state='readonly',
                                     width=17, values=('建築のみ', '修正案（未承認）', '変更差分', '地形＋建築'))
        self.view_box.pack(side='left', padx=(0, 7))
        self.view_box.bind('<<ComboboxSelected>>', lambda _: self._refresh_view(preserve_camera=True))
        detail_box = ttk.Combobox(tools, textvariable=self.detail_mode, state='readonly',
                                   width=16, values=tuple(VoxelPreview.DETAIL_LIMITS))
        detail_box.pack(side='left')
        ttk.Button(tools, text='全体表示', command=lambda: self.preview.reset_view(),
                   style='Quiet.TButton').pack(side='left', padx=(8, 0))
        detail_box.bind('<<ComboboxSelected>>', lambda _: self.preview.set_detail(self.detail_mode.get()))
        ttk.Label(tools, text='SOURCE検証済み・施工なし', style='Muted.TLabel').pack(side='right')

        # One-click camera directions for reviewing different facades/floors.
        # Pure view state; no new SOURCE computation, history, or world writes.
        camera_bar = ttk.Frame(right, style='Panel.TFrame')
        camera_bar.pack(fill='x', pady=(0, 7))
        ttk.Label(camera_bar, text='視点', style='Muted.TLabel').pack(side='left', padx=(0, 7))
        self._camera_controls = ttk.Frame(camera_bar, style='Panel.TFrame')
        self._camera_controls.pack(side='left')
        self.camera_buttons = {}
        for name in VoxelPreview.VIEWS:
            button = ttk.Button(self._camera_controls, text=name,
                                command=lambda selected=name: self.preview.set_view(selected),
                                style='Quiet.TButton')
            self.camera_buttons[name] = button
        self._camera_hint = ttk.Label(camera_bar, text='F：全体表示', style='Muted.TLabel')

        self.reviewbar = ttk.Frame(right, style='Panel.TFrame')
        self.reviewbar_label = ttk.Label(self.reviewbar, text='未承認の修正案', style='Section.TLabel')
        self.reviewbar_label.pack(side='left', padx=(0, 12))
        self.approve_draft_btn = ttk.Button(self.reviewbar, text='修正案を承認',
                                            command=self.approve_revision, state='disabled',
                                            style='Primary.TButton')
        self.approve_draft_btn.pack(side='left', padx=(0, 6))
        self.reject_draft_btn = ttk.Button(self.reviewbar, text='却下',
                                           command=self.reject_revision, state='disabled')
        self.reject_draft_btn.pack(side='left')
        self.compare_draft_btn = ttk.Button(self.reviewbar, text='元の建築を見る',
                                            command=self.toggle_draft_comparison,
                                            style='Quiet.TButton', state='disabled')
        self.compare_draft_btn.pack(side='left', padx=(9, 0))
        self._reviewbar_visible = False
        self.viewer_host = ttk.Frame(right, style='Panel.TFrame')
        self.viewer_host.pack(fill='both', expand=True)
        # The one-click native launcher activates the embedded engine from
        # startup; ordinary Python sources continue to work without VTK.
        prefer_native = (os.environ.get('MAB_STUDIO_NATIVE') == '1' or
                         (getattr(sys, 'frozen', False) and native_dependencies_available()))
        if prefer_native and native_dependencies_available():
            try:
                self.preview = NativeVoxelPreview(self.viewer_host)
            except (NativeViewerError, OSError, RuntimeError):
                self.preview = VoxelPreview(self.viewer_host)
        else:
            self.preview = VoxelPreview(self.viewer_host)
        self._large_preview_window = None
        self._large_preview = None
        self._large_preview_target_mode = None
        self.preview.on_scene_changed = self._preview_updated
        self.preview.pack(fill='both', expand=True)
        sectionbar = ttk.Frame(right, style='Panel.TFrame')
        sectionbar.pack(fill='x', pady=(9, 3))
        self.section_toggle = ttk.Checkbutton(sectionbar, text='断面表示',
                                              variable=self.section_enabled,
                                              command=self._section_changed)
        self.section_toggle.pack(side='left')
        ttk.Label(sectionbar, text='軸').pack(side='left', padx=(9, 3))
        self.section_axis_box = ttk.Combobox(sectionbar, textvariable=self.section_axis,
                                             state='readonly', width=4, values=('X', 'Y', 'Z'))
        self.section_axis_box.pack(side='left')
        self.section_axis_box.bind('<<ComboboxSelected>>', lambda _: self._sync_section_controls())
        ttk.Label(sectionbar, text='残す側').pack(side='left', padx=(8, 3))
        self.section_side_box = ttk.Combobox(sectionbar, textvariable=self.section_side,
                                             state='readonly', width=8,
                                             values=('低い側', '高い側'))
        self.section_side_box.pack(side='left')
        self.section_side_box.bind('<<ComboboxSelected>>', lambda _: self._section_changed())
        self.section_view_btn = ttk.Button(sectionbar, text='断面側を見る',
                                           command=self._view_section_face)
        self.section_view_btn.pack(side='left', padx=(5, 0))
        self.section_scale = tk.Scale(sectionbar, from_=0, to=96, orient='horizontal',
                                      variable=self.section_level, showvalue=True, length=175,
                                      resolution=1, bg=c['panel'], highlightthickness=0,
                                      command=lambda _: self._queue_section_change())
        self.section_scale.pack(side='left', padx=(4, 7))
        self._section_hint = ttk.Label(sectionbar, text='表示のみ・SOURCE不変',
                                       style='Muted.TLabel')
        self.section_scale.configure(state='disabled')
        self.caption = tk.StringVar(value='建築のプレビューがここに表示されます')
        ttk.Label(right, textvariable=self.caption, style='Muted.TLabel',
                  wraplength=750).pack(anchor='w', pady=(3, 0))
        self.status = tk.StringVar(value='準備中')
        statusbar = ttk.Frame(root, style='App.TFrame')
        statusbar.pack(fill='x', pady=(12, 0))
        self.cancel_btn = ttk.Button(statusbar, text='生成をキャンセル',
                                     command=self._cancel_active_ai, state='disabled')
        self.cancel_btn.pack(side='right', padx=(8, 0))
        tk.Label(statusbar, textvariable=self.status, bg=c['bg'], fg='#3d5876',
                 anchor='w', font=('Yu Gothic UI', 9)).pack(side='left', fill='x', expand=True)
        tk.Label(statusbar, text='● 施工機能は無効', bg=c['bg'], fg=c['caution'],
                 font=('Yu Gothic UI', 9)).pack(side='right')

        # Advanced controls are constructed once, but never occupy normal view.
        # Keeping the same widget identities preserves tests and all workflows.
        # An on-demand overlay avoids adding a hidden top-level window to normal
        # operation, which otherwise also intercepts history dialogs on Windows.
        self._compact_layout = None
        right.bind('<Configure>', self._on_preview_width)
        self._set_compact_layout(False)
        self._advanced = ttk.Frame(self, style='Panel.TFrame', padding=6)
        advanced = ttk.Frame(self._advanced, style='Panel.TFrame', padding=20)
        advanced.pack(fill='both', expand=True)
        ttk.Label(advanced, text='エンジン管理・地形・Education',
                  style='Section.TLabel').pack(anchor='w')
        ttk.Label(advanced, text='普段の建築には不要です。SOURCE検証とワールド保護条件は変更しません。',
                  style='Muted.TLabel').pack(anchor='w', pady=(3, 14))
        self.engine_state = tk.StringVar(value='エンジンを確認中')
        ttk.Label(advanced, text='MABエンジン', style='Section.TLabel').pack(anchor='w')
        ttk.Label(advanced, textvariable=self.engine_state, style='Muted.TLabel').pack(anchor='w', pady=3)
        self.install_btn = ttk.Button(advanced, text='確認済みB143を導入', command=self.install_baseline)
        self.install_btn.pack(fill='x', pady=(5, 6))
        branchrow = ttk.Frame(advanced)
        branchrow.pack(fill='x', pady=(0, 7))
        branchrow.columnconfigure(0, weight=1)
        ttk.Entry(branchrow, textvariable=self.branch).grid(row=0, column=0, sticky='ew', padx=(0, 6))
        ttk.Button(branchrow, text='追跡先を保存', command=self.save_branch).grid(row=0, column=1)
        updates = ttk.Frame(advanced)
        updates.pack(fill='x', pady=(0, 10))
        self.check_btn = ttk.Button(updates, text='GitHub更新を確認', command=self.check_update)
        self.check_btn.pack(side='left', padx=(0, 6))
        self.apply_btn = ttk.Button(updates, text='検証して更新', command=self.install_pending, state='disabled')
        self.apply_btn.pack(side='left', padx=(0, 6))
        self.rollback_btn = ttk.Button(updates, text='以前のエンジンへ戻す', command=self.rollback)
        self.rollback_btn.pack(side='left')
        ttk.Separator(advanced).pack(fill='x', pady=(7, 14))
        ttk.Label(advanced, text='接続・設計履歴', style='Section.TLabel').pack(anchor='w')
        quick = ttk.Frame(advanced)
        quick.pack(fill='x', pady=(6, 13))
        ttk.Button(quick, text='接続診断', command=self.diagnose).pack(side='left', padx=(0, 6))
        self.load_btn = ttk.Button(quick, text='最後の設計を開く', command=self.load_saved_design)
        self.load_btn.pack(side='left', padx=(0, 6))
        self.browse_btn = ttk.Button(quick, text='保存済み一覧', command=self.browse_saved_designs)
        self.browse_btn.pack(side='left')
        ttk.Checkbutton(advanced, text='AI修正を軽量化（変更要素だけ生成）',
                        variable=self.fast_revision_mode).pack(anchor='w', pady=(0, 8))
        ttk.Separator(advanced).pack(fill='x', pady=(1, 14))
        ttk.Label(advanced, text='地形・Education（読み取り専用）', style='Section.TLabel').pack(anchor='w')
        terrainbar = ttk.Frame(advanced)
        terrainbar.pack(fill='x', pady=(9, 6))
        ttk.Label(terrainbar, text='予定位置 XYZ').pack(side='left')
        self.anchor_entry = ttk.Entry(terrainbar, textvariable=self.place_xyz, width=18)
        self.anchor_entry.pack(side='left', padx=(8, 10))
        self.anchor_entry.bind('<FocusOut>', lambda _: self._refresh_view() if self.view_mode.get()=='地形＋建築' else None)
        self.anchor_entry.bind('<Return>', lambda _: self._refresh_view() if self.view_mode.get()=='地形＋建築' else None)
        self.import_terrain_btn = ttk.Button(terrainbar, text='.mcworldを読み取る', command=self.import_terrain)
        self.import_terrain_btn.pack(side='left')
        ttk.Button(terrainbar, text='B134結果を読み込む',
                   command=self.import_education_diagnostic).pack(side='left', padx=(8, 0))
        educationbar = ttk.Frame(advanced)
        educationbar.pack(fill='x', pady=(0, 8))
        self.edu_live_btn = ttk.Button(educationbar, text='Educationから階段を読取',
                                       command=self.start_education_readonly)
        self.edu_live_btn.pack(side='left', padx=(0, 6))
        self.edu_stop_btn = ttk.Button(educationbar, text='読取待機を停止',
                                       command=self.stop_education_readonly, state='disabled')
        self.edu_stop_btn.pack(side='left')
        self.edu_btn = ttk.Button(advanced, text='Minecraftに施工（未実装・利用不可）',
                                  state='disabled')
        self.edu_btn.pack(anchor='w', pady=(4, 4))
        ttk.Label(advanced, text='B143は設計専用です。施工許可はありません。\n'
                 'Educationへの直接接続は既存の1点読取診断に限定します。',
                 style='Muted.TLabel').pack(anchor='w')
        ttk.Button(advanced, text='閉じる', command=self._advanced.place_forget).pack(side='right', pady=(10, 0))

    def _submit_prompt_shortcut(self, _event):
        self.generate_btn.invoke()
        return 'break'

    def _on_preview_width(self, event):
        # This is a view-only layout switch. Never recompute or mutate SOURCE.
        if event.widget is not None:
            self._set_compact_layout(event.width < 735)

    def _set_compact_layout(self, compact: bool):
        if self._compact_layout is compact:
            return
        self._compact_layout = compact
        self._preview_title.pack_forget()
        self.gpu_view_btn.pack_forget()
        self._preview_actions.pack_forget()
        if compact:
            self._preview_actions.pack(side='bottom', fill='x')
        self._preview_title.pack(side='left')
        self.gpu_view_btn.pack(side='right', padx=(7, 0))
        if not compact:
            self._preview_actions.pack(side='right')
        for i, button in enumerate(self.camera_buttons.values()):
            button.grid(row=i // 3 if compact else 0, column=i % 3 if compact else i,
                        sticky='w', padx=(0, 3), pady=(0, 2))
        if compact:
            self._camera_hint.pack_forget()
            self._section_hint.pack_forget()
        else:
            self._camera_hint.pack(side='right')
            self._section_hint.pack(side='left')

    def _open_advanced(self):
        self._advanced.place(relx=0.5, rely=0.5, anchor='center',
                             relwidth=0.72, relheight=0.92)
        self._advanced.lift()
        self._advanced.focus_set()

    def _startup(self):
        self._update_engine_label()
        # Open an existing, independently recompiled SOURCE project automatically
        # on ordinary startup. No provider inference or world access.
        if self.manager.active_path:
            self._restore_last_session()
        else:
            def ready(_):
                self._update_engine_label()
                self.caption.set('安全確認済みB143の初期導入完了。建築を指示できます。')
                self._restore_last_session()
            self._start('初回起動：固定B143を安全確認して導入中',
                        lambda: self.manager.install(BASELINE_SHA), ready, quiet=True)

    def _restore_last_session(self):
        """Reopen the last approved design without changing history or world.

        A corrupt or incompatible last snapshot is held, never silently
        reconstructed or overwritten. The local model list is still available.
        """
        engine = self.manager.active_path
        if engine is None:
            return
        def work():
            try:
                saved = self.history.head()
                if saved is None:
                    return None, None, None
                compiled = run_bridge(engine, saved['design'])
                if (compiled['sourceSha256'] != saved['sourceSha256'] or
                        compiled['geometrySha256'] != saved['geometrySha256']):
                    raise StudioError('保存時と現在のSOURCEが一致しません')
                return saved, compiled, None
            except (StudioError, OSError, ValueError, KeyError, TypeError) as exc:
                return None, None, str(exc)[:220]
        def done(result):
            saved, compiled, problem = result
            if problem:
                self.caption.set('前回の設計は安全に開けませんでした。履歴を変更していません: '+problem)
            elif saved is not None:
                self.failed_generation = None
                self.current_snapshot = saved
                self.design = saved['design']
                self.scene = compiled
                self.delta = None
                self.pending_revision = None
                self._sync_preview_outputs(self.design, self.scene)
                self.view_mode.set('建築のみ')
                self._refresh_view()
                self._refresh_buttons()
                self.export_btn.configure(state='normal')
                self.caption.set('前回の建築「'+self.design['title']+'」をSOURCE再検証して復元（ワールド未変更）')
            self.refresh_models()
        self._start('前回のSOURCE建築を安全に再開中（推論・施工なし）', work, done, quiet=True)

    def _update_engine_label(self):
        sha=self.manager.active_sha
        self.engine_state.set('使用中: '+sha[:12]+'…  /  更新はコミットSHA固定' if self.manager.active_path
                              else '未導入 / 最初にB143を導入してください')
        self.generate_btn.configure(state='normal' if self.manager.active_path and not self.busy and self.pending_revision is None else 'disabled')
        self.sample_btn.configure(state='normal' if self.manager.active_path and not self.busy and self.pending_revision is None else 'disabled')
        self.install_btn.configure(state='normal' if not self.busy and not self.manager.active_path else 'disabled')
        self.check_btn.configure(state='normal' if not self.busy and self.pending_revision is None else 'disabled')
        self.rollback_btn.configure(state='normal' if not self.busy and self.pending_revision is None else 'disabled')
        self.apply_btn.configure(state='normal' if self.pending_sha and not self.busy and self.pending_revision is None else 'disabled')
        if hasattr(self, 'cancel_btn'):
            self.cancel_btn.configure(state='normal' if self.busy and self._active_cancel_event is not None
                                      and not self._active_cancel_event.is_set() else 'disabled')
        self._refresh_buttons()

    def _start(self, title, fn, callback=None, quiet=False, cancel_event=None):
        if self.busy:
            return
        # Prevent finalizers of previously closed Tk variables from executing
        # in this worker; Tcl is main-thread-affine.
        gc.collect()
        self.busy=True
        self._active_cancel_event=cancel_event
        self.status.set(title)
        self._update_engine_label()
        def work():
            try:
                result=fn()
                self.pending_events.put(('ok',title,result,callback,quiet,cancel_event))
            except Exception as e:
                kind='cancelled' if cancel_event is not None and cancel_event.is_set() else 'error'
                self.pending_events.put((kind,title,str(e),callback,quiet,cancel_event))
        threading.Thread(target=work,daemon=True).start()

    def _cancel_active_ai(self):
        token=self._active_cancel_event
        if token is None or token.is_set():
            return
        token.set()
        self.cancel_btn.configure(state='disabled')
        self.status.set('キャンセル要求済み：回答は破棄します。サーバー側の推論停止は未確認です。')

    def _poll(self):
        try:
            while True:
                item=self.pending_events.get_nowait()
                kind,title,result,callback,quiet=item[:5]
                cancel_event=item[5] if len(item)>5 else None
                if kind=='progress':
                    # A cancelled request can enqueue progress just before a new
                    # request begins. Ignore that stale message, not just the
                    # cancelled request's final response.
                    if (self.busy and cancel_event is not None
                            and self._active_cancel_event is cancel_event
                            and not cancel_event.is_set()):
                        self.status.set(str(result))
                    continue
                self.busy=False
                if cancel_event is not None:
                    was_cancelled=cancel_event.is_set() or kind=='cancelled'
                    if self._active_cancel_event is cancel_event:
                        self._active_cancel_event=None
                    if was_cancelled:
                        self.status.set('キャンセル済み：回答は破棄しました。サーバー側の推論停止は確認できません。')
                        self._update_engine_label()
                        continue
                if title.startswith('Education直接読取'):
                    self._education_running=False
                if kind=='error':
                    if title.startswith('Education直接読取') and '接続待機を中止しました' in str(result):
                        quiet=True
                    if quiet and title.startswith('GitHub'):
                        self.status.set('GitHub更新確認はオフラインです。導入済みエンジンは使えます。')
                    else:
                        self.status.set('失敗: '+str(result))
                    if not quiet:messagebox.showerror(title,str(result))
                else:
                    self.status.set('完了: '+title)
                    if callback:
                        try:callback(result)
                        except Exception as e:
                            self.status.set('表示に失敗: '+str(e))
                self._update_engine_label()
        except queue.Empty:
            pass
        if not self._closed:
            self._poll_after = self.after(120,self._poll)

    def _invalidate_scene(self):
        self.failed_generation = None
        self.scene = None
        self.preview.show(None)
        self._sync_section_controls()
        self.export_btn.configure(state='disabled')
        self.delta = None
        self.pending_revision = None
        self.design = None
        self.current_snapshot = None
        self._refresh_buttons()

    def _refresh_buttons(self):
        ready = not self.busy and self.pending_revision is None
        new_draft = (self.pending_revision is not None
                     and self.pending_revision.get('kind') == 'new')
        can_repair = (ready and self.failed_generation is not None
                      and self.manager.active_path is not None)
        if can_repair:
            if not self.repair_generation_btn.winfo_manager():
                self.repair_generation_btn.pack(fill='x', pady=(0, 6), before=self.revise_btn)
            self.repair_generation_btn.configure(state='normal')
        elif self.repair_generation_btn.winfo_manager():
            self.repair_generation_btn.pack_forget()
        self.revise_btn.configure(
            state='normal' if self.manager.active_path and not self.busy and (
                (self.design and ready) or new_draft) else 'disabled',
            text='この指示で未承認の生成案を修正' if new_draft else 'この指示で今の建築を修正')
        self.export_btn.configure(state='normal' if self.scene is not None and ready else 'disabled')
        self.restore_btn.configure(state='normal' if self.current_snapshot and self.current_snapshot.get('parentId') and ready else 'disabled')
        self.import_v11_btn.configure(state='normal' if ready and self.manager.active_path else 'disabled')
        self.load_btn.configure(state='normal' if ready else 'disabled')
        self.browse_btn.configure(state='normal' if ready else 'disabled')
        self.approve_draft_btn.configure(state='normal' if not self.busy and self.pending_revision else 'disabled')
        self.reject_draft_btn.configure(state='normal' if not self.busy and self.pending_revision else 'disabled')
        self.compare_draft_btn.configure(state='normal' if not self.busy and self.pending_revision else 'disabled')
        if self.pending_revision is not None:
            self.reviewbar_label.configure(text=('新規案・未保存' if new_draft
                                                 else '未承認の修正案'))
            self.approve_draft_btn.configure(text='承認・保存' if new_draft else '修正案を承認')
            self.compare_draft_btn.configure(
                state='normal' if not self.busy and self.scene is not None else 'disabled')
        self.edu_live_btn.configure(state='normal' if not self.busy else 'disabled')
        self.edu_stop_btn.configure(state='normal' if self._education_running else 'disabled')
        self.gpu_view_btn.configure(state='normal' if not self.busy and self.preview.scene else 'disabled',
                                    text='軽量3Dに切替' if isinstance(self.preview, NativeVoxelPreview) else '高精細3D（アプリ内）')
        self.unload_btn.configure(state='normal' if not self.busy and bool(self.model.get().strip()) else 'disabled')
        self.models_btn.configure(state='normal' if not self.busy else 'disabled')
        self.model_box.configure(state='normal' if not self.busy else 'disabled')
        self.provider_box.configure(state='readonly' if not self.busy else 'disabled')
        if self.pending_revision is not None and not self._reviewbar_visible:
            self.reviewbar.pack(fill='x', before=self.viewer_host, pady=(0, 9))
            self._reviewbar_visible = True
        elif self.pending_revision is None and self._reviewbar_visible:
            self.reviewbar.pack_forget()
            self._reviewbar_visible = False

    def _coords(self):
        try:
            tokens = self.place_xyz.get().replace(',', ' ').split()
            if len(tokens) != 3:
                raise ValueError('expected 3')
            coordinates = tuple(int(t) for t in tokens)
            if any(abs(x) > 30000000 for x in coordinates):
                raise ValueError('beyond bounds')
            return coordinates
        except ValueError as exc:
            raise StudioError('予定位置は X Y Z の整数3個で入力してください') from exc

    def _on_anchor_changed(self, *_):
        # Editing the anchor must immediately retire the old overlay. Otherwise
        # users could mistake an old position's terrain for the newly typed XYZ.
        if self._closed or self.terrain is None or self.view_mode.get()!='地形＋建築':
            return
        self.terrain_report = None
        self.preview.show(self.scene)
        self.status.set('予定位置を変更しました。古い地形の重ね合わせを解除しました。新しい位置では.mcworldを再読取してください。')

    def _queue_section_change(self):
        # A 160,000-cell source can be expensive to mesh. Do not rebuild the
        # scene for every mouse-move while a floor slider is being dragged.
        if self._closed:
            return
        if self._section_after is not None:
            self.after_cancel(self._section_after)
        self._section_after = self.after(100, self._apply_queued_section)

    def _apply_queued_section(self):
        self._section_after = None
        self._section_changed()

    def _section_changed(self):
        # Renderer-only section; SOURCE remains immutable.
        if self._closed or not hasattr(self,'preview'):
            return
        level = self.section_level.get() if self.section_enabled.get() else None
        axis = self.section_axis.get().lower()
        side = 'low' if self.section_side.get() == '低い側' else 'high'
        if hasattr(self,'section_scale'):
            self.section_scale.configure(state='normal' if self.section_enabled.get() and self.preview.scene else 'disabled')
        if hasattr(self.preview, 'set_section'):
            self.preview.set_section(axis, level, side)
        else:
            self.preview.set_cut_level(level if axis == 'y' else None)

    def _view_section_face(self):
        if not self.section_enabled.get() or not self.preview.scene:
            self.status.set('内部を見るには「断面表示」を有効にしてください。SOURCEと履歴は変更されません。')
            return
        axis = self.section_axis.get().lower()
        side = 'low' if self.section_side.get() == '低い側' else 'high'
        self.preview.view_section_face(axis, side)
        if self._large_preview is not None:
            try:
                if self._large_preview.winfo_exists():
                    self._large_preview.view_section_face(axis, side)
            except tk.TclError:
                self._large_preview = self._large_preview_window = None
        self.status.set('表示を断面の正面に合わせました。セル・SOURCE・履歴は変更されません。')

    def _sync_section_controls(self):
        # Use the currently displayed scene bounds on the selected axis.
        scene = self.preview.scene
        bounds = scene.get('bounds') if isinstance(scene,dict) else None
        if not isinstance(bounds,dict):
            self.section_scale.configure(state='disabled')
            if hasattr(self.preview, 'set_section'):
                self.preview.set_section(self.section_axis.get().lower(), None)
            else:
                self.preview.set_cut_level(None)
            return
        axis = self.section_axis.get().upper()
        low, high = bounds.get('min'+axis), bounds.get('max'+axis)
        if type(low) is not int or type(high) is not int or low>high:
            self.section_scale.configure(state='disabled')
            if hasattr(self.preview, 'set_section'):
                self.preview.set_section(axis.lower(), None)
            else:
                self.preview.set_cut_level(None)
            return
        previous_level = self.section_level.get()
        self.section_scale.configure(from_=low,to=high,
                                     state='normal' if self.section_enabled.get() else 'disabled')
        # A newly opened building may have different floor bounds. Avoid
        # dragging a stale section level outside its actual extent.
        if previous_level<low or previous_level>high:
            self.section_level.set((low+high)//2)
        self._section_changed()

    def toggle_draft_comparison(self):
        """A/B review using the SAME camera and section; no history writes."""
        if self.pending_revision is None or self.busy:
            return
        if self.view_mode.get() == '修正案（未承認）':
            self.view_mode.set('建築のみ')
        else:
            self.view_mode.set('修正案（未承認）')
        self._refresh_view(preserve_camera=True)

    def _show_view_scene(self, scene, *, preserve_camera: bool):
        if preserve_camera:
            self.preview.show(scene, preserve_camera=True)
        else:
            self.preview.show(scene)

    def _refresh_view(self, *, preserve_camera: bool = False):
        candidate = self.pending_revision
        if not self.scene and not candidate:
            self.preview.show(None)
            self._sync_section_controls()
            return
        mode = self.view_mode.get()
        active_scene = candidate['scene'] if candidate else self.scene
        active_delta = candidate['delta'] if candidate else self.delta
        if mode == '修正案（未承認）':
            self._show_view_scene(active_scene, preserve_camera=preserve_camera)
            if candidate is None:
                self.status.set('未承認の修正案はありません。承認済み設計を表示しています。')
        elif mode == '変更差分':
            if candidate is not None and candidate.get('kind') == 'new':
                # A new project has no comparable parent. Showing the old
                # approved building here looks like the NEW candidate and can
                # lead to approving an unseen design.
                self._show_view_scene(active_scene, preserve_camera=preserve_camera)
                self.status.set('新規生成案には比較元がありません。未承認の新しい建築を表示しています。')
            elif active_delta is None or not any(active_delta['counts'][p] for p in ('added','removed','replaced')):
                self._show_view_scene(self.scene or active_scene, preserve_camera=preserve_camera)
                self.status.set('変更差分がありません。通常モデルを表示します。')
            else:
                self._show_view_scene(diff_preview_scene(active_delta, active_scene), preserve_camera=preserve_camera)
        elif mode == '地形＋建築':
            if self.terrain is None:
                # For new drafts, do not fall back to an unrelated previously
                # approved building when terrain has not yet been imported.
                unmerged = active_scene if candidate and candidate.get('kind') == 'new' else (self.scene or active_scene)
                self._show_view_scene(unmerged, preserve_camera=preserve_camera)
                self.status.set('地形ブロックは未読取です。.mcworldの読み込みを先に行ってください')
            else:
                try:
                    merged, self.terrain_report = create_overlay_scene(self.terrain, active_scene, self._coords())
                    self._show_view_scene(merged, preserve_camera=preserve_camera)
                    r=self.terrain_report
                    self.status.set('地形照合（未施工）：既存ブロック重複候補 '+str(r['potentialTerrainIntersections'])+
                                    ' / 確認済み空気 '+str(r['projectedKnownAir'])+
                                    ' / 読取不能 '+str(r['projectedUnreadable'])+
                                    ' / 読取範囲外 '+str(r['projectedOutsideSample'])+
                                    ' / 位置を確認できないセル '+str(r['projectedLegacyUnverified']))
                except StudioError as exc:
                    unmerged = active_scene if candidate and candidate.get('kind') == 'new' else (self.scene or active_scene)
                    self._show_view_scene(unmerged, preserve_camera=preserve_camera)
                    self.status.set(str(exc))
        else:
            self._show_view_scene(self.scene or active_scene, preserve_camera=preserve_camera)
        self._sync_section_controls()
        if self.pending_revision is not None:
            self.compare_draft_btn.configure(
                text='修正案を見る' if mode == '建築のみ' else '元の建築を見る')

    def start_education_readonly(self):
        """User-initiated test-world transport. Never performs Minecraft writes."""
        if self.busy or self._education_running:
            return
        try:
            pos=parse_probe_coordinates(self.place_xyz.get().replace(',',' '))
        except StudioError as exc:
            messagebox.showerror('読取位置の確認',str(exc))
            return
        if not messagebox.askyesno('Educationの安全な読取',
                '専用テストワールドの既存オーク階段を確認します。\n'
                f'指定 XYZ: {pos[0]} {pos[1]} {pos[2]}\n\n'
                '読み取り専用testforblockを最大5件送信します。\n'
                '建築、削除、ワールドデータの編集は行いません。\n'
                'Minecraft実機の状態認証や施工許可にはなりません。\n\n'
                '読み取り待機を開始しますか？'):
            return
        self._education_cancel.clear()
        self._education_running=True
        def done(report):
            label=report['outcome']
            value=report['provisionalStateValue']
            messagebox.showinfo('Education読み取り診断（未認証）',
                f'対象XYZ: {report["coords"]}\n'
                f'結果: {label}\n方向候補: {value if value is not None else "不明"}\n'
                f'保存先: {report["readbackPath"]}\n\n'
                '照合は設計・建築認証ではありません。\nワールド書き込み0。')
            self.status.set('Education読取診断を保存しました（状態は独立認証していません）。')
        self._start('Education直接読取：指定階段5照合を待機中',
                    lambda:run_education_readonly(pos,stop=self._education_cancel),done,quiet=False)
        self.status.set('Educationで /wsserver 127.0.0.1:8000 を実行 → チャットに !mab-read （テストワールドのみ）')
        messagebox.showinfo('Minecraft Educationでの操作',
            '1. 専用テストワールドを開いてください。\n'
            '2. ゲーム内コマンド: /wsserver 127.0.0.1:8000\n'
            '3. 接続後、チャットに !mab-read と送ってください。\n\n'
            '接続タイムアウトは約2分半。既存B134等の8000番ポートを使うソフトは終了してください。\n'
            '※ 自動操作・施工はありません。いつでも「読取待機を停止」できます。')

    def stop_education_readonly(self):
        if self._education_running:
            self._education_cancel.set()
            self.status.set('Education読取の停止を要求しました。ワールドは変更しません。')

    def import_education_diagnostic(self):
        # Existing B134 connector does the real read-only WebSocket probing.
        # Studio only accepts its diagnostic report; it never generates commands.
        filename=filedialog.askopenfilename(title='MAB B134の読み取り専用診断JSON',
                                              filetypes=[('JSON診断','*.json')])
        if not filename:
            return
        def done(report):
            messagebox.showinfo('Education B134読取診断（外部結果）',
                '対象XYZ: '+str(report['coords'])+'\n'
                '結果: '+report['outcome']+'\n'
                '方向候補: '+str(report['provisionalStateValue'])+'\n\n'
                'これは外部診断JSONの形式検査のみです。\n'
                'StudioからのMinecraft接続・実ワールドのブロック認証・施工は未実施です。')
            self.status.set('B134外部診断の形式検査が完了しました。ワールド未変更。')
        self._start('B134読み取り専用結果を安全検査中',
                    lambda:import_b134_readback(filename),done)

    def import_terrain(self):
        if self.pending_revision is not None:
            messagebox.showwarning('未承認の修正案', '先に修正案を承認または却下してください。')
            return
        filename = filedialog.askopenfilename(title='Minecraft Educationからエクスポートした.mcworldを選択',
                                              filetypes=[('Minecraft World','*.mcworld')])
        if not filename:
            return
        try: anchor = self._coords()
        except StudioError as e:
            messagebox.showerror('予定位置XYZ',str(e)); return
        # Prefer observing foundation and the above-ground building over
        # spending half the vertical sample on blocks beneath the foundation.
        # MAX_SAMPLE_VOLUME and the 32-cell per-axis cap remain unchanged.
        if self.scene is not None:
            bounds = self.scene['bounds']
            width = bounds['maxX']-bounds['minX']+1
            depth = bounds['maxZ']-bounds['minZ']+1
            story_height = bounds['maxY']-bounds['minY']+1
            sample_size = min(32,max(24,width,depth))
            sample_height = min(32,max(24,story_height+4))
            scan_center=(anchor[0],anchor[1]+sample_height//2-4,anchor[2])
        else:
            sample_size=24
            sample_height=24
            scan_center=anchor
        def work():
            metadata = inspect_mcworld(filename)
            try:
                terrain = read_mcworld_terrain(metadata, scan_center,
                    size=sample_size, height=sample_height, planned_anchor=anchor)
                return metadata, terrain, None
            except StudioError as exc:
                return metadata, None, str(exc)
        def done(result):
            metadata, terrain, issue = result
            self.terrain_archive, self.terrain = metadata, terrain
            self.view_mode.set('地形＋建築')
            if terrain is None:
                self.status.set('ZIP検査成功: ' + metadata['worldName'] + '。地形は未読取: ' + str(issue))
                messagebox.showwarning('地形ブロックは未読取',
                                       'エクスポートされたワールド構造を確認しました。\nただし、地形ブロックはまだ取得できていません。\n' + str(issue))
            else:
                self._refresh_view()
                self.caption.set('実地形スナップショット: ' + metadata['worldName'] +
                                 ' / 既知ブロック: ' + str(len(terrain['cells'])) +
                                 ' / 読取領域: ' + str(terrain['size']) +
                                 ' / 未読取: ' + str(terrain['unknown']) + '（Minecraft未変更）')
        self._start('安全なコピーから.mcworld地形を解析中', work, done)

    def install_baseline(self):
        if self.pending_revision is not None:
            return
        self._start('B143エンジンを検証して初期導入中',lambda:self.manager.install(BASELINE_SHA),
                    lambda r:(self._invalidate_scene(),self._update_engine_label(),
                              self.caption.set('B143導入とSOURCE互換性試験が完了（施工は無効）')))

    def save_branch(self):
        try:
            prior = self.manager.state.get('tracked_branch', DEFAULT_BRANCH)
            self.manager.save_branch(self.branch.get().strip())
            if prior != self.branch.get().strip():
                self.pending_sha = None
                self._update_engine_label()
            self.status.set('追跡ブランチを保存。アプリは自動で本体を書き換えません。')
        except Exception as e:messagebox.showerror('入力エラー',str(e))

    def check_update(self,silent=False):
        try:self.manager.save_branch(self.branch.get().strip())
        except StudioError as e:
            if not silent:messagebox.showerror('追跡ブランチ',str(e))
            return
        target_branch = self.branch.get().strip()
        def done(sha):
            if self.branch.get().strip() != target_branch:
                self.pending_sha = None
                self.status.set('追跡先が変わったため更新候補を破棄しました。再確認してください。')
                return
            if sha == self.manager.active_sha:
                self.pending_sha=None
                self.status.set('GitHub確認：現在のエンジンは追跡ブランチと同じSHAです。')
            elif sha != BASELINE_SHA:
                # A branch moving is not an engine security/compatibility review.
                # Show the newer commit for awareness, but do not offer to apply
                # it through the single-file approved B143 installer.
                self.pending_sha=None
                self.status.set('MABの新しいコミット '+sha[:12]+'… を検出。Studioで未審査のため導入保留（旧版は継続使用可）。')
            else:
                self.pending_sha=sha
                self.status.set('審査済みB143の更新候補: '+sha[:12]+'…  手動適用まで切り替えません。')
            self._update_engine_label()
        self._start('GitHubの最新コミットを確認中',lambda:branch_sha(target_branch),done,quiet=silent)

    def install_pending(self):
        sha=self.pending_sha
        if not sha or sha==self.manager.active_sha:
            return
        if sha != BASELINE_SHA:
            messagebox.showerror('更新保留','未審査のMABコミットは適用できません。')
            return
        self._start('更新候補を隔離導入しSOURCE互換性を検証中',lambda:self.manager.install(sha),
                    lambda r:(setattr(self,'pending_sha',None),self._invalidate_scene(),
                              self._update_engine_label(),self.caption.set('更新適用済み。以前のエンジンへ復元できます。')))

    def rollback(self):
        self._start('旧エンジンを検証して復元中',self.manager.rollback,
                    lambda sha:(self._invalidate_scene(),self.caption.set('復元しました: '+sha[:12]+'…')))

    def _remember_model_choice(self):
        # Only the model identifier is remembered. Never load the model, run
        # inference, or persist a user prompt as a side effect of choosing it.
        save_local_choice(self.manager.root,self.llm_provider.get(),self.model.get().strip())

    def _provider_changed(self):
        self.model.set('')
        self._remember_model_choice()
        self.refresh_models()

    def refresh_models(self):
        provider=self.llm_provider.get()
        def done(names):
            if self.llm_provider.get()!=provider:
                return  # provider changed during request, never cross-assign IDs
            self.model_box['values']=names
            chosen=self.model.get().strip()
            if names and not chosen:
                self.model.set(names[0])
                self._remember_model_choice()
            elif names and chosen not in names:
                self.status.set('以前のモデル '+chosen[:45]+' が現在の一覧にありません。使うモデルを選び直してください。')
                return
            self.status.set(f'{provider}: {len(names)}件のモデルを取得。一覧には未ロードも含まれます。使用するモデルは先にロードしてください。')
        self._start(provider+'のモデル一覧を取得中',lambda:llm_models(provider),done,quiet=True)

    def unload_selected_model(self):
        # A click targets one exact loaded model, never every loaded model.
        # The busy gate prevents this app from unloading its own in-flight request.
        if self.busy:
            messagebox.showwarning('アンロードを保留',
                '生成や検証が実行中です。終了後にアンロードできます。')
            return
        provider = self.llm_provider.get()
        model = self.model.get().strip()
        if not model:
            messagebox.showwarning('モデル未選択', 'アンロードするモデルを選択してください。')
            return
        def done(report):
            self.status.set(provider + ' の ' + model + ' をアンロードしました。別のモデルには触れていません。')
            # Retain the chosen ID for convenient future reload; never silently load.
        self._start('選択したローカルAIモデルをメモリからアンロード中',
                    lambda: unload_local_model(provider, model), done)

    def diagnose(self):
        provider = self.llm_provider.get()
        def done(report):
            if self.llm_provider.get() == provider and report['models']:
                self.model_box['values'] = report['models']
                if not self.model.get().strip():
                    self.model.set(report['models'][0])
                    self._remember_model_choice()
            messagebox.showinfo('MAB Studio 接続診断（読み取りのみ）', readiness_text(report))
            self.status.set('接続診断完了：MAB / Node / ローカルAIのみ。Minecraft未接続。')
        self._start('MAB SOURCEとローカルAIの読み取り診断中',
                    lambda: check_local_setup(self.manager,provider),done)

    def _confirm_replace_failed_generation(self):
        """Warn only when an explicit new proposal could replace a repairable one.

        Keep the rejected candidate until a *new* candidate is actually shown;
        provider failures, cancelled requests, and file errors cannot erase it.
        """
        if self.failed_generation is None:
            return True
        return messagebox.askyesno(
            '修正待ちのAI案',
            'B143で不合格になったAI案が修正待ちです。\n'
            '別の案が生成・読み込みできた場合、この不合格案に戻れなくなります。\n'
            '新しい案へ進みますか？', parent=self)

    def _check_v11_ready_source(self):
        # File discovery only: no model inference, SOURCE compiler or history
        # mutation. A new V11 research commit alone is NOT a compatible release.
        if self._closed:
            return
        try:
            ready = newest_v11_ready_source(self.manager.root, self._v11_seen_sha)
            self._v11_ready = ready
            if ready:
                self.v11_ready_btn.configure(text='V11の新しい案を確認: ' + ready[2][:18])
                if not self.v11_ready_btn.winfo_manager():
                    self.v11_ready_btn.pack(anchor='w', pady=(3, 0))
            elif self.v11_ready_btn.winfo_manager():
                self.v11_ready_btn.pack_forget()
        except (OSError, ValueError):
            # The GUI continues to work when a sync drive is offline.
            pass
        self._v11_watch_after = self.after(120000, self._check_v11_ready_source)

    def import_latest_v11_source(self):
        ready = self._v11_ready
        if not ready or self.busy or self.pending_revision is not None:
            return
        # If a cloud sync replaced the file after discovery, let the next
        # poll inspect the new proposal rather than importing unseen bytes.
        try:
            import hashlib
            if hashlib.sha256(ready[0].read_bytes()).hexdigest() != ready[1]:
                self._v11_ready = None
                self.v11_ready_btn.pack_forget()
                return
        except OSError:
            self._v11_ready = None
            self.v11_ready_btn.pack_forget()
            return
        def on_loaded():
            self._v11_seen_sha = ready[1]
            self._v11_ready = None
            self.v11_ready_btn.pack_forget()
        self._import_v11_filename(str(ready[0]), on_imported=on_loaded)

    def import_external_v11_source(self):
        """Open an external B143 design as a NEW unapproved project proposal.

        No model inference, conversion, accepted history writes, or world I/O.
        The frozen SHA-checked B143 is the only geometry compiler.
        """
        if self.busy or self.pending_revision is not None:
            return
        engine = self.manager.active_path
        if engine is None:
            messagebox.showwarning('固定B143未導入', '確認済みB143を導入してから読み込んでください。')
            return
        filename = filedialog.askopenfilename(
            title='V11のB143設計またはTRUE_B143_PRODUCT JSONを選ぶ（未承認）',
            filetypes=[('B143 SOURCE JSON', '*.json')])
        if not filename:
            return False
        return self._import_v11_filename(filename)

    def _import_v11_filename(self, filename, on_imported=None):
        if self.busy or self.pending_revision is not None:
            return False
        engine = self.manager.active_path
        if engine is None:
            return False
        try:
            previous = self.history.head()
            expected_head_id = previous['revisionId'] if previous else None
        except (StudioError, OSError, ValueError) as exc:
            messagebox.showerror('履歴保護', '保存履歴を確認できません: ' + str(exc))
            return
        if not self._confirm_replace_failed_generation():
            return
        engine_sha = self.manager.active_sha
        def work():
            design = read_external_b143_source(filename)
            try:
                scene = run_bridge(engine, design)
            except StudioError as exc:
                if 'B143_' in str(exc):
                    raise StudioError('この外部SOURCEは固定B143で不合格です。'
                                      '自動変換せず元ファイルを維持しました: ' + str(exc)) from exc
                raise
            return design, scene
        def done(result):
            if self.manager.active_sha != engine_sha:
                raise StudioError('B143が読み込み中に変わりました。外部案は破棄しました')
            design, scene = result
            self._hold_generated_candidate(design, scene, 'V11_EXTERNAL_SOURCE', '', expected_head_id)
            self.caption.set('外部B143 SOURCE：' + str(design['title'])[:100]
                             + ' | 未承認・未保存。3Dで確認後に承認・破棄してください')
            self.status.set('外部SOURCEを固定B143で検証しました。元ファイル・保存履歴・Minecraftは未変更です。')
            if on_imported is not None:
                on_imported()
        self._start('外部V11 SOURCEを固定B143で読み込み・3D検証中', work, done)
        return True

    def generate(self):
        if self.pending_revision is not None:
            messagebox.showwarning('未承認の修正案', '先に修正案を承認または却下してください。')
            return
        prompt=self.prompt.get('1.0','end').strip()
        provider=self.llm_provider.get()
        model=self.model.get().strip()
        engine=self.manager.active_path
        if not engine:
            messagebox.showwarning('未導入','MAB B143エンジンを導入してください。')
            return
        if not prompt or not model:
            messagebox.showwarning('入力不足','日本語指示とローカルLLMモデルを選んでください。')
            return
        self._remember_model_choice()
        try:
            existing = self.history.head()
            expected_head_id = existing['revisionId'] if existing else None
        except (StudioError, OSError, ValueError) as exc:
            messagebox.showerror('建築履歴の保護', '現在の保存履歴を確認できないため生成を開始しません: '+str(exc))
            return
        # A failed model connection or cancelled replacement must not erase
        # an already expensive B143-rejected candidate awaiting explicit repair.
        if not self._confirm_replace_failed_generation():
            return
        cancel_event = threading.Event()
        def work():
            # An installed-but-idle Ollama model would otherwise auto-load here.
            # Only the user's already loaded, exact selection may be used.
            model_status = verify_loaded_model(provider, model)
            # The picker may hold a catalog key, but the LM Studio request must
            # use the one exact already-loaded instance, not a JIT-loadable key.
            inference_model = model_status['inferenceModel']
            # One user action permits one model inference, not a hidden retry.
            design = ask_llm(provider, inference_model, prompt,
                             on_progress=lambda message: self.pending_events.put(
                                 ('progress', 'LOCAL_LLM', message, None, True, cancel_event)),
                             cancel_event=cancel_event)
            if cancel_event.is_set():
                raise ModelCancelled('生成をキャンセルしました。回答を破棄しました。')
            try:
                scene = run_bridge(engine, design)
            except StudioError as exc:
                if 'B143_' not in str(exc):
                    raise  # Do not disguise engine or safety failures as AI defects.
                error = str(exc)
                if not self.auto_repair_invalid_generation:
                    return 'invalid', design, error
                # Sequential, bounded second request, explicitly disclosed in
                # the UI. No change to B143, SOURCE, history or Minecraft.
                self.pending_events.put((
                    'progress', 'LOCAL_LLM',
                    '最初の案がSOURCE不合格。AIが元の案を1回だけ自動修正中…',
                    None, True, cancel_event))
                try:
                    corrected = ask_llm(
                        provider, inference_model, prompt,
                        error=error, previous_design=design,
                        on_progress=lambda message: self.pending_events.put(
                            ('progress', 'LOCAL_LLM', message, None, True, cancel_event)),
                        cancel_event=cancel_event)
                except ModelCancelled:
                    raise
                except StudioError as retry_exc:
                    # Preserve the original rejected proposal for a future
                    # explicit repair, not a silent discard or false success.
                    return ('invalid', design,
                            error + ' / 自動修正は完了しませんでした: ' + str(retry_exc)[:220])
                if cancel_event.is_set():
                    raise ModelCancelled('修正をキャンセルしました。回答を破棄しました。')
                try:
                    corrected_scene = run_bridge(engine, corrected)
                except StudioError as retry_exc:
                    if 'B143_' not in str(retry_exc):
                        raise
                    return 'invalid', corrected, str(retry_exc)
                return 'valid', corrected, corrected_scene
            return 'valid', design, scene
        def done(res):
            state, design, result = res
            if state == 'invalid':
                self._hold_failed_generation(design, result, prompt, provider, model,
                                             expected_head_id)
            else:
                self._hold_generated_candidate(design, result, provider, model,
                                               expected_head_id)
        self._start('ローカルAIで設計・必要なら1回修正・MAB SOURCE検証中',work,done,
                    cancel_event=cancel_event)

    def _hold_generated_candidate(self, design, scene, provider, model, expected_head_id):
        """Present B143-verified new design without silently persisting it.

        Initial buildings and repairs share this explicit approval step.
        The currently approved building, output files and history stay intact.
        """
        head = self.history.head()
        if (head['revisionId'] if head else None) != expected_head_id:
            raise StudioError('建築履歴を保護: 別の操作で保存履歴が変わりました。AI生成案は採用しません')
        self.failed_generation = None
        self.pending_revision = {
            'kind': 'new', 'design': design, 'scene': scene, 'delta': None,
            'engineSha': self.manager.active_sha, 'expectedHead': expected_head_id,
            'provider': provider, 'model': model,
        }
        self.view_mode.set('修正案（未承認）')
        # This is a different building: fit its bounds rather than inheriting
        # an unrelated old building's tiny/offscreen camera scale.
        self._refresh_view()
        self._refresh_buttons()
        self.caption.set(f'{scene.get("title", "新しい建築")} | '
                         f'{scene["stats"]["voxels"]:,}セル | B143 SOURCE合格 | '
                         '未承認・未保存。3Dと動線を確認し、修正または承認・破棄してください。')
        self.status.set('AI生成案を3Dに表示しました。承認するまで保存履歴とSOURCE出力は変更されません。')

    def _hold_failed_generation(self, design, error, prompt, provider, model, expected_head_id):
        """Retain ONLY an invalid AI proposal for one user-triggered repair."""
        self.failed_generation = {
            'design': design, 'error': error, 'prompt': prompt,
            'provider': provider, 'model': model,
            'engineSha': self.manager.active_sha,
            'expectedHead': expected_head_id,
        }
        self._refresh_buttons()
        # No scene/history/latest-output changes; avoid false SOURCE PASS.
        self.caption.set('AI案はB143検証で不合格です（保存・施工していません）。'
                         '同じ案を修正するには左の「検証不合格のAI案を修正」を押してください。')
        self.status.set('SOURCE不合格（自動再推論なし）: ' + str(error)[:300])

    def repair_failed_generation(self):
        """Explicit one-request repair of a SOURCE-rejected *new* design."""
        candidate = self.failed_generation
        if self.busy or self.pending_revision is not None or candidate is None:
            return
        engine = self.manager.active_path
        if engine is None or self.manager.active_sha != candidate['engineSha']:
            messagebox.showerror('修正案を保護', 'B143エンジンが変わりました。古い不合格案は修正できません。')
            self.failed_generation = None
            self._refresh_buttons()
            return
        try:
            head = self.history.head()
            if (head['revisionId'] if head else None) != candidate['expectedHead']:
                raise StudioError('ほかの操作で保存履歴が更新されました。古い不合格案を破棄します。')
        except (StudioError, OSError, ValueError) as exc:
            self.failed_generation = None
            self._refresh_buttons()
            messagebox.showerror('修正案を保護', str(exc))
            return
        provider, model = candidate['provider'], candidate['model']
        cancel_event = threading.Event()
        def work():
            status = verify_loaded_model(provider, model)
            repaired = ask_llm(
                provider, status['inferenceModel'], candidate['prompt'],
                error=candidate['error'], previous_design=candidate['design'],
                on_progress=lambda message: self.pending_events.put(
                    ('progress', 'LOCAL_LLM', message, None, True, cancel_event)),
                cancel_event=cancel_event)
            if cancel_event.is_set():
                raise ModelCancelled('修正をキャンセルしました。回答は破棄しました。')
            try:
                scene = run_bridge(engine, repaired)
            except StudioError as exc:
                if 'B143_' not in str(exc):
                    raise
                return 'invalid', repaired, str(exc)
            return 'valid', repaired, scene
        def done(res):
            state, design, result = res
            if state == 'invalid':
                # Still invalid: one click performed exactly one inference.
                # Offer another EXPLICIT click without hidden retries.
                self._hold_failed_generation(design, result, candidate['prompt'],
                                             provider, model, candidate['expectedHead'])
            else:
                self._hold_generated_candidate(design, result, provider, model,
                                               candidate['expectedHead'])
        self._start('不合格AI案を1回だけ修正しB143検証中（自動再試行なし）',
                    work, done, cancel_event=cancel_event)

    def show_sample(self):
        if self.pending_revision is not None:
            return
        engine = self.manager.active_path
        if not engine:
            messagebox.showwarning('未導入','先にMABエンジンを導入してください。')
            return
        doc = example_library()
        try:
            existing = self.history.head()
            expected_head_id = existing['revisionId'] if existing else None
        except (StudioError, OSError, ValueError) as exc:
            messagebox.showerror('建築履歴の保護', '現在の保存履歴を確認できないためサンプルを開けません: '+str(exc))
            return
        if not self._confirm_replace_failed_generation():
            return
        def done(scene):
            # 'サンプルを見る' is preview, not consent to save a new project.
            # Reuse the exact B143-verified review/approve flow used by AI
            # generation; never mutate approved history merely by looking.
            self._hold_generated_candidate(doc, scene, 'OFFLINE_SAMPLE', '', expected_head_id)
            self.caption.set('AIなしのサンプル | B143 SOURCE合格 | 未承認・未保存。'
                             '3Dで確認後、必要な場合だけ承認・保存してください。')
        self._start('本物のMABでサンプル設計をSOURCE検証中（未承認プレビュー）',
                    lambda: run_bridge(engine, doc), done)

    def _sync_preview_outputs(self, design, scene, provider='SAVED_SOURCE', model=''):
        """Keep legacy preview exports aligned with the ACTIVE approved view.

        Never exports a draft, a Minecraft operation or an unverified world state.
        These preview files are not a construction journal/authorization.
        """
        out_dir = self.manager.root/'outputs'
        atomic_json(out_dir/'latest-design.json', design)
        atomic_json(out_dir/'latest-scene.json', scene)
        atomic_json(out_dir/'latest-receipt.json', {
            'appVersion':'0.3.0','createdAt':datetime.now(timezone.utc).isoformat(),
            'engineCommit':self.manager.active_sha,'sourceSha256':scene['sourceSha256'],
            'geometrySha256':scene['geometrySha256'],'provider':provider,'model':model,
            'readiness':'NOT_PREPARED','worldWrites':0})

    def _display_design(self, design, scene, provider, model, qualifier, *,
                        operation='new', expected_head_id=...):
        if operation == 'revision':
            previous = self.current_snapshot
            if previous is None:
                raise StudioError('修正元の設計履歴がありません')
            delta = compare_scenes(previous['scene'], scene)
            snapshot = self.history.commit(design, scene, engine_commit=self.manager.active_sha,
                                           operation='revision', previous_id=previous['revisionId'])
        else:
            delta = None
            commit_options = ({'expected_head_id': expected_head_id}
                              if expected_head_id is not ... else {})
            snapshot = self.history.commit(design, scene, engine_commit=self.manager.active_sha,
                                           operation='new', **commit_options)
        self.current_snapshot = snapshot
        self.failed_generation = None
        self.delta = delta
        self.scene = scene
        self.design = design
        self._sync_preview_outputs(design, scene, provider, model)
        self._refresh_view()
        self._refresh_buttons()
        self.caption.set(f'{scene.get("title", "建築")} | {scene["stats"]["voxels"]:,}セル'
                         f' | SOURCE検証成功 | {qualifier} | 施工禁止')
        self.export_btn.configure(state='normal')
        if self.delta is not None:
            self.caption.set(self.caption.get() + ' | 追加' + str(self.delta['counts']['added']) +
                             ' / 削除' + str(self.delta['counts']['removed']) +
                             ' / 交換' + str(self.delta['counts']['replaced']) + '（施工前）')

    def revise_design(self):
        if self.pending_revision is not None:
            if self.pending_revision.get('kind') == 'new':
                self._revise_unapproved_generation()
            return
        if not self.scene or not self.design or self.current_snapshot is None:
            messagebox.showwarning('修正元なし','まず建築設計を作成してください。')
            return
        instruction = self.prompt.get('1.0','end').strip()
        model = self.model.get().strip()
        provider = self.llm_provider.get()
        engine = self.manager.active_path
        if not instruction or not model or engine is None:
            messagebox.showwarning('入力不足','修正内容を入力し、ローカルAIを選択してください。')
            return
        self._remember_model_choice()
        # Snapshot Tk state on the UI thread, never read Tk variables from
        # the inference worker (which can otherwise fail before the POST).
        revision_mode = 'delta' if self.fast_revision_mode.get() else 'full'
        cancel_event = threading.Event()
        original = self.design
        pinned_hash = self.scene['geometrySha256']
        def work():
            # Read-only loaded-model gate, followed by exactly ONE inference.
            model_status = verify_loaded_model(provider, model)
            inference_model = model_status['inferenceModel']
            check = run_bridge(engine, original)
            if check['geometrySha256'] != pinned_hash:
                raise StudioError('現在のエンジンは元の設計を再現できません。変更を中止しました')
            ai_started = time.monotonic()
            draft = ask_llm(provider, inference_model, instruction, revision_base=original,
                            revision_mode=revision_mode,
                            on_progress=lambda message: self.pending_events.put(
                                ('progress', 'LOCAL_LLM', message, None, True, cancel_event)),
                            cancel_event=cancel_event)
            ai_seconds = time.monotonic() - ai_started
            if cancel_event.is_set():
                raise ModelCancelled('修正生成をキャンセルしました。回答を破棄しました。')
            self.pending_events.put(('progress', 'LOCAL_LLM',
                f'AIの応答完了（{ai_seconds:.1f}秒）。B143のSOURCEを検証中…',
                None, True, cancel_event))
            try:
                scene = run_bridge(engine, draft)
            except StudioError as exc:
                raise StudioError(f'{exc}\n（AI応答{ai_seconds:.1f}秒後のSOURCE検証で不合格。'
                                  '自動で再推論しません）') from exc
            return draft, scene
        def done(data):
            revised, scene = data
            # The model response is only a DRAFT. No commit before approval.
            if self.current_snapshot is None or self.scene is None:
                raise StudioError('修正元が消えたため修正案を破棄しました')
            delta = compare_scenes(self.scene, scene)
            self.pending_revision = {
                'design': revised, 'scene': scene, 'delta': delta,
                'parentId': self.current_snapshot['revisionId'],
                'parentGeometrySha256': self.scene['geometrySha256'],
                'engineSha': self.manager.active_sha,
                'provider': provider, 'model': model,
            }
            # Review the *finished candidate building* first. A colored delta
            # alone does not show usable rooms, shape or architectural quality.
            # Users can still choose '変更差分' from the existing view selector.
            self.view_mode.set('修正案（未承認）')
            self._refresh_view()
            counts = delta['counts']
            changes = describe_design_changes(original, revised)
            # Architectural fitness is still a human 3D review decision.
            # Do not confuse changed design declarations with valid entrances,
            # stairs, or attractive geometry (B143 only verifies SOURCE).
            geometry_note = ('3Dセルの変化なし' if not any(counts[k] for k in
                             ('added', 'removed', 'replaced')) else
                             f'セル追加{counts["added"]} / 削除{counts["removed"]} / 交換{counts["replaced"]}')
            self.caption.set(f'修正内容：{changes} | {geometry_note} | '
                             '3Dと動線を確認して承認・却下（未施工）')
            self.status.set('修正案の検証に成功しました。承認前の設計履歴は未変更です。')
        self._start('AI修正案の検証＋差分計算（ワールドには未反映）',work,done,
                    cancel_event=cancel_event)

    def _revise_unapproved_generation(self):
        """Allow improving a *new* B143-pass draft before first approval.

        It uses the existing AI revision schema/engine; does not create a
        second geometry engine or a premature saved revision.
        """
        candidate = self.pending_revision
        if self.busy or candidate is None or candidate.get('kind') != 'new':
            return
        engine = self.manager.active_path
        instruction = self.prompt.get('1.0', 'end').strip()
        provider, model = self.llm_provider.get(), self.model.get().strip()
        if not instruction or not model or engine is None:
            messagebox.showwarning('入力不足', '生成案への修正内容とローカルAIモデルを選択してください。')
            return
        if candidate['engineSha'] != self.manager.active_sha:
            messagebox.showerror('生成案の保護', 'B143エンジンが変更されました。生成案は修正できません。')
            return
        try:
            head = self.history.head()
            if (head['revisionId'] if head else None) != candidate['expectedHead']:
                raise StudioError('保存履歴が別の操作で変更されました。生成案を修正できません')
        except (StudioError, OSError, ValueError) as exc:
            messagebox.showerror('生成案の保護', str(exc))
            return
        revision_mode = 'delta' if self.fast_revision_mode.get() else 'full'
        original, original_scene = candidate['design'], candidate['scene']
        cancel_event = threading.Event()
        def work():
            status = verify_loaded_model(provider, model)
            # Revalidate the draft that is actually visible in 3D, not a
            # separately loaded approved project or the stale latest output.
            checked = run_bridge(engine, original)
            if (checked['geometrySha256'] != original_scene['geometrySha256']
                    or checked['sourceSha256'] != original_scene['sourceSha256']):
                raise StudioError('未承認の生成案を同じB143で再現できません')
            revised = ask_llm(
                provider, status['inferenceModel'], instruction,
                revision_base=original, revision_mode=revision_mode,
                on_progress=lambda msg: self.pending_events.put(
                    ('progress', 'LOCAL_LLM', msg, None, True, cancel_event)),
                cancel_event=cancel_event)
            if cancel_event.is_set():
                raise ModelCancelled('生成案の修正をキャンセルしました。回答は破棄しました。')
            scene = run_bridge(engine, revised)
            return revised, scene
        def done(res):
            revised, scene = res
            if self.pending_revision is not candidate:
                raise StudioError('生成案が切り替わりました。古いAI応答は破棄しました')
            candidate['design'] = revised
            candidate['scene'] = scene
            candidate['provider'] = provider
            candidate['model'] = model
            self.view_mode.set('修正案（未承認）')
            self._refresh_view(preserve_camera=True)
            self.caption.set('未承認の生成案を修正：' + describe_design_changes(original, revised)
                             + ' | 3D・入口・動線を確認して承認・破棄（保存前）')
            self.status.set('生成案の修正がB143に合格しました。承認済み履歴はまだ変更されていません。')
        self._start('未承認の生成案を1回修正しB143で検証中（自動再試行なし）',
                    work, done, cancel_event=cancel_event)

    def approve_revision(self):
        candidate = self.pending_revision
        if not candidate or self.busy:
            return
        # The user may be looking at the original building, an abstract
        # difference image, or a terrain overlay. One click switches to the
        # actual candidate first; only a subsequent deliberate click commits.
        # Normal review already starts on this view, so it needs no extra step.
        if self.view_mode.get() != '修正案（未承認）':
            self.view_mode.set('修正案（未承認）')
            self._refresh_view(preserve_camera=True)
            self.status.set('承認する建築案を3Dに表示しました。確認してからもう一度「承認」を押してください。')
            return
        engine = self.manager.active_path
        if candidate.get('kind') == 'new':
            if engine is None or self.manager.active_sha != candidate['engineSha']:
                messagebox.showerror('生成案の承認を保留', '使用中のB143エンジンが変わりました。')
                return
            def work():
                # Final B143 verification precedes the first history commit.
                scene = run_bridge(engine, candidate['design'])
                if (scene['sourceSha256'] != candidate['scene']['sourceSha256']
                        or scene['geometrySha256'] != candidate['scene']['geometrySha256']):
                    raise StudioError('生成案を承認前に再検証できませんでした')
                return scene
            def done(scene):
                # Existing RevisionStore enforces the expected-head lease even
                # if another process changed the active project mid-request.
                self._display_design(candidate['design'], scene, candidate['provider'],
                                     candidate['model'], '利用者が3Dを確認して承認 / 施工なし',
                                     expected_head_id=candidate['expectedHead'])
                self.pending_revision = None
                self.view_mode.set('建築のみ')
                self._refresh_view()
                self._refresh_buttons()
                self.status.set('新しい建築を承認・保存しました。Minecraftワールドは未変更です。')
            self._start('新規生成案を承認前にSOURCE再検証（ワールド未接続）', work, done)
            return
        if (engine is None or self.manager.active_sha != candidate['engineSha']
                or self.current_snapshot is None or self.scene is None
                or self.current_snapshot['revisionId'] != candidate['parentId']
                or self.scene['geometrySha256'] != candidate['parentGeometrySha256']):
            messagebox.showerror('修正案の承認を保留', '元の設計またはMABエンジンが変更されました。')
            return
        def work():
            # Recompile both approved and draft source before accepting.
            before = run_bridge(engine, self.design)
            after = run_bridge(engine, candidate['design'])
            if before['geometrySha256'] != candidate['parentGeometrySha256']:
                raise StudioError('元の設計の再検証に失敗しました')
            if (after['geometrySha256'] != candidate['scene']['geometrySha256']
                    or after['sourceSha256'] != candidate['scene']['sourceSha256']):
                raise StudioError('修正案の再検証でSOURCEが一致しません')
            return after
        def done(scene):
            # Only here does the existing append-only RevisionStore commit.
            self._display_design(candidate['design'], scene, candidate['provider'],
                                 candidate['model'], '承認されたAI修正 / 施工なし',
                                 operation='revision')
            self.pending_revision = None
            self._refresh_buttons()
            # After approval show the actual saved building, not abstract
            # comparison colors. '変更差分' remains an optional view.
            self.view_mode.set('建築のみ')
            self._refresh_view()
            self.status.set('承認済み：設計履歴だけ更新しました。Minecraftワールドは未変更です。')
        self._start('承認前のSOURCE真正再検証（ワールド書き込みなし）',work,done)

    def reject_revision(self):
        if self.pending_revision is None or self.busy:
            return
        was_new = self.pending_revision.get('kind') == 'new'
        self.pending_revision = None
        self.view_mode.set('建築のみ')
        self._refresh_view()
        self._update_engine_label()
        self.caption.set('AI生成案を破棄しました。保存済み建築と履歴は維持しています。' if was_new else
                         'AI修正案を却下しました。承認済みの元設計と履歴を維持しています。')
        self.status.set('未承認案を破棄しました。Minecraftワールドは未変更です。')

    def restore_previous(self):
        engine=self.manager.active_path
        if engine is None:
            return
        def done(data):
            self.failed_generation = None
            self.current_snapshot=data
            self.design=data['design']
            self.scene=data['scene']
            self._sync_preview_outputs(self.design, self.scene)
            self.delta=None
            self.view_mode.set('建築のみ')
            self._refresh_view()
            self._refresh_buttons()
            self.caption.set('過去の設計を新しい履歴として復元しました（ワールドは変更されていません）')
        self._start('真正MABで旧設計を再検証してプレビューだけ戻す',
                    lambda:self.history.restore_previous(engine_commit=self.manager.active_sha,
                          verify=lambda design:run_bridge(engine,design)),done)

    def load_saved_design(self):
        if self.pending_revision is not None:
            return
        engine=self.manager.active_path
        if engine is None:
            messagebox.showwarning('エンジン未導入','先にB143を導入してください。')
            return
        def work():
            old=self.history.head()
            if old is None:
                raise StudioError('保存された設計履歴はありません')
            scene=run_bridge(engine, old['design'])
            if scene['geometrySha256'] != old['geometrySha256']:
                raise StudioError('保存された設計とMAB出力が一致しません')
            return old,scene
        def done(data):
            old,scene=data
            self.failed_generation = None
            self.current_snapshot=old
            self.design=old['design']
            self.scene=scene
            self._sync_preview_outputs(self.design, self.scene)
            self.delta=None
            self._refresh_view()
            self._refresh_buttons()
            self.export_btn.configure(state='normal')
            self.caption.set('保存された設計を真正MABで再検証しました。Minecraftの施工記録ではありません')
        self._start('保存した履歴を真正MABで再検証中',work,done)

    def browse_saved_designs(self):
        """Pick any verified previous SOURCE snapshot, including older projects."""
        if self.pending_revision is not None or self.busy:
            return
        def done(items):
            if not items:
                messagebox.showinfo('保存済み設計', '安全に読み出せる保存済み設計はありません。')
                return
            dialog=tk.Toplevel(self)
            dialog.title('過去の建築一覧 — SOURCE履歴のみ')
            dialog.geometry('760x420')
            dialog.transient(self)
            dialog.grab_set()
            ttk.Label(dialog, text='保存済みの設計を選んで開く（最大100件）。Minecraftワールドは変更しません。',
                      wraplength=720).pack(padx=14,pady=(12,8),anchor='w')
            frame=ttk.Frame(dialog,padding=(12,0,12,8))
            frame.pack(fill='both',expand=True)
            frame.columnconfigure(0,weight=1)
            frame.rowconfigure(0,weight=1)
            table=ttk.Treeview(frame,columns=('title','operation','revision'),show='headings',selectmode='browse')
            for name,width,label in [('title',390,'建築名'),('operation',100,'履歴種類'),('revision',190,'履歴ID')]:
                table.heading(name,text=label)
                table.column(name,width=width)
            bar=ttk.Scrollbar(frame,orient='vertical',command=table.yview)
            table.configure(yscrollcommand=bar.set)
            table.grid(row=0,column=0,sticky='nsew')
            bar.grid(row=0,column=1,sticky='ns')
            ids=set()
            for item in items:
                rid=item['revisionId']
                ids.add(rid)
                title=('● ' if item['isCurrent'] else '') + item['title']
                table.insert('', 'end',iid=rid,values=(title[:90],item['operation'],rid[:16]+'…'))
            if items:table.selection_set(items[0]['revisionId'])
            def select(*_):
                selection=table.selection()
                if not selection or selection[0] not in ids:return
                rid=selection[0]
                dialog.grab_release()
                dialog.destroy()
                self._open_saved_revision(rid)
            buttons=ttk.Frame(dialog,padding=(12,0,12,12))
            buttons.pack(fill='x')
            ttk.Button(buttons,text='選択した設計を開く',command=select).pack(side='left')
            ttk.Button(buttons,text='キャンセル',command=dialog.destroy).pack(side='right')
            table.bind('<Double-1>',select)
        self._start('過去の建築履歴を読み込み中（ワールド操作なし）',
                    lambda:self.history.list_saved(),done)

    def _open_saved_revision(self, revision_id: str):
        if self.busy or self.pending_revision is not None:return
        engine=self.manager.active_path
        if engine is None:
            messagebox.showwarning('MAB未導入','先に確認済みエンジンを導入してください。')
            return
        previous=self.history.head()
        expected=previous['revisionId'] if previous is not None else None
        def done(snapshot):
            self.failed_generation = None
            self.current_snapshot=snapshot
            self.design=snapshot['design']
            self.scene=snapshot['scene']
            self._sync_preview_outputs(self.design, self.scene)
            self.delta=None
            self.terrain=None
            self.terrain_report=None
            self.pending_revision=None
            self.view_mode.set('建築のみ')
            self._refresh_view()
            self._refresh_buttons()
            self.export_btn.configure(state='normal')
            self.caption.set('過去の設計「'+self.design['title']+'」を真正MABで照合して開きました（ワールド未変更）')
        self._start('保存済みSOURCEを真正MABで検証して開く',
                    lambda:self.history.open_saved(
                        revision_id,verify=lambda d:run_bridge(engine,d),expected_head_id=expected),done)

    def export_design(self):
        """Export the currently visible APPROVED model, never a stale latest file."""
        if self.scene is None or self.design is None or self.busy:
            return
        if self.pending_revision is not None:
            messagebox.showwarning('修正案は未承認', '承認済みの設計だけを保存できます。')
            return
        engine = self.manager.active_path
        if engine is None:
            messagebox.showwarning('MAB未導入','確認済みB143エンジンが必要です。')
            return
        name = ''.join(c for c in self.design.get('id','building') if c.isascii() and
                       (c.isalnum() or c in '-_'))[:45] or 'building'
        filename = filedialog.asksaveasfilename(
            title='承認済みSOURCE JSONを書き出す（アプリ内履歴とは別）',
            defaultextension='.json', initialfile=name+'-source.json',
            filetypes=[('SOURCE JSON', '*.json')])
        if not filename:
            return
        # Copy bound source/geometry on GUI thread, verify with real MAB worker.
        design = json.loads(json.dumps(self.design, ensure_ascii=False))
        source_sha = self.scene['sourceSha256']
        geometry_sha = self.scene['geometrySha256']
        def work():
            return export_verified_source(filename, design, source_sha,
                                          geometry_sha, lambda d:run_bridge(engine,d))
        def done(path):
            self.status.set('画面の承認済みSOURCEを検証して保存しました（Minecraft未変更）: '+str(path))
            messagebox.showinfo('SOURCE JSON保存完了',
                '保存先: '+str(path)+'\n\nこれはSOURCE JSONの書き出しです。アプリ内の建築履歴とは別のファイルです。'
                '\n承認した修正案は建築履歴に自動保存されます。Minecraft施工許可やワールドの復元にはなりません。')
        self._start('表示中の承認済み設計をSOURCE再検証してJSON保存',work,done)


def main():
    if '--saved-native-selfcheck' in sys.argv:
        # Explicit read-only, saved SOURCE native VTK proof. Never switch head.
        from app.saved_native_selfcheck import main as saved_native_main
        return saved_native_main([v for v in sys.argv[1:] if v != '--saved-native-selfcheck'])
    if '--native-selfcheck' in sys.argv:
        # Exercise native VTK from INSIDE the actual frozen executable.
        # Synthetic read-only geometry only; never invokes the LLM or Minecraft.
        from app.native_selfcheck import main as native_main
        return native_main([v for v in sys.argv[1:] if v != '--native-selfcheck'])
    if '--acceptance' in sys.argv:
        # Explicit model-inference acceptance. Reports evidence to the chosen
        # path, but does not save a user project or touch Minecraft.
        from app.acceptance import run_acceptance
        return run_acceptance(sys.argv[1:])
    if '--smoke' in sys.argv:
        # Explicit release-test command.  No Minecraft connection or world
        # writes; GUI and optional real pinned B143 are checked in isolation.
        from app.smoke import run_smoke
        return run_smoke(Studio, sys.argv[1:])
    # No silent traceback in a --windowed packaged application.  A missing
    # private Node or bridge must be visible instead of depending on PATH.
    try:
        from app.release_runtime import verify_packaged_runtime
        verify_packaged_runtime()
        Studio().mainloop()
    except (StudioError, OSError, ValueError) as exc:
        try:
            from tkinter import messagebox as startup_messagebox
            startup_messagebox.showerror('MAB Studioを起動できません', str(exc)[:500])
        except (tk.TclError, RuntimeError):
            # PyInstaller --windowed may set sys.stderr to None; do not raise
            # a second exception while handling a startup failure.
            if sys.stderr is not None:
                print('MAB Studio startup failed: ' + str(exc)[:500], file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
