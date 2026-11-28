"""Engine download/pinning, compatibility checks and local LLM transport.

No API here connects to Minecraft or writes world coordinates.
"""
from __future__ import annotations
import copy

import hashlib
import http.client
import socket
import threading
import io
import json
import os
import time
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import urllib.error
import zipfile

REPO = "ryomashibaba/MinecraftAIBuilder"
DEFAULT_BRANCH = "feature/b143-universal-llm-building-dsl"
# Verified via GitHub metadata on 2026-11-17; this SHA is the known B143 contract.
BASELINE_SHA = "b0295c2f5012877f57ae218ae791634794de7b52"
HEX40 = re.compile(r"^[a-f0-9]{40}$")
ENGINE_FILE = "source/ai/b143-building-dsl.mjs"
SCHEMA = "minecraftaibuilder-b143-building-dsl-v1"
MAX_ZIP_BYTES = 60 * 1024 * 1024
MAX_UNPACK_BYTES = 260 * 1024 * 1024
MAX_MEMBERS = 5000
MAX_RESPONSE = 1024 * 1024
MAX_PINNED_MODULE_BYTES = 256 * 1024
OLLAMA_BASE = "http://127.0.0.1:11434"
LM_STUDIO_BASE = "http://127.0.0.1:1234"
# Only known loopback endpoints may receive user prompts.  urllib's default
# opener follows HTTP redirects, which could otherwise leak the prompt off-host.
class _RefuseModelRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StudioError("ローカルAIが外部へのリダイレクトを要求したため通信を拒否しました")


# The model request can block *before* urlopen returns (waiting for HTTP
# response headers), so watching only the returned HTTPResponse is insufficient.
# Capture the socket as soon as urllib connects; never affect other apps or
# the read-only model-management calls on separate threads.
_MODEL_REQUEST_STATE = threading.local()


class _TrackedModelConnection(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        watcher = getattr(_MODEL_REQUEST_STATE, 'watcher', None)
        if watcher is not None:
            watcher.track(self.sock)


class _TrackedModelHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_TrackedModelConnection, req)


class _ModelRequestWatch:
    """Cancel a single HTTP request, including the wait for first headers.

    No inference retry, server kill, model unload, or mutation outside this
    request's socket. A model server may continue computing after disconnect.
    """
    def __init__(self, cancel_event, *, total_seconds=None):
        self.cancel_event = cancel_event
        self.deadline = (time.monotonic() + total_seconds
                         if total_seconds is not None else None)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._sock = None
        self.reason = None
        self._thread = None

    @staticmethod
    def _interrupt(sock):
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def track(self, sock):
        with self._lock:
            self._sock = sock
            interrupted = self.reason is not None
        if interrupted:
            self._interrupt(sock)

    def start(self):
        if self.cancel_event is None and self.deadline is None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='MABStudio-model-request-watch')
        self._thread.start()

    def _run(self):
        while not self._stop.wait(0.05):
            if self.cancel_event is not None and self.cancel_event.is_set():
                why = 'cancelled'
            elif self.deadline is not None and time.monotonic() >= self.deadline:
                why = 'deadline'
            else:
                continue
            with self._lock:
                self.reason = why
                sock = self._sock
            self._interrupt(sock)
            return

    def assert_not_interrupted(self):
        _raise_if_cancelled(self.cancel_event)
        if self.reason == 'deadline':
            raise ModelTransportError(
                'ローカルAI生成が総時間上限を超えました（15分）。追加推論せず停止しました')

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)


LOCAL_HTTP = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _RefuseModelRedirect(),
    _TrackedModelHTTPHandler()
)
# Exact SHA256 of GitHub B143 source at commit b0295c2... (Git blob 3e257f9f...).
# Source changes require a separately reviewed app release; a GitHub branch move
# is never permission to execute unreviewed third-party JavaScript on a user's PC.
APPROVED_B143_SOURCE_SHA256 = "537aa9e4a2b4e027f16179ef7a306b604ca4d850713d6de23940667ed2348e16"
PINNED_SMOKE_GEOMETRY_SHA256 = "0486ea0ef8a5ea9741742251eec2e55cdd235fb8106068ba6ae4b8508d01403b"


class StudioError(Exception):
    pass


def user_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "MABStudio"


def atomic_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".state-", suffix=".tmp", delete=False) as f:
        tmp = Path(f.name)
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def json_read(path: Path, default: object = None) -> object:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def request_json(url: str, timeout: int = 12) -> dict:
    if not url.startswith(f"https://api.github.com/repos/{REPO}/"):
        raise StudioError("GitHub以外のURLへ接続できません")
    req = urllib.request.Request(url, headers={"User-Agent": "MABStudio/0.1", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise StudioError("GitHubの応答が大きすぎます")
    return json.loads(raw)


def branch_sha(branch: str = DEFAULT_BRANCH) -> str:
    # Do not allow custom URLs, backtracking, query strings or accidental ref injection.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,120}", branch) or ".." in branch or "//" in branch:
        raise StudioError("ブランチ名が不正です")
    safe = urllib.parse.quote(branch, safe="/")
    data = request_json(f"https://api.github.com/repos/{REPO}/branches/{safe}")
    sha = data.get("commit", {}).get("sha", "")
    if not HEX40.fullmatch(sha):
        raise StudioError("GitHubから正しいコミットSHAを取得できません")
    return sha


def _source_sha256(engine: Path) -> str:
    source = engine / ENGINE_FILE
    if not source.is_file():
        raise StudioError("MAB B143エンジンが存在しません")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != APPROVED_B143_SOURCE_SHA256:
        raise StudioError("B143のコードが安全確認済み版と異なります。更新を保留します")
    return digest


def _sandbox_node_args(node: str, engine: Path, inp: Path, out: Path) -> list[str]:
    # A reviewed B143 source has zero local JS imports: grant only these 3 reads.
    # Node 22+ permission model blocks unexpected fs reads/writes, workers and
    # child processes. This is an additional guard, not a full network sandbox.
    script = bridge_script().resolve()
    return [node, "--permission",
            f"--allow-fs-read={script}",
            f"--allow-fs-read={(engine / ENGINE_FILE).resolve()}",
            f"--allow-fs-read={inp.resolve()}",
            f"--allow-fs-write={out.resolve()}",
            str(script)]


def node_binary() -> str:
    # Windows distribution includes its own node.exe; developers may use PATH.
    if getattr(sys, "frozen", False):
        # A released EXE must NEVER silently rely on a developer's PATH Node.
        # Otherwise CI can pass while the distributed zip is nonfunctional.
        bundled = Path(sys.executable).resolve().parent / "node.exe"
        if not bundled.is_file():
            raise StudioError("配布EXEのnode.exeがありません。dist\\MABStudioフォルダを丸ごと展開してください。")
        return str(bundled)
    local = Path(__file__).resolve().parent / "node.exe"
    if local.is_file():
        return str(local)
    node = shutil.which("node")
    if not node:
        raise StudioError("Node.js 22以降が見つかりません。開発用ソース実行にはNode.jsが必要です。")
    return node


def bridge_script() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "app" / "engine_bridge.mjs"


def explain_b143_local_bounds(design: dict) -> str | None:
    """Explain an already-rejected B143 spatial error; never change SOURCE.

    Uses the pinned B143 shape-envelope rules for display only. This is NOT
    a geometry validator, automatic repair, or substitute for run_bridge().
    """
    if not isinstance(design, dict):
        return None
    bounds = design.get('bounds')
    elems = design.get('elements')
    if not isinstance(bounds, dict) or not isinstance(elems, list):
        return None
    dims = [bounds.get(k) for k in ('width', 'height', 'depth')]
    if any(type(v) is not int or v <= 0 for v in dims):
        return None
    for i, element in enumerate(elems):
        if not isinstance(element, dict):
            continue
        shape = element.get('shape')
        low = high = None
        if shape == 'box':
            origin, size = element.get('origin'), element.get('size')
            if _safe_vec3(origin) and _safe_vec3(size):
                low = origin
                high = [origin[j] + size[j] - 1 for j in range(3)]
        elif shape == 'ellipsoid':
            center, radii = element.get('center'), element.get('radii')
            if _safe_vec3(center) and _safe_vec3(radii):
                low = [center[j] - radii[j] for j in range(3)]
                high = [center[j] + radii[j] for j in range(3)]
        elif shape == 'cylinder':
            origin, radius, length, axis = (element.get('origin'), element.get('radius'),
                                            element.get('length'), element.get('axis'))
            if (_safe_vec3(origin) and type(radius) is int and type(length) is int
                    and axis in ('x', 'y', 'z')):
                along = {'x': 0, 'y': 1, 'z': 2}[axis]
                low = [origin[j] - (0 if j == along else radius) for j in range(3)]
                high = [origin[j] + (length - 1 if j == along else radius) for j in range(3)]
        if low is None:
            continue
        violations = [f'{axis}={low[j]}..{high[j]}（許可0..{dims[j]-1}）'
                      for j, axis in enumerate('XYZ')
                      if low[j] < 0 or high[j] >= dims[j]]
        if violations:
            element_id = element.get('id')
            identifier = (element_id[:40] if isinstance(element_id, str)
                          else f'index={i}')
            return (f'要素「{identifier}」（{shape}）がboundsの外です: '
                    + '、'.join(violations) + '。'
                    '修正案は採用されず、承認済み建築と履歴は変更されません。'
                    '修正指示で形状とboundsの整合を指定してください。')
    return None


def _safe_vec3(value: object) -> bool:
    return (isinstance(value, list) and len(value) == 3
            and all(type(v) is int for v in value))


def run_bridge(engine: Path, design: dict, timeout: int = 60) -> dict:
    """The Node bridge imports the *actual installed MAB module*, never a copy."""
    _source_sha256(engine)
    with tempfile.TemporaryDirectory(prefix="mab-studio-compile-") as d:
        tmp = Path(d)
        inp = tmp / "design.json"
        out = tmp / "scene.json"
        inp.write_text(json.dumps(design, ensure_ascii=False), encoding="utf-8")
        args = _sandbox_node_args(node_binary(), engine, inp, out) + ["compile", str(engine.resolve()), str(inp), str(out)]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=d)
        except subprocess.TimeoutExpired as e:
            raise StudioError("MAB SOURCE検証が時間切れになりました") from e
        if r.returncode:
            msg = (r.stderr or r.stdout or "MABの検証失敗").strip()[-700:]
            if 'B143_OUTSIDE_LOCAL_BOUNDS' in msg:
                detail = explain_b143_local_bounds(design)
                if detail:
                    msg = 'B143_OUTSIDE_LOCAL_BOUNDS: ' + detail
            raise StudioError(msg)
        if not out.exists() or out.stat().st_size > 16 * 1024 * 1024:
            raise StudioError("MABの出力がありません、または大きすぎます")
        scene = json_read(out)
        if not isinstance(scene, dict):
            raise StudioError("MABの出力が正しくありません")
        # Independent refusal gate for app-owned actions (not a reimplementation of MAB).
        if (scene.get("designOnly") is not True or scene.get("readiness") != "NOT_PREPARED"
                or scene.get("worldContacts") != 0 or scene.get("worldCoordinates") is not None
                or scene.get("stats", {}).get("constructionApproved") is not False
                or scene.get("BuildPlan", {}).get("operations") != []):
            raise StudioError("施工禁止ゲートに不一致があります。処理を中断しました")
        return scene


def compatibility_check(engine: Path) -> dict:
    """Run a known small design against the downloaded engine before activation."""
    doc = {"schema": SCHEMA, "id": "studio-smoke", "title": "Studio smoke test",
           "brief": "Offline geometry check", "designOnly": True,
           "bounds": {"width": 12, "height": 12, "depth": 12},
           "elements": [{"id": "base", "shape": "box", "mode": "add", "material": "stone",
                         "origin": [2, 1, 2], "size": [8, 3, 8]}]}
    scene = run_bridge(engine, doc)
    if (scene.get("stats", {}).get("voxels") != 192
            or scene.get("geometrySha256") != PINNED_SMOKE_GEOMETRY_SHA256):
        raise StudioError("MABの計算結果が安全確認済み版と異なります")
    return {"sourceSha256": scene["sourceSha256"], "geometrySha256": scene["geometrySha256"],
            "schema": SCHEMA, "voxels": 192, "readOnly": True}


def _safe_extract_zip(zbytes: bytes, target: Path, expected_commit: str | None = None) -> None:
    if len(zbytes) > MAX_ZIP_BYTES:
        raise StudioError("アーカイブの容量が上限を超えています")
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        items = z.infolist()
        if len(items) > MAX_MEMBERS or sum(x.file_size for x in items) > MAX_UNPACK_BYTES:
            raise StudioError("アーカイブの展開容量またはファイル数が上限を超えています")
        files = [x for x in items if not x.is_dir()]
        if not files:
            raise StudioError("空のアーカイブです")
        roots = {x.filename.split("/")[0] for x in files}
        if len(roots) != 1:
            raise StudioError("GitHubアーカイブのルートが異常です")
        root_name = next(iter(roots))
        if expected_commit is not None:
            if not HEX40.fullmatch(expected_commit):
                raise StudioError("検証するコミットが不正です")
            prefix = "MinecraftAIBuilder-"
            suffix = root_name[len(prefix):] if root_name.startswith(prefix) else ""
            if not (7 <= len(suffix) <= 40 and re.fullmatch("[a-f0-9]+", suffix)
                    and expected_commit.startswith(suffix)):
                raise StudioError("ダウンロードしたアーカイブとコミットが一致しません")
        seen = set()
        for member in items:
            # ZIP entries must be portable to Windows, even during Linux tests.
            name = member.filename
            if "\\" in name or name.startswith("/") or name.startswith("//"):
                raise StudioError("ZIPにWindows非互換パスがあります")
            parts = name.rstrip("/").split("/")
            if member.is_dir() and parts == [root_name]:
                target.mkdir(parents=True, exist_ok=True)
                continue
            if (len(parts) < 2 or parts[0] != root_name
                    or any(x in ("", ".", "..") or ":" in x or x[-1:] in (" ", ".")
                           for x in parts)):
                raise StudioError("不正なアーカイブパスです")
            if any(re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", x, re.I)
                   for x in parts[1:]):
                raise StudioError("Windows予約ファイル名は禁止されています")
            path_key = "/".join(parts[1:]).lower()
            if path_key in seen:
                raise StudioError("Windowsで重複するアーカイブパスがあります")
            seen.add(path_key)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise StudioError("シンボリックリンクは禁止されています")
            relative = PurePosixPath(*parts[1:])
            dest = target.joinpath(*relative.parts)
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, dest.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 128 * 1024)


