"""Isolated, display-only review of an existing V11 R125 run.

The reviewed compiler is a data-only V11 SOURCE compiler. It is kept separate
from the fixed B143 project compiler and never makes a project or world write.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from app import core
from app.gpu_viewer import compact_display_data
from app.v11_run_import import read_run

REVIEW_COMPILERS = {
    # Commit and module bytes independently checked against the V11 handoff.
    "24ea8056f66fec986f887056ffa3129f583aa990":
        "477c4306689d9fa62e91cefb30fa2ca877e934479f824585c5f71c7fdb56455d",
}
REVIEW_MODULE = Path(__file__).with_name("v11_review_compiler.mjs")
BRIDGE_MODULE = Path(__file__).with_name("engine_bridge.mjs")
BRIDGE_SHA256 = "0842a249e045cbb05551a190104a4fe0255cbe57ff2950a85528fb0309652172"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _bounded_bytes(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("V11 review input missing or linked")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise ValueError("V11 review input size invalid")
    return raw


def read_review(directory, *, expected_model=None, node=None, bridge=None):
    """Recompile one captured V11 SOURCE with its exact reviewed V11 module.

    No producer commit is inferred from the directory name or RUN_REPORT. The
    returned provenance identifies the review compiler separately.
    """
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid V11 run directory")
    compiler_commit = "24ea8056f66fec986f887056ffa3129f583aa990"
    expected_module_sha = REVIEW_COMPILERS[compiler_commit]
    module_raw = _bounded_bytes(REVIEW_MODULE, 1024 * 1024)
    if hashlib.sha256(module_raw).hexdigest() != expected_module_sha:
        raise ValueError("V11 review compiler SHA mismatch")
    bridge_path = Path(bridge) if bridge is not None else BRIDGE_MODULE
    bridge_raw = _bounded_bytes(bridge_path, 128 * 1024)
    if hashlib.sha256(bridge_raw).hexdigest() != BRIDGE_SHA256:
        raise ValueError("Studio review bridge SHA mismatch")
    node_path = str(Path(node or core.node_binary()).resolve())

    original_source = None

    def load_design(path):
        nonlocal original_source
        original_source = _bounded_bytes(Path(path), 1024 * 1024)
        design = json.loads(original_source.decode("utf-8"))
        if (not isinstance(design, dict)
                or design.get("schema") != "minecraftaibuilder-b143-building-dsl-v1"
                or design.get("designOnly") is not True):
            raise ValueError("Invalid V11 SOURCE schema")
        return design

    def compile_design(_design):
        if not original_source:
            raise ValueError("V11 SOURCE bytes were not captured")
        with tempfile.TemporaryDirectory(prefix="studio-v11-review-") as tmp:
            temp_root = Path(tmp).resolve()
            engine_root = temp_root / "engine"
            module = engine_root / "source" / "ai" / "b143-building-dsl.mjs"
            module.parent.mkdir(parents=True)
            module.write_bytes(module_raw)
            bridge_file = temp_root / "bridge.mjs"
            bridge_file.write_bytes(bridge_raw)
            source_file = temp_root / "input.json"
            output_file = temp_root / "scene.json"
            source_file.write_bytes(original_source)
            env = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("NODE_")}
            command = [
                node_path, "--permission",
                "--allow-fs-read=" + str(temp_root),
                "--allow-fs-write=" + str(output_file),
                str(bridge_file), "compile", str(engine_root),
                str(source_file), str(output_file),
            ]
            try:
                completed = subprocess.run(
                    command, cwd=temp_root, env=env, capture_output=True,
                    timeout=60, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("V11 review compiler timed out") from exc
            if (completed.returncode != 0 or not output_file.is_file()
                    or output_file.stat().st_size > 16 * 1024 * 1024):
                reason = (completed.stderr or completed.stdout or b"").decode(
                    "utf-8", errors="replace"
                )[-300:]
                raise ValueError("V11 review compiler rejected SOURCE: " + reason)
            scene = json.loads(output_file.read_bytes().decode("utf-8"))
        if (not isinstance(scene, dict)
                or scene.get("designOnly") is not True
                or scene.get("readiness") != "NOT_PREPARED"
                or type(scene.get("worldContacts")) is not int
                or scene["worldContacts"] != 0
                or "worldCoordinates" not in scene
                or scene["worldCoordinates"] is not None
                or scene.get("stats", {}).get("constructionApproved") is not False
                or scene.get("BuildPlan", {}).get("operations") != []):
            raise ValueError("V11 review scene safety contract mismatch")
        display = compact_display_data(scene)
        if scene.get("stats", {}).get("voxels") != len(display["voxels"]):
            raise ValueError("V11 review voxel count mismatch")
        return scene

    result = read_run(
        root, load_design=load_design, compile_design=compile_design,
        expected_model=expected_model,
    )
    report = result["report"]
    if (not isinstance(report.get("sourceSha256"), str)
            or not _HEX64.fullmatch(report["sourceSha256"])
            or not isinstance(report.get("geometrySha256"), str)
            or not _HEX64.fullmatch(report["geometrySha256"])):
        raise ValueError("V11 review receipt hashes invalid")
    return {
        "reviewScene": result["scene"],
        "reportSha256": result["reportSha256"],
        "sourceFileSha256": result["sourceFileSha256"],
        "sourceSha256": report["sourceSha256"],
        "geometrySha256": report["geometrySha256"],
        "model": str(report.get("model", "V11"))[:80],
        "compilerCommit": compiler_commit,
        "compilerSha256": expected_module_sha,
        "producerCommitVerified": False,
        "status": "V11_REVIEW_ONLY",
        "formalB143Verified": False,
        "constructionApproved": False,
        "artAdopted": False,
    }
