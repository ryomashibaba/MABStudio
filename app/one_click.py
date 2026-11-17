"""One-click, unprivileged Windows startup for MAB Studio v0.3.

Only installs the already approved free VTK/Pillow into a private Python venv.
Never launches a model, changes machine-wide Python, reads a Minecraft world,
or runs the real B143 or Education construction command. Runs bounded VTK QA
once per installed runtime, then opens the existing Studio GUI directly.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import contextlib
import venv

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / '.venv-native'
LOCAL = ROOT / '.studio-startup'
REPORT = LOCAL / 'last-startup.json'
SELF_CHECK = LOCAL / 'native-selfcheck.json'
LOG = LOCAL / 'last-startup.log'
REQUIREMENTS = ('vtk==9.6.2', 'Pillow>=10,<13')
SCHEMA = 'mab-studio-one-click-v1'
NATIVE_CHECK_TIMEOUT = 100
SETUP_TIMEOUT = 480


def private_python(runtime: Path = RUNTIME, *, windows: bool = True) -> Path:
    return runtime / ('Scripts/python.exe' if windows else 'bin/python')


def gui_python(runtime: Path = RUNTIME) -> Path:
    return runtime / 'Scripts/pythonw.exe'


def runtime_fingerprint(py: Path) -> str:
    """Invalidate first-run QA when bundled code or dependency pins change."""
    import hashlib
    key = b'|'.join((str(py).encode('utf-8'),
                    ','.join(REQUIREMENTS).encode('ascii'),
                    (ROOT/'app/native_viewer.py').read_bytes(),
                    (ROOT/'app/native_selfcheck.py').read_bytes()))
    return hashlib.sha256(key).hexdigest()


def valid_check_receipt(report: dict, fingerprint: str) -> bool:
    return (report.get('result') == 'PASS_NATIVE_READONLY_SELF_CHECK'
            and report.get('runtimeFingerprint') == fingerprint
            and report.get('source') == 'SYNTHETIC_IN_MEMORY'
            and report.get('worldWrites') == 0
            and report.get('worldContacts') == 0)


def _read_json(path: Path) -> dict:
    try:
        x = json.loads(path.read_text(encoding='utf-8'))
        return x if isinstance(x, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _append_log(message: str) -> None:
    LOCAL.mkdir(parents=True, exist_ok=True)
    # Keep only the latest startup's bounded diagnostics. No model prompt,
    # credentials, Minecraft saves, or whole environment variables are logged.
    with LOG.open('a', encoding='utf-8') as fh:
        fh.write(message.rstrip()[:1000]+'\n')


def _command(command: list[str], *, timeout: int, log_output: bool = False) -> int:
    """Run a fixed command without shell; limit runtime and log size."""
    try:
        completed = subprocess.run(command, cwd=ROOT, timeout=timeout,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace',
                                   check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _append_log(type(exc).__name__)
        return 125
    if log_output and completed.stdout:
        # A package installer sometimes prints full download URLs or local
        # paths; user-local log is not sent or uploaded anywhere.
        _append_log(completed.stdout[-900:])
    return completed.returncode


def _alert(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning('MAB Studio 起動診断', message, parent=root)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def _dependency_probe_script() -> str:
    """Require known-compatible native 3D, never silently accept another VTK."""
    return ("import sys, PIL, vtkmodules.vtkRenderingOpenGL2; "
            "from vtkmodules.vtkCommonCore import vtkVersion; "
            "assert sys.version_info[:2] == (3,13); "
            "assert vtkVersion.GetVTKVersion() == '9.6.2'; "
            "assert 10 <= int(PIL.__version__.split('.')[0]) < 13")


@contextlib.contextmanager
def _single_setup_lock():
    """Make double-clicking safe: only one Windows dependency install at once."""
    if os.name != 'nt':
        yield True
        return
    import msvcrt
    LOCAL.mkdir(parents=True, exist_ok=True)
    path = LOCAL / 'launch.lock'
    with path.open('a+b') as handle:
        handle.seek(0)
        handle.write(b'0')
        handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _check_python() -> None:
    if os.name != 'nt' or sys.version_info[:2] != (3, 13) or platform.architecture()[0] != '64bit':
        raise RuntimeError('Windows x64 と Python 3.13 (64-bit) が必要です')


def _prepare_runtime() -> tuple[Path, dict]:
    _check_python()
    py = private_python(RUNTIME)
    if not py.is_file():
        _append_log('Creating isolated venv (never changes system Python)')
        venv.EnvBuilder(with_pip=True).create(str(RUNTIME))
    if not py.is_file():
        raise RuntimeError('PRIVATE_PYTHON_NOT_CREATED')
    probe = _command([str(py), '-c', _dependency_probe_script()], timeout=45)
    if probe:
        _append_log('Installing approved native 3D libraries into private venv')
        code = _command([str(py), '-m', 'pip', 'install',
                         '--disable-pip-version-check', '--no-input',
                         *REQUIREMENTS], timeout=SETUP_TIMEOUT, log_output=True)
        if code:
            raise RuntimeError('NATIVE_DEPENDENCY_INSTALL_FAILED')
        probe = _command([str(py), '-c', _dependency_probe_script()], timeout=60)
        if probe:
            raise RuntimeError('NATIVE_DEPENDENCY_IMPORT_FAILED')
    finger = runtime_fingerprint(py)
    cached = _read_json(SELF_CHECK)
    if not valid_check_receipt(cached, finger):
        _append_log('Running bounded native render QA (synthetic scene only)')
        code = _command([str(py), '-m', 'app.native_selfcheck', '--report', str(SELF_CHECK)],
                        timeout=NATIVE_CHECK_TIMEOUT, log_output=True)
        selfcheck = _read_json(SELF_CHECK)
        if code or selfcheck.get('result') != 'PASS_NATIVE_READONLY_SELF_CHECK':
            raise RuntimeError('NATIVE_RENDER_SELF_CHECK_FAILED')
        selfcheck['runtimeFingerprint'] = finger
        _write_json(SELF_CHECK, selfcheck)
        cached = selfcheck
    return py, cached


def _spawn_app(py: Path, *, native: bool) -> subprocess.Popen:
    env = os.environ.copy()
    env['MAB_STUDIO_NATIVE'] = '1' if native else '0'
    env['MAB_STUDIO_ONE_CLICK'] = '1'
    env['MAB_STUDIO_ONE_CLICK_FALLBACK'] = '0' if native else '1'
    # pythonw uses Tk directly and does not keep a command console open.
    system_gui = Path(sys.executable).with_name('pythonw.exe')
    exe = gui_python() if native else (system_gui if system_gui.is_file() else Path(sys.executable))
    if not exe.is_file():
        exe = py if native else Path(sys.executable)
    LOCAL.mkdir(parents=True, exist_ok=True)
    output = (LOCAL/'gui-errors.log').open('w', encoding='utf-8')
    try:
        process = subprocess.Popen([str(exe), '-m', 'app.main'],
                                   cwd=ROOT, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, close_fds=True)
    finally:
        output.close()
    return process


def _startup_result(native: bool, *, reason: str = '', check: dict | None = None) -> dict:
    return {'schema': SCHEMA, 'date': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'display': 'NATIVE_VTK' if native else 'LIGHTWEIGHT_FALLBACK',
            'nativeSelfCheck': (check or {}).get('result', 'NOT_RUN'),
            'reason': reason[:180], 'realModelCalls': 0,
            'minecraftWorldReads': 0, 'minecraftWorldWrites': 0,
            'windowsUserVisualQA': 'NOT_TESTED'}


def launch() -> int:
    with _single_setup_lock() as acquired:
        if not acquired:
            # An existing first-run bootstrap is in progress; no second pip.
            return 0
        return _launch_unlocked()


def _launch_unlocked() -> int:
    LOCAL.mkdir(parents=True, exist_ok=True)
    LOG.write_text('MAB Studio one-click launch (no world/model operations)\n', encoding='utf-8')
    native = False
    reason = ''
    check: dict = {}
    py = Path(sys.executable)
    try:
        py, check = _prepare_runtime()
        native = True
    except Exception as exc:
        reason = str(exc)[:180]
        _append_log('Native startup preparation failed: ' + type(exc).__name__ + ': ' + reason)
        # Never imply that the Tk fallback provides the native graphics quality.
        _append_log('Auto fallback: no user approval or additional操作 required')
    _write_json(REPORT, _startup_result(native, reason=reason, check=check))
    try:
        process = _spawn_app(py, native=native)
        # Catch immediate import errors without waiting for a long GUI session.
        try:
            exit_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return 0
        if exit_code != 0 and native:
            _append_log('Native GUI exited immediately; auto-fallback to lightweight')
            _write_json(REPORT, _startup_result(False, reason='NATIVE_GUI_EARLY_EXIT', check=check))
            _spawn_app(py, native=False)
            return 0
        return 0 if exit_code == 0 else 1
    except OSError as exc:
        _append_log('GUI launch error: ' + type(exc).__name__)
        _write_json(REPORT, _startup_result(False, reason='GUI_PROCESS_START_FAILED', check=check))
        _alert('MAB Studio の起動に失敗しました。診断ファイルは自動保存されています。')
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--report-only', action='store_true',
                    help='Pure read-only plan; do not install anything or start GUI')
    args = ap.parse_args(argv)
    if args.report_only:
        print(json.dumps({'schema': SCHEMA, 'windowsRequired': True,
                          'pythonRequired': '3.13-x64',
                          'dependencies': list(REQUIREMENTS),
                          'mode': 'NO_INSTALL_NO_LAUNCH',
                          'worldWrites': 0}, ensure_ascii=False))
        return 0
    return launch()


if __name__ == '__main__':
    sys.exit(main())