def _download_reviewed_b143(sha: str) -> bytes:
    """Fetch only the one independently approved B143 JS file, not an entire repo.

    The commit AND content hash are pinned. This never tracks a moving branch or
    silently authorizes a newer MAB engine. Offline use works once installed.
    """
    if sha != BASELINE_SHA:
        raise StudioError("新しいMABコミットはまだStudioの互換性・安全審査対象外です。導入を保留します")
    url = (f"https://raw.githubusercontent.com/{REPO}/{sha}/"
           f"{ENGINE_FILE}")
    req = urllib.request.Request(url, headers={"User-Agent": "MABStudio/0.3"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            source = res.read(MAX_PINNED_MODULE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise StudioError("固定B143ソースの取得に失敗しました。接続後に再試行してください") from exc
    if not source or len(source) > MAX_PINNED_MODULE_BYTES:
        raise StudioError("固定B143モジュールのサイズが不正です")
    if hashlib.sha256(source).hexdigest() != APPROVED_B143_SOURCE_SHA256:
        raise StudioError("取得したB143モジュールのSHA-256が承認済み原本と一致しません")
    return source


class EngineManager:
    def __init__(self, root: Path | None = None):
        self.root = root or user_root()
        self.versions = self.root / "engines"
        self.state_path = self.root / "state.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = json_read(self.state_path, {})
        if not isinstance(self.state, dict):
            raise StudioError("状態ファイルを読み取れません")

    @property
    def active_sha(self) -> str | None:
        return self.state.get("active")

    @property
    def active_path(self) -> Path | None:
        sha = self.active_sha
        if not isinstance(sha, str) or not HEX40.fullmatch(sha):
            return None
        p = self.versions / sha
        try:
            self._check_integrity(p, sha)
            return p
        except (StudioError, OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _check_integrity(engine: Path, sha: str) -> None:
        evidence = json_read(engine / ".studio-evidence.json")
        if not isinstance(evidence, dict) or evidence.get("commit") != sha:
            raise StudioError("インストール証跡が不正です")
        digest = _source_sha256(engine)
        if digest != evidence.get("engineSha256"):
            raise StudioError("エンジンファイルの改変を検出しました")

    def install(self, sha: str, *, archive: bytes | None = None) -> dict:
        if not HEX40.fullmatch(sha):
            raise StudioError("更新に使うコミットSHAが不正です")
        target = self.versions / sha
        if target.exists():
            self._check_integrity(target, sha)
            evidence = compatibility_check(target)
        else:
            self.versions.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.versions))
            try:
                if archive is None:
                    # The reviewed B143 module has no imports or other source
                    # dependencies. Downloading the giant repository archive is
                    # unnecessary for the normal first-run experience.
                    source = _download_reviewed_b143(sha)
                    dest = staging / ENGINE_FILE
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(source)
                    provenance = {"installMethod": "PINNED_SINGLE_MODULE",
                                  "moduleDownloadSha256": hashlib.sha256(source).hexdigest()}
                else:
                    if len(archive) > MAX_ZIP_BYTES:
                        raise StudioError("ダウンロード容量の上限を超えました")
                    _safe_extract_zip(archive, staging, expected_commit=sha)
                    provenance = {"installMethod": "EXPLICIT_ARCHIVE",
                                  "archiveSha256": hashlib.sha256(archive).hexdigest()}
                evidence = compatibility_check(staging)
                atomic_json(staging / ".studio-evidence.json", {"commit": sha,
                            **provenance,
                            "engineSha256": hashlib.sha256((staging / ENGINE_FILE).read_bytes()).hexdigest(),
                            "compatibility": evidence})
                staging.rename(target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        # The active version changes only AFTER successful compilation & safety checks.
        old = self.active_sha
        new_state = {**self.state, "active": sha, "previous": old if old != sha else self.state.get("previous"),
                     "tracked_branch": self.state.get("tracked_branch", DEFAULT_BRANCH)}
        atomic_json(self.state_path, new_state)
        self.state = new_state
        return evidence

    def rollback(self) -> str:
        prev = self.state.get("previous")
        if not isinstance(prev, str) or not HEX40.fullmatch(prev) or not (self.versions / prev / ENGINE_FILE).is_file():
            raise StudioError("戻せる旧バージョンがありません")
        self._check_integrity(self.versions / prev, prev)
        compatibility_check(self.versions / prev)
        active = self.active_sha
        new_state = {**self.state, "active": prev, "previous": active}
        atomic_json(self.state_path, new_state)
        self.state = new_state
        return prev

    def save_branch(self, branch: str) -> None:
        # Validate without network call.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,120}", branch) or ".." in branch or "//" in branch:
            raise StudioError("ブランチ名が不正です")
        obj = {**self.state, "tracked_branch": branch}
        atomic_json(self.state_path, obj)
        self.state = obj


def parse_llm_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    if len(text) > MAX_RESPONSE:
        raise StudioError("LLMの応答が大きすぎます")
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise StudioError("LLMが設計JSONを返しませんでした") from e
    if not isinstance(d, dict):
        raise StudioError("LLMの設計形式が正しくありません")
    return d


def _valid_b143_metadata(value: object, limit: int) -> bool:
    """Match B143 strictName, including JavaScript UTF-16 string length."""
    return (isinstance(value, str)
            and 1 <= len(value.encode('utf-16-le', errors='surrogatepass')) // 2 <= limit
            and not re.search(r'[\x00-\x1f<>\ud800-\udfff]', value))


def _metadata_single_line(text: str) -> str:
    # Only textual labels: geometry, element IDs, bounds and material are untouched.
    return (re.sub(r'[\x00-\x1f]+', ' ', text).replace('<', '＜')
            .replace('>', '＞').strip())


def _limit_utf16(text: str, limit: int) -> str:
    # B143 uses JS string.length (UTF-16 units), not Python code-point length.
    output, count = [], 0
    for char in text:
        if 0xD800 <= ord(char) <= 0xDFFF:
            continue  # malformed stand-alone surrogate from the model
        length = 2 if ord(char) > 0xFFFF else 1
        if count + length > limit:
            break
        output.append(char)
        count += length
    return ''.join(output)


def repair_generated_metadata(design: dict, user_prompt: str,
                              revision_base: dict | None = None) -> tuple[dict, dict]:
    """Bounded, deterministic *metadata-only* repair of a model draft.

    Does not infer or modify spatial geometry. A missing/invalid text label is
    grounded in the user's actual prompt (or the approved revision's label).
    Returns a machine-readable, content-free repair receipt for transparency.
    True B143 is still the only validator and its rejection is never bypassed.
    """
    if not isinstance(design, dict):
        raise StudioError('LLMの設計形式が正しくありません')
    updated = dict(design)
    changes: dict[str, list[str]] = {}
    issues: dict[str, str] = {}
    for field, limit in (('title', 100), ('brief', 500)):
        original = updated.get(field)
        if _valid_b143_metadata(original, limit):
            continue
        if field not in updated or original is None:
            issues[field] = 'MISSING_OR_NULL'
        elif not isinstance(original, str):
            issues[field] = 'WRONG_TYPE'
        elif not original.strip():
            issues[field] = 'EMPTY_STRING'
        elif re.search(r'[\x00-\x1f<>\ud800-\udfff]', original):
            issues[field] = 'FORBIDDEN_CHARACTERS'
        else:
            issues[field] = 'EXCEEDS_UTF16_LIMIT'
        actions = []
        if isinstance(original, str) and original.strip():
            candidate = original
        elif revision_base is not None and _valid_b143_metadata(revision_base.get(field), limit):
            candidate = revision_base[field]
            actions.append('restored_from_approved_source')
        else:
            candidate = user_prompt
            actions.append('derived_from_user_prompt')
        cleaned = _metadata_single_line(candidate)
        if cleaned != candidate:
            actions.append('removed_forbidden_formatting')
        bounded = _limit_utf16(cleaned, limit)
        if bounded != cleaned:
            actions.append('limited_to_b143_text_length')
        if not _valid_b143_metadata(bounded, limit):
            raise StudioError('生成された'+field+'を安全に補正できませんでした')
        updated[field] = bounded
        changes[field] = actions or ['normalized_label']
    return updated, {'metadataOnly': True, 'fields': changes,
                     'originalIssues': issues,
                     'geometryUnchanged': True, 'worldWrites': 0}


def expand_generated_canvas_bounds(design: dict) -> tuple[dict, dict] | None:
    """Expand only a B143 draft's local canvas to contain its *unchanged* elements.

    Call this ONLY after the frozen B143 has rejected the original draft with
    B143_OUTSIDE_LOCAL_BOUNDS. Other syntax/geometry/source errors remain HOLD.
    This is no claim of visual quality and never grants construction approval.
    The caller MUST recompile and verify the resulting source with frozen B143.
    """
    if (not isinstance(design, dict) or design.get('schema') != SCHEMA
            or design.get('designOnly') is not True):
        return None
    bounds = design.get('bounds')
    elements = design.get('elements')
    if (not isinstance(bounds, dict) or set(bounds) != {'width', 'height', 'depth'}
            or not isinstance(elements, list) or not 1 <= len(elements) <= 160):
        return None
    axes = ('width', 'height', 'depth')
    limits = (128, 96, 128)
    dims = [bounds[k] for k in axes]
    if any(type(v) is not int or not 8 <= v <= limit
           for v, limit in zip(dims, limits)) or dims[0] * dims[1] * dims[2] > 500000:
        return None

    required = dims[:]
    def triplet(values, minimum, maximum):
        return (isinstance(values, list) and len(values) == 3
                and all(type(v) is int and minimum <= v <= maximum for v in values))

    for element in elements:
        if not isinstance(element, dict) or 'repeat' in element:
            return None  # No V11 extension or implicit source translation.
        shape = element.get('shape')
        if shape == 'box':
            origin, size = element.get('origin'), element.get('size')
            if not triplet(origin, 0, 128) or not triplet(size, 1, 128):
                return None
            low = origin
            high = [a + b - 1 for a, b in zip(origin, size)]
        elif shape == 'ellipsoid':
            center, radii = element.get('center'), element.get('radii')
            if not triplet(center, 0, 128) or not triplet(radii, 1, 64):
                return None
            low = [c - r for c, r in zip(center, radii)]
            high = [c + r for c, r in zip(center, radii)]
        elif shape == 'cylinder':
            origin = element.get('origin')
            axis = element.get('axis')
            radius, length = element.get('radius'), element.get('length')
            if (not triplet(origin, 0, 128) or axis not in ('x', 'y', 'z')
                    or type(radius) is not int or not 1 <= radius <= 64
                    or type(length) is not int or not 1 <= length <= 128):
                return None
            along = ('x', 'y', 'z').index(axis)
            low = [v if i == along else v - radius for i, v in enumerate(origin)]
            high = [(v + length - 1) if i == along else v + radius
                    for i, v in enumerate(origin)]
        else:
            return None  # Unsupported geometry is NEVER guessed or repaired.
        if any(v < 0 for v in low):
            return None  # A canvas enlargement cannot fix negative coordinates.
        required = [max(old, endpoint + 1) for old, endpoint in zip(required, high)]

    if (required == dims or any(value > limit for value, limit in zip(required, limits))
            or required[0] * required[1] * required[2] > 500000):
        return None
    corrected = {**design, 'bounds': dict(zip(axes, required))}
    changed = {key: {'before': old, 'after': new}
               for key, old, new in zip(axes, dims, required) if old != new}
    return corrected, {'boundsOnly': True, 'changes': changed,
                       'geometryElementsUnchanged': True, 'worldWrites': 0}


COMMON_SYSTEM_PROMPT = """あなたはMinecraftAIBuilder B143の設計JSON生成器です。
ユーザーの日本語の建築意図を保持した概念建築を生成します。
出力はB143設計JSONオブジェクト1個のみ。前置き・Markdown・思考過程・解説は不要です。
必須形式（省略禁止）：
{"schema":"minecraftaibuilder-b143-building-dsl-v1","id":"lowercase-id","title":"短い題名","brief":"短い説明","designOnly":true,"bounds":{"width":32,"height":24,"depth":32},"elements":[...]}
要素例（各shapeに必要なプロパティだけを書く）：
{"id":"wall","shape":"box","mode":"add","material":"stone","origin":[1,1,1],"size":[10,5,10],"shell":1}
{"id":"opening","shape":"box","mode":"cut","origin":[4,1,1],"size":[3,4,2]}
形状は box / ellipsoid(center,radii) / cylinder(origin,axis,radius,length)。
材質は stone, concrete, metal, glass, wood, glow, accent の7種。
各要素の座標はbounds内の整数。最大160要素はB143エンジン自体の上限です。
cutにmaterialを付けない。shapeに無関係なキーは出力しない。
Minecraftコマンド、ワールド座標、URLは出力しない。
末尾まで完結したJSONで終え、続きの説明を出力しない。
"""

INITIAL_GENERATION_GUIDANCE = """【初回設計】
短く完結させるが単純な箱の寄せ集めにしない。通常は18～24要素を使い、最大24要素まで建築意図を表現する。
最初に建物の外形・階構成・特徴的な屋根や上部構造を決める。正面/側面の壁を巨大な箱1個で済ませず、複数の量塊を段違いに重ね、セットバック、張り出し、庇、柱、塔状コアなどで輪郭に変化をつける。窓・帯・床をまとめすぎて特徴を消さない。
boxだけで構成せず、意図に合うellipsoidまたはcylinderを少なくとも1要素、主要なシルエットを作る位置に使う。円筒コア、曲面屋根、ドーム、リング状設備など形の意味が分かる造形を優先し、色替えだけを造形差として扱わない。材質数を増やすより形状と凹凸で個性を出す。
外観のシルエット、建築的特徴、複数階の床と入口を優先する。4階建てなら4つの階が外観から判別できる床線・窓帯・段差を設け、各階を省略しない。
人が使う建物では、入口のcutを実際の外壁と交差させ、外部から入口までの床・段差をつなぐ。窓も外壁を貫く開口として配置し、必要なら開口にガラスを重ねる。複数階を設ける場合は床と階段で上下階を接続し、柱・設備シャフトを動線として扱わない。要素数に余裕がないときは装飾より入口・動線・採光を優先する。
「小さな」建物はboundsも控えめにし、幅・奥行12～24、高さ8～18セル程度を目安とする（大きさの指定がなければ32×24×32を避ける）。cutは対象壁の実際の外面を見定め、壁厚を貫通させる。書棚・家具は歩行域を塞ぐ大きな塊にせず、階段は高さを変えた複数段で上下階をつなぐ。特徴的な屋根は厚い全面板にせず、庇や段違いを使う。
窓列や装飾帯はbox/cut/shellでまとめ、同種の窓を1個ずつ書き並べない。
B143のshell制約を守る。boxでshell>0なら3軸すべて size>2*shell、ellipsoidの各radiiとcylinderのradiusはshellより大きくする。初回はshell=0を基本にし、薄い中空壁だけshell=1を使う。shell=2以上は避ける。厚い殻は使わず、必要な厚みは複数のadd要素で表現する。
すべての座標はbounds内の0始まりローカル座標。boxはorigin+sizeが各bounds以下、ellipsoidはcenter±radiiが0..bounds-1、cylinderは軸方向のorigin+lengthと半径方向の外周がbounds内に収まることを確認する。bounds.heightはセル数なので、屋上部材・アンテナの最高Y座標より大きくし、Y=heightを含めない。
タイトルは48文字以内、説明は120文字以内、要素IDは24文字以内。
titleとbriefは必ず1行の普通の文字列とし、省略・改行・<・>は禁止。
生成予算を使い切る前にJSONを閉じる。完全なB143 JSONだけを返す。
"""

SYSTEM_PROMPT = COMMON_SYSTEM_PROMPT + INITIAL_GENERATION_GUIDANCE


# Schema is a transport/output constraint, NOT a replacement for the pinned B143
# validateDesign/compileDesign/verifyCompiled authority. Some local models cannot
# generate grammar-constrained JSON; the API wrapper handles explicit 400/422
# incompatibility below without claiming semantic or architectural success.
B143_JSON_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema", "id", "title", "brief", "designOnly", "bounds", "elements"],
    "properties": {
        "schema": {"const": SCHEMA},
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9-]{2,47}$"},
        "title": {"type": "string", "minLength": 1, "maxLength": 100},
        "brief": {"type": "string", "minLength": 1, "maxLength": 500},
        "designOnly": {"const": True},
        "bounds": {
            "type": "object", "additionalProperties": False,
            "required": ["width", "height", "depth"],
            "properties": {
                "width": {"type": "integer", "minimum": 8, "maximum": 128},
                "height": {"type": "integer", "minimum": 8, "maximum": 96},
                "depth": {"type": "integer", "minimum": 8, "maximum": 128},
            },
        },
        "elements": {
            "type": "array", "minItems": 1, "maxItems": 160,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "shape", "mode"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-zA-Z][a-zA-Z0-9_-]{0,39}$"},
                    "shape": {"type": "string", "enum": ["box", "ellipsoid", "cylinder"]},
                    "mode": {"type": "string", "enum": ["add", "cut"]},
                    "material": {"type": "string", "enum": ["stone", "concrete", "metal", "glass", "wood", "glow", "accent"]},
                    "origin": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 128}, "minItems": 3, "maxItems": 3},
                    "size": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 128}, "minItems": 3, "maxItems": 3},
                    "center": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 128}, "minItems": 3, "maxItems": 3},
                    "radii": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 64}, "minItems": 3, "maxItems": 3},
                    "axis": {"type": "string", "enum": ["x", "y", "z"]},
                    "radius": {"type": "integer", "minimum": 1, "maximum": 64},
                    "length": {"type": "integer", "minimum": 1, "maximum": 128},
                    "shell": {"type": "integer", "minimum": 0, "maximum": 4},
                },
            },
        },
    },
}


