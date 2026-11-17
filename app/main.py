"""MAB Studio Windows GUI. Offline-first, test-world only. No world-writing calls."""
from __future__ import annotations
import json
import gc
from pathlib import Path
from datetime import datetime, timezone
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.core import (
    BASELINE_SHA, DEFAULT_BRANCH, EngineManager, StudioError, ModelTransportError,
    ask_llm, branch_sha, compatibility_check, llm_models, run_bridge, atomic_json
)
from app.preview import VoxelPreview
from app.project_io import export_verified_source
from app.diagnostics import check_local_setup, readiness_text
from app.education import import_b134_readback, parse_probe_coordinates, run_education_readonly
from app.examples import example_library
from app.revisions import RevisionStore, compare_scenes, diff_preview_scene
from app.terrain import inspect_mcworld, read_mcworld_terrain, create_overlay_scene


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('MAB Studio — Minecraft Education 建築AI（安全プレビュー）')
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
        self.pending_revision = None  # in-memory DRAFT; NEVER a committed history state
        self._education_running = False
        self._education_cancel = threading.Event()
        self._closed = False
        self.terrain = None
        self.terrain_archive = None
        self.terrain_report = None
        self.view_mode = tk.StringVar(value='建築のみ')
        self.detail_mode = tk.StringVar(value='標準（2,300）')
        self.place_xyz = tk.StringVar(value='0 64 0')
        self.llm_provider = tk.StringVar(value='Ollama')
        self.model = tk.StringVar(value='')
        self.branch = tk.StringVar(value=self.manager.state.get('tracked_branch', DEFAULT_BRANCH))
        self._make_ui()
        self.place_xyz.trace_add('write', self._on_anchor_changed)
        self._poll_after = self.after(120, self._poll)
        self._startup_after = self.after(2000, self._startup)
        # Reviewed engine updates are not automatically installable. Do not
        # interrupt users with periodic remote checks; manual check stays available.
        self._check_after = None

    def destroy(self):
        # Tk.after callbacks must not run after GUI disposal; shutdown an active
        # read-only listener without editing any world state.
        if self._closed:
            return
        self._closed = True
        self._education_cancel.set()
        for attr in ('_poll_after','_startup_after','_check_after'):
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

    def _make_ui(self):
        style = ttk.Style(self)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('Title.TLabel', font=('Yu Gothic UI',17,'bold'), foreground='#15304d')
        style.configure('Header.TLabel', font=('Yu Gothic UI',10,'bold'))
        style.configure('TButton', font=('Yu Gothic UI',10), padding=(9,7))
        style.configure('TLabel', font=('Yu Gothic UI',10))
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer,text='MAB Studio',style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer,text='自然な日本語 → ローカルAI → MAB SOURCE検証 → 3Dプレビュー  |  ワールド操作なし').pack(anchor='w',pady=(0,10))
        body = ttk.Frame(outer)
        body.pack(fill='both',expand=True)
        body.columnconfigure(0,minsize=385,weight=0)
        body.columnconfigure(1,weight=1)
        body.rowconfigure(0,weight=1)
        left = ttk.Frame(body,padding=(0,5,12,0),width=390)
        right = ttk.Frame(body,padding=(8,5,0,0))
        left.grid(row=0,column=0,sticky='nsew')
        left.grid_propagate(False)
        right.grid(row=0,column=1,sticky='nsew')
        left.columnconfigure(0,weight=1)
        self.engine_state=tk.StringVar(value='エンジンを確認中')
        ttk.Label(left,text='1  MABエンジン',style='Header.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Label(left,textvariable=self.engine_state,wraplength=330,foreground='#2a516e').grid(row=1,column=0,sticky='w',pady=(3,5))
        self.install_btn=ttk.Button(left,text='MABを初期導入（確認済みB143）',command=self.install_baseline)
        self.install_btn.grid(row=2,column=0,sticky='ew')
        branchrow=ttk.Frame(left)
        branchrow.grid(row=3,column=0,sticky='ew',pady=(9,4))
        branchrow.columnconfigure(0,weight=1)
        ttk.Entry(branchrow,textvariable=self.branch).grid(row=0,column=0,sticky='ew',padx=(0,4))
        ttk.Button(branchrow,text='追跡保存',command=self.save_branch).grid(row=0,column=1)
        updates=ttk.Frame(left)
        updates.grid(row=4,column=0,sticky='ew',pady=(0,5))
        updates.columnconfigure(0,weight=1)
        updates.columnconfigure(1,weight=1)
        self.check_btn=ttk.Button(updates,text='GitHub更新を確認',command=self.check_update)
        self.check_btn.grid(row=0,column=0,sticky='ew',padx=(0,4))
        self.apply_btn=ttk.Button(updates,text='検証して更新',command=self.install_pending,state='disabled')
        self.apply_btn.grid(row=0,column=1,sticky='ew')
        self.rollback_btn=ttk.Button(left,text='前のエンジンに戻す',command=self.rollback)
        self.rollback_btn.grid(row=5,column=0,sticky='ew')
        ttk.Separator(left).grid(row=6,column=0,sticky='ew',pady=13)
        ttk.Label(left,text='2  ローカルAI',style='Header.TLabel').grid(row=7,column=0,sticky='w')
        provider=ttk.Combobox(left,textvariable=self.llm_provider,values=('Ollama','LM Studio'),state='readonly')
        provider.grid(row=8,column=0,sticky='ew',pady=(5,4))
        provider.bind('<<ComboboxSelected>>',lambda _: self.refresh_models())
        mrow=ttk.Frame(left)
        mrow.grid(row=9,column=0,sticky='ew',pady=(0,7))
        mrow.columnconfigure(0,weight=1)
        self.model_box=ttk.Combobox(mrow,textvariable=self.model)
        self.model_box.grid(row=0,column=0,sticky='ew',padx=(0,4))
        self.models_btn=ttk.Button(mrow,text='モデル取得',command=self.refresh_models)
        self.models_btn.grid(row=0,column=1,padx=(0,4))
        ttk.Button(mrow,text='接続診断',command=self.diagnose).grid(row=0,column=2)
        ttk.Label(left,text='3  どんな建築を作りたい？',style='Header.TLabel').grid(row=10,column=0,sticky='w',pady=(6,5))
        self.prompt = tk.Text(left,height=7,wrap='word',font=('Yu Gothic UI',10),relief='solid',borderwidth=1)
        self.prompt.grid(row=11,column=0,sticky='nsew')
        self.prompt.insert('1.0','未来的な2階建ての小さな図書館。ガラス張りの正面、\n広い入口、屋上庭園を作って。')
        left.rowconfigure(11,weight=1)
        self.generate_btn=ttk.Button(left,text='AIで設計して3Dプレビュー',command=self.generate)
        self.generate_btn.grid(row=12,column=0,sticky='ew',pady=(10,4))
        self.revise_btn=ttk.Button(left,text='現在の設計をAIで部分修正',command=self.revise_design,state='disabled')
        self.revise_btn.grid(row=13,column=0,sticky='ew',pady=(2,4))
        self.sample_btn=ttk.Button(left,text='サンプル建築で動作確認（AI不要）',command=self.show_sample)
        self.sample_btn.grid(row=14,column=0,sticky='ew',pady=(2,4))
        self.export_btn=ttk.Button(left,text='検証済み設計をJSON保存',command=self.export_design,state='disabled')
        self.export_btn.grid(row=15,column=0,sticky='ew',pady=(2,4))
        self.edu_btn=ttk.Button(left,text='Educationへインポート（実機未検証）',state='disabled')
        self.edu_btn.grid(row=16,column=0,sticky='ew',pady=(2,5))
        ttk.Label(left,text='注意：B143は設計用。教育版への施工・ファイル書込は未接続。\n専用テストワールド以外では使わないでください。',
                  wraplength=320,foreground='#a0522d').grid(row=17,column=0,sticky='w',pady=(2,3))
        ttk.Label(right,text='SOURCE建築モデル / 設計差分 / ワールド地形（読み取り専用）',style='Header.TLabel').pack(anchor='w',pady=(0,6))
        tools = ttk.Frame(right)
        tools.pack(fill='x', pady=(0, 7))
        self.view_box = ttk.Combobox(tools,textvariable=self.view_mode, state='readonly', width=17,
                                     values=('建築のみ','修正案（未承認）','変更差分','地形＋建築'))
        self.view_box.pack(side='left',padx=(0,6))
        detail_box = ttk.Combobox(tools, textvariable=self.detail_mode, state='readonly',
                                  width=16, values=tuple(VoxelPreview.DETAIL_LIMITS))
        detail_box.pack(side='left',padx=(0,6))
        detail_box.bind('<<ComboboxSelected>>', lambda _: self.preview.set_detail(self.detail_mode.get()))
        self.view_box.bind('<<ComboboxSelected>>', lambda _: self._refresh_view())
        self.restore_btn=ttk.Button(tools,text='前の設計に戻す',command=self.restore_previous,state='disabled')
        self.restore_btn.pack(side='left',padx=(0,6))
        self.load_btn=ttk.Button(tools,text='前回の設計を開く',command=self.load_saved_design)
        self.load_btn.pack(side='left')
        self.browse_btn=ttk.Button(tools,text='過去の建築一覧',command=self.browse_saved_designs)
        self.browse_btn.pack(side='left',padx=(6,0))
        reviewbar = ttk.Frame(right)
        reviewbar.pack(fill='x', pady=(0,7))
        self.approve_draft_btn=ttk.Button(reviewbar,text='AI修正案を承認（設計のみ）',
                                           command=self.approve_revision,state='disabled')
        self.approve_draft_btn.pack(side='left',padx=(0,6))
        self.reject_draft_btn=ttk.Button(reviewbar,text='修正案を却下',
                                          command=self.reject_revision,state='disabled')
        self.reject_draft_btn.pack(side='left')
        ttk.Label(reviewbar,text='承認してもMinecraftには施工しません',
                  foreground='#a0522d').pack(side='left',padx=(10,0))
        terrainbar = ttk.Frame(right)
        terrainbar.pack(fill='x', pady=(0,7))
        ttk.Label(terrainbar,text='予定位置 XYZ').pack(side='left')
        self.anchor_entry = ttk.Entry(terrainbar,textvariable=self.place_xyz,width=18)
        self.anchor_entry.pack(side='left',padx=(6,6))
        self.anchor_entry.bind('<FocusOut>', lambda _event: self._refresh_view() if self.view_mode.get()=='地形＋建築' else None)
        self.anchor_entry.bind('<Return>', lambda _event: self._refresh_view() if self.view_mode.get()=='地形＋建築' else None)
        self.import_terrain_btn=ttk.Button(terrainbar,text='.mcworldを読み取る',command=self.import_terrain)
        self.import_terrain_btn.pack(side='left')
        ttk.Button(terrainbar,text='B134読取結果',command=self.import_education_diagnostic).pack(side='left',padx=(8,0))
        educationbar = ttk.Frame(right)
        educationbar.pack(fill='x', pady=(0,7))
        self.edu_live_btn=ttk.Button(educationbar,text='Educationから直接読取（5照合）',
                                     command=self.start_education_readonly)
        self.edu_live_btn.pack(side='left',padx=(0,6))
        self.edu_stop_btn=ttk.Button(educationbar,text='読取待機を停止',
                                     command=self.stop_education_readonly,state='disabled')
        self.edu_stop_btn.pack(side='left')
        ttk.Label(educationbar,text='1か所のオーク階段・testforblock限定・施工しません',
                  foreground='#a0522d').pack(side='left',padx=(9,0))
        self.preview=VoxelPreview(right)
        self.preview.pack(fill='both',expand=True)
        self.caption=tk.StringVar(value='表示待ち：AIモデル・エンジンが準備できたら生成できます。')
        ttk.Label(right,textvariable=self.caption,wraplength=670).pack(anchor='w',pady=(7,0))
        self.status=tk.StringVar(value='準備中')
        foot=ttk.Label(outer,textvariable=self.status,anchor='w',wraplength=950,foreground='#34566e')
        foot.pack(fill='x',pady=(9,0))

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
        self._refresh_buttons()

    def _start(self, title, fn, callback=None, quiet=False):
        if self.busy:
            return
        # Prevent finalizers of previously closed Tk variables from executing
        # in this worker; Tcl is main-thread-affine.
        gc.collect()
        self.busy=True
        self.status.set(title)
        self._update_engine_label()
        def work():
            try:
                result=fn()
                self.pending_events.put(('ok',title,result,callback,quiet))
            except Exception as e:
                self.pending_events.put(('error',title,str(e),callback,quiet))
        threading.Thread(target=work,daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind,title,result,callback,quiet=self.pending_events.get_nowait()
                self.busy=False
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
        self.scene = None
        self.preview.show(None)
        self.export_btn.configure(state='disabled')
        self.delta = None
        self.pending_revision = None
        self.design = None
        self.current_snapshot = None
        self._refresh_buttons()

    def _refresh_buttons(self):
        ready = not self.busy and self.pending_revision is None
        self.revise_btn.configure(state='normal' if self.design and self.manager.active_path and ready else 'disabled')
        self.restore_btn.configure(state='normal' if self.current_snapshot and self.current_snapshot.get('parentId') and ready else 'disabled')
        self.load_btn.configure(state='normal' if ready else 'disabled')
        self.browse_btn.configure(state='normal' if ready else 'disabled')
        self.approve_draft_btn.configure(state='normal' if not self.busy and self.pending_revision else 'disabled')
        self.reject_draft_btn.configure(state='normal' if not self.busy and self.pending_revision else 'disabled')
        self.edu_live_btn.configure(state='normal' if not self.busy else 'disabled')
        self.edu_stop_btn.configure(state='normal' if self._education_running else 'disabled')

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

    def _refresh_view(self):
        if not self.scene:
            self.preview.show(None)
            return
        mode = self.view_mode.get()
        candidate = self.pending_revision
        active_scene = candidate['scene'] if candidate else self.scene
        active_delta = candidate['delta'] if candidate else self.delta
        if mode == '修正案（未承認）':
            self.preview.show(active_scene)
            if candidate is None:
                self.status.set('未承認の修正案はありません。承認済み設計を表示しています。')
        elif mode == '変更差分':
            if active_delta is None or not any(active_delta['counts'][p] for p in ('added','removed','replaced')):
                self.preview.show(self.scene)
                self.status.set('変更差分がありません。通常モデルを表示します。')
            else:
                self.preview.show(diff_preview_scene(active_delta, active_scene))
        elif mode == '地形＋建築':
            if self.terrain is None:
                self.preview.show(self.scene)
                self.status.set('地形ブロックは未読取です。.mcworldの読み込みを先に行ってください')
            else:
                try:
                    merged, self.terrain_report = create_overlay_scene(self.terrain, active_scene, self._coords())
                    self.preview.show(merged)
                    r=self.terrain_report
                    self.status.set('地形照合（未施工）：既存ブロック重複候補 '+str(r['potentialTerrainIntersections'])+
                                    ' / 確認済み空気 '+str(r['projectedKnownAir'])+
                                    ' / 読取不能 '+str(r['projectedUnreadable'])+
                                    ' / 読取範囲外 '+str(r['projectedOutsideSample'])+
                                    ' / 位置を確認できないセル '+str(r['projectedLegacyUnverified']))
                except StudioError as exc:
                    self.preview.show(self.scene)
                    self.status.set(str(exc))
        else:
            self.preview.show(self.scene)

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

    def refresh_models(self):
        provider=self.llm_provider.get()
        def done(names):
            self.model_box['values']=names
            if names and self.model.get() not in names:
                self.model.set(names[0])
            self.status.set(f'{provider}: {len(names)}件のモデルを取得しました。')
        self._start(provider+'のモデル一覧を取得中',lambda:llm_models(provider),done,quiet=True)

    def diagnose(self):
        provider = self.llm_provider.get()
        def done(report):
            if self.llm_provider.get() == provider and report['models']:
                self.model_box['values'] = report['models']
                if self.model.get() not in report['models']:
                    self.model.set(report['models'][0])
            messagebox.showinfo('MAB Studio 接続診断（読み取りのみ）', readiness_text(report))
            self.status.set('接続診断完了：MAB / Node / ローカルAIのみ。Minecraft未接続。')
        self._start('MAB SOURCEとローカルAIの読み取り診断中',
                    lambda: check_local_setup(self.manager,provider),done)

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
        def work():
            error = ''
            design = None
            # No world writes. Exactly one bounded correction attempt.
            for attempt in range(2):
                previous = design
                try:
                    design = ask_llm(provider, model, prompt, error, previous_design=previous)
                    scene = run_bridge(engine, design)
                    return design, scene, attempt
                except ModelTransportError:
                    raise  # avoid a second long inference on server failure
                except StudioError as e:
                    error = str(e)
            raise StudioError('AIの設計を2回検証しましたが、MAB SOURCEが拒否しました: ' + error)
        def done(res):
            design, scene, retry = res
            self._display_design(design, scene, provider, model,
                                 '自動修正1回' if retry else '初回成功')
        self._start('ローカルAIで設計し、MAB SOURCEで検証中',work,done)

    def show_sample(self):
        if self.pending_revision is not None:
            return
        engine = self.manager.active_path
        if not engine:
            messagebox.showwarning('未導入','先にMABエンジンを導入してください。')
            return
        doc = example_library()
        def done(scene):
            self._display_design(doc, scene, 'OFFLINE_SAMPLE', '', 'サンプル / AI未使用')
        self._start('本物のMABでサンプル設計をSOURCE検証中',
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

    def _display_design(self, design, scene, provider, model, qualifier, *, operation='new'):
        if operation == 'revision':
            previous = self.current_snapshot
            if previous is None:
                raise StudioError('修正元の設計履歴がありません')
            delta = compare_scenes(previous['scene'], scene)
            snapshot = self.history.commit(design, scene, engine_commit=self.manager.active_sha,
                                           operation='revision', previous_id=previous['revisionId'])
        else:
            delta = None
            snapshot = self.history.commit(design, scene, engine_commit=self.manager.active_sha,
                                           operation='new')
        self.current_snapshot = snapshot
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
        original = self.design
        pinned_hash = self.scene['geometrySha256']
        def work():
            check = run_bridge(engine, original)
            if check['geometrySha256'] != pinned_hash:
                raise StudioError('現在のエンジンは元の設計を再現できません。変更を中止しました')
            error = ''
            draft = None
            for attempt in range(2):
                prev = draft
                try:
                    draft = ask_llm(provider, model, instruction, error,
                                    previous_design=prev, revision_base=original)
                    scene = run_bridge(engine, draft)
                    return draft, scene, attempt
                except ModelTransportError:
                    raise  # connectivity/HTTP failures do not merit a repeat
                except StudioError as exc:
                    error = str(exc)
            raise StudioError('AI修正候補がMAB SOURCE検証に通りませんでした: '+error)
        def done(data):
            revised, scene, retry = data
            # The model response is only a DRAFT.  No RevisionStore.commit,
            # no latest-design overwrite until the user explicitly approves.
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
            self.view_mode.set('変更差分')
            self._refresh_view()
            counts = delta['counts']
            self.caption.set(f'未承認のAI修正案：追加{counts["added"]} / 削除{counts["removed"]} / '
                             f'交換{counts["replaced"]}  |  3Dを確認して承認または却下（未施工）')
            self.status.set('修正案の検証に成功しました。承認前の設計履歴は未変更です。')
        self._start('AI修正案の検証＋差分計算（ワールドには未反映）',work,done)

    def approve_revision(self):
        candidate = self.pending_revision
        if not candidate:
            return
        engine = self.manager.active_path
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
            self.view_mode.set('変更差分')
            self._refresh_view()
            self.status.set('承認済み：設計履歴だけ更新しました。Minecraftワールドは未変更です。')
        self._start('承認前のSOURCE真正再検証（ワールド書き込みなし）',work,done)

    def reject_revision(self):
        if self.pending_revision is None or self.busy:
            return
        self.pending_revision = None
        self.view_mode.set('建築のみ')
        self._refresh_view()
        self._update_engine_label()
        self.caption.set('AI修正案を却下しました。承認済みの元設計と履歴を維持しています。')
        self.status.set('修正案を破棄しました。Minecraftワールドは未変更です。')

    def restore_previous(self):
        engine=self.manager.active_path
        if engine is None:
            return
        def done(data):
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
            title='表示中の承認済みSOURCE設計をJSON保存（Minecraft施工ファイルではありません）',
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
                '保存先: '+str(path)+'\n\nMAB設計JSONのみです。Minecraft施工許可やワールドの復元にはなりません。')
        self._start('表示中の承認済み設計をSOURCE再検証してJSON保存',work,done)


def main():
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
    Studio().mainloop()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