class ModelTransportError(StudioError):
    """A server/transport failure; do not repeat expensive model inference."""


class ModelCancelled(StudioError):
    """The user cancelled locally; server-side inference state is unknown."""


def _raise_if_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ModelCancelled('生成をキャンセルしました。回答は破棄しました。サーバー側の推論停止は確認できません。')


class ModelOutputLimitError(StudioError):
    """The model ended before its full JSON. Never retry with the same limits."""


# This is an OUTPUT ceiling, not a guarantee of capacity. LM Studio can have a
# smaller loaded context window. In that case we reduce the output allowance
# without silently changing or reloading the user's model (which costs VRAM).
DEFAULT_OUTPUT_TOKENS = 6400
MAX_OUTPUT_TOKENS = 8000
# A complete B143 source is still verified by the pinned MAB engine. The output
# grammar only bounds the FIRST proposal so a normal building fits in one reply.
MAX_MODEL_ELEMENTS = 24
# Compact output is a first-proposal transport boundary, not a new MAB
# architecture engine. Full B143 source (including richer revisions) is still
# checked by the unchanged pinned MAB validate/compile/verify functions.
def _shape_specific_output_schema(*, max_elements: int, compact: bool) -> dict:
    """Describe B143's actual shape/mode *syntax* to constrained decoders.

    A source `add` needs a material; a `cut` may NOT contain material or
    shell. Each shape needs different coordinates. This is an OUTPUT grammar,
    not a source compiler, spatial validator or construction authorization.
    B143 remains the sole authority for coordinates, work/voxel limits and
    geometry. `anyOf` arms are disjoint by shape+mode.
    """
    base = B143_JSON_SCHEMA['properties']
    raw = B143_JSON_SCHEMA['properties']['elements']['items']['properties']
    identity = {**raw['id']}
    if compact:
        identity['maxLength'] = 24
    shapes = {
        'box': ('origin', 'size'),
        'ellipsoid': ('center', 'radii'),
        'cylinder': ('origin', 'axis', 'radius', 'length'),
    }
    variants = []
    for shape, coordinates in shapes.items():
        for mode in ('add', 'cut'):
            required = ['id', 'shape', 'mode', *coordinates]
            props = {
                'id': identity,
                'shape': {'const': shape},
                'mode': {'const': mode},
                **{key: raw[key] for key in coordinates},
            }
            if mode == 'add':
                required.append('material')
                props['material'] = raw['material']
                props['shell'] = raw['shell']
            variants.append({'type': 'object', 'additionalProperties': False,
                             'required': required, 'properties': props})
    return {
        **B143_JSON_SCHEMA,
        'properties': {
            **base,
            'title': {**base['title'], 'maxLength': 48 if compact else 100},
            'brief': {**base['brief'], 'maxLength': 120 if compact else 500},
            'elements': {
                'type': 'array', 'minItems': 1, 'maxItems': max_elements,
                'items': {'anyOf': variants},
            },
        },
    }


MODEL_GENERATION_SCHEMA = _shape_specific_output_schema(
    max_elements=MAX_MODEL_ELEMENTS, compact=True)

# A revision can optionally return only the edited B143 elements. This is a
# transport format (never a second construction or geometry engine). B143 is
# still the ONLY authority that validates/compiles the merged complete source.
REVISION_DELTA_SCHEMA_ID = 'mab-studio-b143-revision-delta-v1'
MAX_DELTA_CHANGES = 32
_B143_ELEMENT_OUTPUT = _shape_specific_output_schema(
    max_elements=MAX_DELTA_CHANGES, compact=False)['properties']['elements']['items']
REVISION_DELTA_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['schema', 'replace', 'add', 'removeIds'],
    'properties': {
        'schema': {'const': REVISION_DELTA_SCHEMA_ID},
        'replace': {'type': 'array', 'maxItems': MAX_DELTA_CHANGES,
                    'items': _B143_ELEMENT_OUTPUT},
        'add': {'type': 'array', 'maxItems': MAX_DELTA_CHANGES,
                'items': _B143_ELEMENT_OUTPUT},
        'removeIds': {'type': 'array', 'maxItems': MAX_DELTA_CHANGES,
                      'items': {'type': 'string', 'pattern': '^[a-zA-Z][a-zA-Z0-9_-]{0,39}$'}},
        'bounds': B143_JSON_SCHEMA['properties']['bounds'],
        'title': B143_JSON_SCHEMA['properties']['title'],
        'brief': B143_JSON_SCHEMA['properties']['brief'],
    },
}


def _revision_delta_output_schema(base: dict) -> dict:
    """Constrain EXISTING edit targets to their exact approved SOURCE IDs.

    For ordinary <=32-element proposals, the existing transport schema already
    supports JSON Schema enums. The model may freely name NEW `add` elements,
    but it must use a real ID for `replace` and `removeIds`. Larger approved
    designs retain the prior schema to avoid a huge repeated enum grammar;
    apply_revision_delta always validates the returned IDs independently.
    """
    elements = base.get('elements') if isinstance(base, dict) else None
    if not isinstance(elements, list) or not elements:
        raise StudioError('軽量修正の元SOURCE要素が不正です')
    identifiers = [e.get('id') if isinstance(e, dict) else None for e in elements]
    if (any(not isinstance(i, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,39}', i)
            for i in identifiers) or len(identifiers) != len(set(identifiers))):
        raise StudioError('軽量修正の元SOURCE要素IDが不正または重複しています')
    schema = copy.deepcopy(REVISION_DELTA_SCHEMA)
    if len(identifiers) <= MAX_DELTA_CHANGES:
        # The base schema intentionally shares the variant definition between
        # replace and add. Detach before binding IDs, or new IDs get blocked.
        schema['properties']['replace']['items'] = copy.deepcopy(
            schema['properties']['replace']['items'])
        for variant in schema['properties']['replace']['items']['anyOf']:
            variant['properties']['id']['enum'] = identifiers
        schema['properties']['removeIds']['items']['enum'] = identifiers
    return schema


def apply_revision_delta(base: dict, delta: dict) -> dict:
    """Merge an explicitly model-authored edit without inventing any geometry.

    The original approved object is never mutated; missing fields are retained
    byte-for-byte at JSON value level. Do not auto-clamp coordinates or enlarge
    bounds: the pinned B143 engine validates the resulting complete source.
    """
    if not isinstance(base, dict) or base.get('schema') != SCHEMA or not isinstance(base.get('elements'), list):
        raise StudioError('軽量修正の元SOURCEが不正です')
    if not isinstance(delta, dict) or delta.get('schema') != REVISION_DELTA_SCHEMA_ID:
        raise StudioError('AIが軽量差分形式で回答しませんでした。自動再推論しません。'
                          '必要なら詳細設定で「AI修正を軽量化」をOFFにして全文修正を選べます')
    allowed = set(REVISION_DELTA_SCHEMA['properties'])
    if set(delta) - allowed or not all(name in delta for name in ('replace', 'add', 'removeIds')):
        raise StudioError('軽量修正JSONに未対応の項目・不足項目があります')
    edits = [delta[name] for name in ('replace', 'add', 'removeIds')]
    if any(not isinstance(items, list) or len(items) > MAX_DELTA_CHANGES for items in edits):
        raise StudioError('軽量修正の変更件数が許容範囲外です')
    before = base['elements']
    if any(not isinstance(e, dict) or not isinstance(e.get('id'), str) for e in before):
        raise StudioError('修正元のB143要素IDが不正です')
    old_ids = [e['id'] for e in before]
    if len(set(old_ids)) != len(old_ids):
        raise StudioError('修正元に重複した要素IDがあります')
    replace, add, remove = edits
    if any(not isinstance(e, dict) or not isinstance(e.get('id'), str) for e in (*replace, *add)):
        raise StudioError('軽量修正に不正な要素があります')
    if any(not isinstance(identifier, str) for identifier in remove):
        raise StudioError('軽量修正の削除対象IDが不正です')
    replace_ids = [e['id'] for e in replace]
    add_ids = [e['id'] for e in add]
    identifiers = replace_ids + add_ids + remove
    if len(set(identifiers)) != len(identifiers):
        raise StudioError('軽量修正で同じ要素IDを複数回変更しようとしました')
    existing = set(old_ids)
    if not set(replace_ids + remove) <= existing or set(add_ids) & existing:
        raise StudioError('軽量修正の対象IDが元の設計と一致しません')
    if len(before) - len(remove) + len(add) > 160:
        raise StudioError('軽量修正でB143の要素数上限を超えます')
    updated = copy.deepcopy(base)
    replacements = {e['id']: copy.deepcopy(e) for e in replace}
    removed = set(remove)
    updated['elements'] = [replacements.get(e['id'], e) for e in updated['elements']
                           if e['id'] not in removed]
    updated['elements'].extend(copy.deepcopy(add))
    for key in ('title', 'brief', 'bounds'):
        if key in delta:
            updated[key] = copy.deepcopy(delta[key])
    if updated == base:
        raise StudioError('AIの軽量修正案には変更がありません。無駄なSOURCE履歴は作りません')
    return updated


# Long local GGUF inferences can need several minutes; while streaming, these
# limits apply to idle reads and the complete request, not the total first-byte
# to last-byte HTTP buffering delay of a non-streaming reply.
MODEL_STREAM_IDLE_TIMEOUT = 300
MODEL_STREAM_TOTAL_TIMEOUT = 900
# Gemma 4 with an 8k loaded context can spend its whole output allowance in
# hidden reasoning, returning zero SOURCE JSON. Stop that single wasted request
# once a substantial thought-only output is observed; do NOT retry or change the
# loaded model. The server may ignore per-request thinking controls.
GEMMA4_THOUGHT_ONLY_CHAR_LIMIT = 8000


def _loaded_lm_studio_context(model: str, timeout: float = 2.5) -> int | None:
    """Read the selected *already loaded* LM Studio model's context, if exposed.

    The optional /api/v1/models endpoint must never load a model, change its
    settings, or become a hard dependency for older OpenAI-compatible servers.
    """
    try:
        req = urllib.request.Request(LM_STUDIO_BASE + '/api/v1/models',
                                     headers={'User-Agent': 'MABStudio/0.3'})
        with LOCAL_HTTP.open(req, timeout=timeout) as res:
            raw = res.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return None
        data = json.loads(raw)
        entries = data.get('models') if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return None
        matches = []
        for item in entries[:150]:
            if not isinstance(item, dict):
                continue
            loaded = item.get('loaded_instances')
            if not isinstance(loaded, list):
                continue
            for instance in loaded[:20]:
                if not isinstance(instance, dict):
                    continue
                if instance.get('id') != model and item.get('key') != model:
                    continue
                config = instance.get('config')
                length = config.get('context_length') if isinstance(config, dict) else None
                if type(length) is int and 1024 <= length <= 1048576:
                    matches.append(length)
        # If an alias matches several instances, use the smaller window.
        return min(matches) if matches else None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError, TypeError):
        return None


def _output_budget(provider: str, model: str, messages: list[dict]) -> tuple[int, int | None]:
    context = _loaded_lm_studio_context(model) if provider == 'LM Studio' else None
    if context is None:
        return DEFAULT_OUTPUT_TOKENS, None
    # Conservative headroom for actual chat instructions and tokenization
    # differences. This is only an estimate, not a tokenizer.
    # A constrained decoder consumes the JSON Schema as a grammar, not as
    # chat history text. Do not charge its six alternative object definitions
    # against the model's input tokens; only the actual messages are sent to
    # the chat prompt. Keep generous headroom for system and tokenizer costs.
    request_chars = len(json.dumps({'messages': messages}, ensure_ascii=False))
    reserve = max(1800, (request_chars + 1) // 2 + 480)
    remaining = context - reserve
    if remaining < 800:
        raise ModelOutputLimitError(
            f'LM Studioのロード済みコンテキスト長{context}トークンでは、'
            'この設計指示とSOURCE形式に必要な余裕がありません。'
            'モデルのコンテキスト設定を確認してください（Studioは変更しません）。')
    return min(MAX_OUTPUT_TOKENS, remaining), context


def _truncated_error(provider: str, reply: dict, budget: int,
                     context: int | None) -> ModelOutputLimitError:
    usage = reply.get('usage')
    consumed = usage.get('completion_tokens') if isinstance(usage, dict) else None
    count = f'、生成済み{consumed}' if type(consumed) is int and consumed >= 0 else ''
    ctx = f'、ロード済み文脈{context}' if context is not None else ''
    metrics = reply.get('_studio_stream_metrics')
    detail = ''
    if isinstance(metrics, dict):
        body_chars = metrics.get('jsonBodyChars')
        thought_chars = metrics.get('reasoningChars')
        if type(body_chars) is int and type(thought_chars) is int:
            detail = f'、JSON本文{body_chars}文字・思考用出力{thought_chars}文字'
            if body_chars == 0 and thought_chars > 0:
                return ModelOutputLimitError(
                    f'{provider}が設計JSONの本文を出さず、思考用出力だけで上限に達しました'
                    f'（出力上限{budget}{count}{ctx}{detail}）。'
                    'MAB SOURCEは未検証です。LM StudioのThinking設定を確認してください。'
                    '自動再推論やモデル設定変更は行いません。')
    return ModelOutputLimitError(
        f'{provider}の設計JSON生成がトークン上限で途中終了しました'
        f'（出力上限{budget}{count}{ctx}{detail}）。MAB SOURCEはまだ検証していません。'
        '同じ条件での無駄な再推論は中止しました。'
        'LM Studioではモデルのコンテキスト長・生成上限を確認してください。'
        'コンテキスト長を増やすとRAM/VRAM使用量も増える可能性があります。')


def _read_lm_studio_stream(res, started: float, on_progress=None, cancel_event=None,
                           *, thought_only_limit: int | None = None) -> dict:
    """Read a single OpenAI-compatible SSE response, without repeating inference.

    Only the final assistant content is used. Reasoning tokens are never
    mistaken for JSON; invalid/unfinished events and timeout fail closed.
    """
    content = []
    chars = 0
    reasoning_chars = 0
    finish = None
    usage = None
    saw_done = False
    last_notice = started
    while True:
        _raise_if_cancelled(cancel_event)
        if time.monotonic() - started > MODEL_STREAM_TOTAL_TIMEOUT:
            raise ModelTransportError('ローカルAI生成が安全な総時間上限を超えました（15分）。追加推論せず停止しました')
        # A socket.makefile()/HTTPResponse stream is not reusable after a read
        # timeout. Never swallow the timeout and call readline() again: Python's
        # buffered reader then raises "cannot read from timed out object".
        # Cancellation instead interrupts the underlying socket from a watcher.
        line = res.readline(MAX_RESPONSE + 1)
        if not line:
            # socket.shutdown() on user cancel can look like a clean EOF.
            _raise_if_cancelled(cancel_event)
            break
        if len(line) > MAX_RESPONSE:
            raise ModelTransportError('ローカルAIのストリーム1行が大きすぎます')
        if not line.startswith(b'data:'):
            continue
        item = line[5:].strip()
        if item == b'[DONE]':
            saw_done = True
            break
        try:
            packet = json.loads(item)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelTransportError('ローカルAIのストリーム応答が壊れています') from exc
        if not isinstance(packet, dict):
            raise ModelTransportError('ローカルAIのストリーム形式が不正です')
        if 'error' in packet:
            raise ModelTransportError('ローカルAIが生成中にエラーを返しました。LM Studioのログを確認してください')
        if isinstance(packet.get('usage'), dict):
            usage = packet['usage']
        choices = packet.get('choices')
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ModelTransportError('ローカルAIの候補形式が不正です')
            delta = choice.get('delta')
            if isinstance(delta, dict):
                # Count only. Never save or expose private reasoning text.
                thought = delta.get('reasoning_content', delta.get('reasoning'))
                if isinstance(thought, str):
                    reasoning_chars += len(thought)
            part = delta.get('content') if isinstance(delta, dict) else None
            if part is not None and not isinstance(part, str):
                raise ModelTransportError('ローカルAIの本文形式が不正です')
            if part:
                content.append(part)
                chars += len(part)
                if chars > MAX_RESPONSE:
                    raise ModelTransportError('ローカルAIの出力が大きすぎます')
            # Avoid consuming minutes on a reasoning-only Gemma 4 response.
            # This is an explicit failure, never a prompt/model retry or a
            # conversion of private reasoning into SOURCE content.
            if (thought_only_limit is not None and chars == 0
                    and reasoning_chars >= thought_only_limit):
                raise ModelOutputLimitError(
                    'Gemma 4が設計JSONを1文字も出さないまま、思考用出力が'
                    f'{reasoning_chars}文字に達しました。無駄な推論を早期中止しました。'
                    'LM StudioでこのモデルのThinkingをOFFにできるか確認してください。'
                    'リクエスト側の思考OFF指定はLM Studioに無視される場合があります。'
                    'モデル再ロード・設定変更・自動再推論はしていません。')
            if choice.get('finish_reason') is not None:
                finish = choice['finish_reason']
        now = time.monotonic()
        if on_progress is not None and now - last_notice >= 1.0:
            on_progress('ローカルAIが生成中：JSON本文 '+str(chars)+
                        '文字／思考用出力 '+str(reasoning_chars)+'文字（SOURCE未検証）')
            last_notice = now
    _raise_if_cancelled(cancel_event)
    if not saw_done or finish is None:
        raise ModelTransportError('ローカルAIのストリームが完了前に切断されました。追加推論はしません')
    return {'choices': [{'message': {'content': ''.join(content)},
                         'finish_reason': finish}], 'usage': usage or {},
            '_studio_stream_metrics': {'jsonBodyChars': chars,
                                       'reasoningChars': reasoning_chars}}


def _model_response(url: str, payload: dict, timeout: int, on_progress=None,
                    cancel_event=None) -> dict:
    # No model request is sent after a cancellation (even through direct calls).
    _raise_if_cancelled(cancel_event)
    req = urllib.request.Request(url,
        json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'MABStudio/0.3'})
    streaming = bool(payload.get('stream'))
    idle_timeout = MODEL_STREAM_IDLE_TIMEOUT if streaming else timeout
    watcher = _ModelRequestWatch(
        cancel_event, total_seconds=MODEL_STREAM_TOTAL_TIMEOUT if streaming else None)
    prior = getattr(_MODEL_REQUEST_STATE, 'watcher', None)
    _MODEL_REQUEST_STATE.watcher = watcher
    watcher.start()
    try:
        # Never retry a read on the same HTTPResponse after a socket timeout:
        # Python's buffered socket file object may then be permanently poisoned.
        with LOCAL_HTTP.open(req, timeout=idle_timeout) as res:
            sock = getattr(getattr(getattr(res, 'fp', None), 'raw', None), '_sock', None)
            if sock is not None:
                watcher.track(sock)
            watcher.assert_not_interrupted()
            if streaming and 'text/event-stream' in res.headers.get('Content-Type', '').lower():
                # Only apply the thought-only watchdog to Gemma 4 structured
                # SOURCE requests. Other models' reasoning is not constrained.
                model_id = str(payload.get('model', ''))
                gemma4 = bool(re.search(r'gemma[._/ -]*4(?:[._/ -]|$)', model_id, re.I))
                thought_limit = (GEMMA4_THOUGHT_ONLY_CHAR_LIMIT
                                 if gemma4 and payload.get('response_format', {}).get('type') == 'json_schema'
                                 else None)
                result = _read_lm_studio_stream(
                    res, time.monotonic(), on_progress, cancel_event,
                    thought_only_limit=thought_limit)
            else:
                # read1() may silently accept a clean-looking JSON prefix even
                # if the server closes before its declared Content-Length.
                # read(amt) can ALSO return a short body without raising, so
                # explicitly check that HTTPResponse consumed its framing.
                raw = res.read(MAX_RESPONSE + 1)
                watcher.assert_not_interrupted()
                if getattr(res, 'length', None) not in (None, 0):
                    raise ModelTransportError('ローカルAIのHTTP本文が途中で切断されました。SOURCEは採用しません')
                if len(raw) > MAX_RESPONSE:
                    raise StudioError('LLMの応答が大きすぎます')
                try:
                    result = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise StudioError('ローカルLLMサーバーが不正なJSON応答を返しました') from exc
                if not isinstance(result, dict):
                    raise StudioError('ローカルLLMサーバーの応答形式が不正です')
            watcher.assert_not_interrupted()
            return result
    except urllib.error.HTTPError:
        watcher.assert_not_interrupted()
        raise
    except http.client.IncompleteRead as exc:
        watcher.assert_not_interrupted()
        raise ModelTransportError('ローカルAIのHTTP本文が途中で切断されました。SOURCEは採用しません') from exc
    except (OSError, TimeoutError) as exc:
        watcher.assert_not_interrupted()
        raise ModelTransportError(
            'ローカルAI通信が中断されました。LM Studioの推論状態を確認してください。'
            '同じ要求の自動再送は行いません: ' + str(exc)[:150]) from exc
    except StudioError:
        watcher.assert_not_interrupted()
        raise
    finally:
        watcher.stop()
        if prior is None:
            del _MODEL_REQUEST_STATE.watcher
        else:
            _MODEL_REQUEST_STATE.watcher = prior

def _local_model_json(path: str, *, method: str = 'GET', payload: dict | None = None,
                      timeout: float = 10.0, provider: str = 'ローカルAI') -> dict:
    """Small, bounded localhost-only model-management requests; never follow redirects."""
    base = LM_STUDIO_BASE if provider == 'LM Studio' else OLLAMA_BASE
    if provider not in ('LM Studio', 'Ollama') or not path.startswith('/') or '//' in path:
        raise StudioError('モデル管理の接続先が不正です')
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'MABStudio/0.3'})
    try:
        with LOCAL_HTTP.open(req, timeout=timeout) as res:
            raw = res.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise StudioError('モデル管理の応答が大きすぎます')
        result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if provider == 'LM Studio' and exc.code in (404, 405, 501):
            raise StudioError('LM Studioのモデル管理APIが使えません。0.4.0以降か確認してください。') from exc
        raise StudioError(f'{provider}のモデル操作がHTTP {exc.code}で拒否されました') from exc
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, UnicodeError) as exc:
        raise StudioError(f'{provider}のローカルサーバーとの通信に失敗しました: {str(exc)[:100]}') from exc
    if not isinstance(result, dict):
        raise StudioError('モデル管理の応答形式が不正です')
    return result


def _lm_instance_for_model(models: dict, model: str) -> str:
    """Select exactly one loaded instance by key or instance ID; never unload all."""
    items = models.get('models')
    if not isinstance(items, list):
        raise StudioError('LM Studioのロード情報を取得できません')
    selected = []
    for entry in items[:200]:
        if not isinstance(entry, dict):
            continue
        instances = entry.get('loaded_instances', [])
        if not isinstance(instances, list):
            raise StudioError('LM Studioのロード情報が不正です')
        for ins in instances[:100]:
            if not isinstance(ins, dict):
                continue
            instance_id = ins.get('id')
            if isinstance(instance_id, str) and (instance_id == model or entry.get('key') == model):
                selected.append(instance_id)
    selected = list(dict.fromkeys(selected))
    if not selected:
        raise StudioError('選択モデルは現在ロードされていません。アンロードは行いません')
    if len(selected) != 1:
        raise StudioError('同じモデルに複数のロード状態があります。誤終了防止のため操作を中止しました')
    return selected[0]


def _ollama_is_running(result: dict, model: str) -> bool:
    items = result.get('models')
    if not isinstance(items, list):
        raise StudioError('Ollamaの実行中モデル一覧を取得できません')
    return any(isinstance(item, dict) and
               (item.get('name') == model or item.get('model') == model)
               for item in items)


def verify_loaded_model(provider: str, model: str) -> dict:
    """Read-only preflight: an explicit request must not implicitly load another model.

    Ollama's /api/chat can auto-load an installed but idle model, so /api/tags
    is NOT an adequate readiness check. LM Studio's catalog may also contain
    downloaded but unloaded models. Only an exact, unique loaded selection is
    accepted. No inference, load/unload, or configuration endpoint is called.
    """
    if provider not in ('Ollama', 'LM Studio') or not isinstance(model, str) or (
            not model.strip() or model != model.strip() or len(model) > 180
            or any(ord(c) < 32 for c in model)):
        raise StudioError('選択したローカルAIモデルが不正です。推論を開始しません')
    if provider == 'Ollama':
        running = _local_model_json('/api/ps', provider=provider, timeout=6.0)
        items = running.get('models')
        if not isinstance(items, list):
            raise StudioError('Ollamaのロード中モデル一覧を確認できません。推論を開始しません')
        matched = [item for item in items if isinstance(item, dict) and (
            item.get('name') == model or item.get('model') == model)]
        if len(matched) != 1:
            raise StudioError('選択したOllamaモデルが一意にロード済みではありません。'
                              'Studioは自動ロードせず、推論を開始しません')
        return {'provider': provider, 'selectedModel': model, 'inferenceModel': model,
                'loaded': True, 'verifiedBy': 'READ_ONLY_API_PS', 'worldWrites': 0}
    listing = _local_model_json('/api/v1/models', provider=provider, timeout=6.0)
    entries = listing.get('models')
    if not isinstance(entries, list):
        raise StudioError('LM Studioのロード済みモデル情報を確認できません。推論を開始しません')
    loaded = []
    for entry in entries[:200]:
        if not isinstance(entry, dict) or entry.get('type') != 'llm':
            continue
        instances = entry.get('loaded_instances')
        if not isinstance(instances, list):
            raise StudioError('LM Studioのロード状態が不正です。推論を開始しません')
        for instance in instances[:100]:
            if not isinstance(instance, dict):
                continue
            instance_id = instance.get('id')
            if isinstance(instance_id, str) and instance_id and (
                    instance_id == model or entry.get('key') == model):
                if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}', instance_id):
                    raise StudioError('LM Studioのロード済みインスタンスIDが不正です。推論を開始しません')
                loaded.append(instance_id)
    if len(set(loaded)) != 1 or len(loaded) != 1:
        raise StudioError('選択したLM Studioモデルが一意にロード済みではありません。'
                          'Studioはモデルを切り替えず、推論を開始しません')
    return {'provider': provider, 'selectedModel': model,
            'loadedInstance': loaded[0], 'inferenceModel': loaded[0], 'loaded': True,
            'verifiedBy': 'READ_ONLY_API_V1_MODELS', 'worldWrites': 0}


def unload_local_model(provider: str, model: str) -> dict:
    """Explicitly unload only a currently running selected model, with no inference.

    Does not unload all, stop servers, change memory settings or touch Minecraft.
    Confirmation/verification is local and does not re-trigger an inference.
    """
    if provider not in ('Ollama', 'LM Studio'):
        raise StudioError('モデルの提供元が不正です')
    if not isinstance(model, str) or not model.strip() or len(model) > 180 or any(ord(c) < 32 for c in model):
        raise StudioError('アンロードするモデル名を選択してください')
    model = model.strip()
    if provider == 'LM Studio':
        before = _local_model_json('/api/v1/models', provider=provider)
        instance_id = _lm_instance_for_model(before, model)
        reply = _local_model_json('/api/v1/models/unload', method='POST',
                                   payload={'instance_id': instance_id},
                                   provider=provider)
        if reply.get('instance_id') != instance_id:
            raise StudioError('LM Studioの応答とアンロード対象が一致しません。状態を確認してください')
        after = _local_model_json('/api/v1/models', provider=provider)
        try:
            _lm_instance_for_model(after, model)
        except StudioError as exc:
            if 'ロードされていません' in str(exc):
                return {'provider': provider, 'model': model, 'instance_id': instance_id,
                        'unloaded': True, 'worldWrites': 0}
            raise
        raise StudioError('アンロード後も対象モデルがロード中と報告されました。LM Studioを確認してください')
    before = _local_model_json('/api/ps', provider=provider)
    if not _ollama_is_running(before, model):
        raise StudioError('選択モデルは現在ロードされていません。アンロードは行いません')
    reply = _local_model_json('/api/generate', method='POST',
                              payload={'model': model, 'keep_alive': 0, 'stream': False},
                              provider=provider, timeout=15.0)
    if reply.get('model') != model or reply.get('done') is not True or reply.get('done_reason') != 'unload':
        raise StudioError('Ollamaからアンロード完了を確認できません。実行中モデルを確認してください')
    after = _local_model_json('/api/ps', provider=provider)
    if _ollama_is_running(after, model):
        raise StudioError('Ollamaが対象モデルをまだ実行中と報告しています')
    return {'provider': provider, 'model': model, 'unloaded': True, 'worldWrites': 0}


def llm_models(provider: str, timeout: int = 5) -> list[str]:
    if provider not in ("Ollama", "LM Studio"):
        raise StudioError("ローカルLLMの種類が不正です")
    url = OLLAMA_BASE + "/api/tags" if provider == "Ollama" else LM_STUDIO_BASE + "/v1/models"
    with LOCAL_HTTP.open(url, timeout=timeout) as res:
        data = json.loads(res.read(MAX_RESPONSE))
    if provider == "Ollama":
        return [x["name"] for x in data.get("models", []) if isinstance(x.get("name"), str)]
    return [x["id"] for x in data.get("data", []) if isinstance(x.get("id"), str)]


def _ollama_qwen3_thinking_can_be_disabled(model: str) -> bool:
    """Only opt known Qwen3-family IDs into Ollama's supported think=False.

    Do not send this setting to unrelated models: support for disabling
    thinking varies by model. This is a request option, not a server change.
    """
    return bool(re.search(r'(?<![A-Za-z0-9])qwen3(?=$|[.:/_-])', model, re.I))


def _compact_revision_reference(design: dict) -> str:
    """Preserve a large B143 draft's *entire* element list in a short AI reference.

    This is input context, never a SOURCE export, compiler, change proposal or
    saved format. For unexpected fields, use the original lossless JSON rather
    than hide material or geometry from the model. The exact design object is
    retained and the verified delta merger still runs against that original.
    """
    original = json.dumps(design, ensure_ascii=False, separators=(',', ':'))
    elements = design.get('elements')
    if not isinstance(elements, list) or len(elements) <= 32:
        return original
    shapes = {
        'box': ('b', ('origin', 'size')),
        'ellipsoid': ('e', ('center', 'radii')),
        'cylinder': ('c', ('origin', 'axis', 'radius', 'length')),
    }
    if set(design) != {'schema', 'id', 'title', 'brief', 'bounds', 'elements', 'designOnly'}:
        return original
    lines = []
    for element in elements:
        if not isinstance(element, dict) or element.get('shape') not in shapes:
            return original
        shape, fields = shapes[element['shape']]
        allowed = {'id', 'shape', 'mode', 'material', 'shell', *fields}
        if set(element) - allowed or element.get('mode') not in ('add', 'cut'):
            return original
        if not isinstance(element.get('id'), str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,39}', element['id']):
            return original
        mode = '+' if element['mode'] == 'add' else '-'
        material = element.get('material', '_')
        if (mode == '+' and material not in B143_JSON_SCHEMA['properties']['elements']['items']['properties']['material']['enum']
                or mode == '-' and 'material' in element):
            return original
        parts = [element['id'], shape, mode, material]
        for field in fields:
            value = element.get(field)
            if isinstance(value, list) and len(value) == 3 and all(type(n) is int for n in value):
                parts.append(','.join(map(str, value)))
            elif field == 'axis' and value in ('x', 'y', 'z'):
                parts.append(value)
            elif field in ('radius', 'length') and type(value) is int:
                parts.append(str(value))
            else:
                return original
        if 'shell' in element:
            if type(element['shell']) is not int:
                return original
            parts.append('shell='+str(element['shell']))
        lines.append('|'.join(parts))
    # SOURCE array order and original element IDs are preserved in every row.
    # No original content is mutated or replaced by this representation.
    return ('AI向け大規模SOURCE参照一覧（要素省略なし。B143保存原本ではない）:\n'
            '形状 b=box(origin,size), e=ellipsoid(center,radii), '
            'c=cylinder(origin,axis,radius,length)。\n'
            '列はID|形状|操作(+ add / - cut)|材質(_ はcut)|形状別の座標と寸法'
            '|shell=n(記載がある場合のみ)。座標と寸法の3数はX,Y,Z順。'
            '各行は原SOURCEの要素順、IDは完全一致。\n'
            'ID='+json.dumps(design['id'], ensure_ascii=False)+'; '
            'title='+json.dumps(design['title'], ensure_ascii=False)+'; '
            'brief='+json.dumps(design['brief'], ensure_ascii=False)+'; '
            'designOnly=true; bounds='+json.dumps(design['bounds'], ensure_ascii=False, separators=(',', ':'))+'\n'
            + '\n'.join(lines))


def ask_llm(provider: str, model: str, user_prompt: str, error: str = "",
            previous_design: dict | None = None, timeout: int = 180,
            revision_base: dict | None = None, on_progress=None,
            on_metadata=None, cancel_event=None, revision_mode: str = 'full') -> dict:
    _raise_if_cancelled(cancel_event)
    if provider not in ("Ollama", "LM Studio"):
        raise StudioError("ローカルLLMの接続先が不正です")
    if not model or len(model) > 180 or not user_prompt.strip() or len(user_prompt) > 2000:
        raise StudioError("モデル名または指示文を確認してください")
    if revision_mode not in ('full', 'delta') or (revision_mode == 'delta' and revision_base is None):
        raise StudioError('修正モードが不正です')
    task = user_prompt
    if revision_base is not None:
        if not isinstance(revision_base, dict) or revision_base.get('schema') != SCHEMA:
            raise StudioError('修正元のMAB B143設計が正しくありません')
        prior = json.dumps(revision_base, ensure_ascii=False, separators=(',', ':'))
        if len(prior) > 64000:
            raise StudioError('修正元の設計が長すぎます。全文をAIへ渡せません')
        dims = revision_base.get('bounds', {})
        if not isinstance(dims, dict):
            dims = {}
        original_size = (f"X={dims.get('width')}, Y={dims.get('height')}, "
                         f"Z={dims.get('depth')}")
        envelope = ('【座標の重要条件】boundsのwidth/height/depthはXYZのセル数で、'
                    '有効座標はそれぞれ0～(寸法-1)。boxはorigin+size-1、'
                    'ellipsoidはcenter±radii、cylinderは長手方向origin+length-1と'
                    '垂直方向origin±radiusの全域がbounds内に必要。'
                    '既存boundsは' + original_size + '。追加・変更後の全要素が入るように'
                    'boundsを整合させること（width/depth≤128、height≤96、体積≤500000）。'
                    '不要な既存要素の削除・縮小で隠さず、指定変更と既存建築を保持する。\n')
        if revision_mode == 'delta':
            task = ('【既存の確定設計を軽量修正】元のB143 JSON全体を書き直さず、'
                    '変更した要素だけを返す。ワールドには実行しない。\n'
                    'JSON形式: {"schema":"mab-studio-b143-revision-delta-v1",'
                    '"replace":[],"add":[],"removeIds":[]}。'
                    'replaceには既存IDの変更後の完全な要素だけ、addには新規要素だけ、'
                    'removeIdsには削除する既存IDだけを書く。'
                    '変更のない既存要素は一切書かない。'
                    '建物の大きさが必要に応じて変わる場合だけboundsを完全な寸法オブジェクトで指定。'
                    'title/briefを変える場合だけ指定。'
                    '修正後の全要素がboundsに収まるようにする。'
                    'Studioは幾何形状を自動修理しない。\n'
                    + envelope + '既存設計: ' + _compact_revision_reference(revision_base) + '\n修正指示: ' + user_prompt)
        else:
            task = ('【既存の確定設計を修正】以下のB143 JSONを保ち、変更の必要な部分だけ修正し、'
                    '省略のないB143 JSON全体を返してください。Minecraftに実行しないこと。\n'
                    + envelope + '既存設計: ' + prior + '\n修正指示: ' + user_prompt)
    if error:
        task += "\n\n前回のSOURCE検証エラー: " + error[-500:]
        if previous_design is not None:
            # Supply the exact invalid design, not only an error string.
            # The model can correct it without regenerating unrelated details.
            prior = json.dumps(previous_design, ensure_ascii=False, separators=(",", ":"))
            task += "\n修正する前回の設計JSON: " + prior[:12000]
        if revision_mode == 'delta':
            task += "\n元の建築意図を保ち、指定された差分JSONだけを返してください。"
        else:
            task += "\n元の建築意図を保ち、エラーだけ直した完全なJSONを返してください。"
    # A compact FIRST proposal must never shrink a previously approved design.
    # Revision candidates can preserve all source elements plus bounded additions;
    # B143 remains the only source/voxel validation authority.
    max_elements = MAX_MODEL_ELEMENTS
    system_prompt = SYSTEM_PROMPT
    if revision_base is not None:
        existing = revision_base.get('elements')
        if not isinstance(existing, list) or not 1 <= len(existing) <= 160:
            raise StudioError('修正元の要素数がB143の範囲外です')
        max_elements = min(160, max(MAX_MODEL_ELEMENTS, len(existing) + 8))
        # A revision must not inherit first-proposal shortening limits:
        # the entire approved B143 source must remain representable.
        system_prompt = COMMON_SYSTEM_PROMPT + (
            f'【修正専用】既存の全要素・title・briefを保持できる最大{max_elements}要素。\n'
            '修正指示に必要な箇所以外のSOURCEを保持し、省略なく完全なJSONを返す。'
        )
    generation_schema = (MODEL_GENERATION_SCHEMA if revision_base is None
                         else _shape_specific_output_schema(max_elements=max_elements,
                                                            compact=False))
    if revision_mode == 'delta':
        generation_schema = _revision_delta_output_schema(revision_base)
        system_prompt = ('あなたはB143の変更箇所だけを返すJSON編集器です。'
                         '指定された要素の変更・追加・削除だけを行い、元SOURCE全文を繰り返さない。'
                         '変更のない要素は保持されます。B143に適合する座標を返し、'
                         '解説・Markdown・施工命令は出力しない。')
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": task}]
    output_budget, context_length = _output_budget(provider, model, messages)
    if provider == 'Ollama':
        url = OLLAMA_BASE + '/api/chat'
        payload = {'model': model, 'messages': messages, 'stream': False,
                   'format': generation_schema,
                   'options': {'temperature': 0.3, 'num_predict': output_budget}}
        if _ollama_qwen3_thinking_can_be_disabled(model):
            # Ollama native /api/chat: Qwen3 supports an explicit non-thinking
            # answer. Keep the strict JSON Schema and B143 validation unchanged.
            payload['think'] = False
    else:
        url = LM_STUDIO_BASE + '/v1/chat/completions'
        payload = {'model': model, 'messages': messages, 'temperature': 0.3,
                   'max_tokens': output_budget, 'stream': True,
                   'response_format': {
                       'type': 'json_schema',
                       'json_schema': {'name': ('mab_b143_delta' if revision_mode == 'delta'
                                             else 'mab_b143_design'),
                                       'schema': generation_schema},
                   }}
        if (re.search(r'qwen[.\/_-]?3[.]?8', model, re.I) or
                re.search(r'gemma[._/ -]*4(?:[._/ -]|$)', model, re.I)):
            # Best-effort, per-request only. LM Studio builds are known to
            # ignore this for Gemma 4; the thought-only watchdog is separate.
            payload['chat_template_kwargs'] = {'enable_thinking': False}
    try:
        reply = _model_response(url, payload, timeout, on_progress, cancel_event)
    except urllib.error.HTTPError as exc:
        _raise_if_cancelled(cancel_event)
        # HTTP 400 can also mean an unknown model, insufficient resources, or
        # invalid prompt. Never double-infer just because the status is 400.
        try:
            message = exc.read(4096).decode('utf-8', 'replace').lower()
        except (OSError, UnicodeError):
            message = ''
        structured_rejected = exc.code in (400, 422) and any(
            token in message for token in ('response_format', 'json_schema',
                                            'structured output', 'unsupported format',
                                            'invalid format', 'grammar'))
        if not structured_rejected:
            raise ModelTransportError(f'{provider}からHTTP {exc.code}を受信しました。モデル/サーバーを確認してください') from exc
        # This server rejected our JSON-Schema contract. Sending a second POST
        # would be an unapproved automatic retry and may trigger expensive
        # inference or a different, less strictly constrained response.
        raise ModelTransportError(
            f'{provider}が構造化JSON Schemaを拒否しました (HTTP {exc.code})。'
            '自動再試行・別形式への切替は行いません。') from exc
    _raise_if_cancelled(cancel_event)
    if provider == 'Ollama':
        if reply.get('done_reason') == 'length':
            raise _truncated_error(provider, reply, output_budget, context_length)
        # A syntactically valid JSON body is not proof that inference finished.
        # Ollama's non-streaming /api/chat replies explicitly report done=true.
        if reply.get('done') is not True or reply.get('done_reason') not in (None, 'stop'):
            raise ModelTransportError('Ollamaが生成完了を確認できない応答を返しました。SOURCEは保存しません')
        message = reply.get('message')
        content = message.get('content', '') if isinstance(message, dict) else ''
    else:
        choices = reply.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise StudioError('LM Studioの回答候補がありません')
        finish_reason = choices[0].get('finish_reason')
        if finish_reason == 'length':
            raise _truncated_error(provider, reply, output_budget, context_length)
        if finish_reason != 'stop':
            raise ModelTransportError('LM Studioの回答は正常終了していません（終了理由: '+str(finish_reason)[:40]+'）。SOURCEは保存しません')
        message = choices[0].get('message')
        content = message.get('content', '') if isinstance(message, dict) else ''
    if not isinstance(content, str) or not content.strip():
        if provider == 'Ollama' and isinstance(message, dict) and message.get('thinking'):
            raise ModelOutputLimitError(
                'Ollamaが思考用出力だけを返し、設計JSON本文が空でした。'
                'SOURCEは未検証です。選択モデルのThinking設定を確認してください。'
                '同じ条件での自動再推論は行いません。')
        raise StudioError('AIから設計JSONの本文を受け取れませんでした。モデルのJSON出力対応を確認してください')
    draft = parse_llm_json(content)
    _raise_if_cancelled(cancel_event)
    if revision_mode == 'delta':
        draft = apply_revision_delta(revision_base, draft)
    # Model servers may accept JSON Schema but still produce invalid display
    # metadata. Do not spend another costly inference fixing a missing title.
    # Only text labels are repaired; the real B143 compiler checks everything.
    repaired, receipt = repair_generated_metadata(draft, user_prompt, revision_base)
    if receipt['fields']:
        if on_metadata is not None:
            on_metadata(receipt)
        if on_progress is not None:
            on_progress('AI設計のタイトル/説明のみB143形式へ調整しました。'
                        '建物形状は変更していません（SOURCE未検証）')
    return repaired
