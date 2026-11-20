"""Collect upstream notices for dependencies shipped in the Windows native build."""
from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path
import re
import shutil
import sys


REQUIRED_DISTRIBUTIONS = ("vtk", "numpy", "Pillow", "matplotlib")


def collect_notices(bundle: Path) -> dict:
    bundle = Path(bundle).resolve()
    target = bundle / "THIRD_PARTY_LICENSES"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    copied: list[str] = []
    for name in REQUIRED_DISTRIBUTIONS:
        dist = metadata.distribution(name)
        project = re.sub(r"[^A-Za-z0-9._-]+", "_", dist.metadata.get("Name") or name)
        version = re.sub(r"[^A-Za-z0-9._-]+", "_", dist.version)
        entries = []
        for rel in dist.files or ():
            parts = tuple(str(part).lower() for part in Path(str(rel)).parts)
            basename = parts[-1] if parts else ""
            if ("licenses" in parts or basename.startswith(("license", "copying", "notice"))):
                src = Path(dist.locate_file(rel))
                if src.is_file():
                    entries.append((rel, src))
        if not entries:
            raise RuntimeError(f"UPSTREAM_LICENSE_FILES_MISSING:{name}")
        for rel, src in entries:
            # Distribution metadata may nest vendor notices under deep source
            # paths (for example NumPy's bundled libdivide tree). Preserve every
            # notice while keeping the Windows destination below MAX_PATH.
            relative_name = str(rel).replace("\\", "/")
            suffix = Path(relative_name)
            path_id = hashlib.sha256(relative_name.encode("utf-8")).hexdigest()[:12]
            stem = re.sub(r"[^A-Za-z0-9._-]+", "_", suffix.stem)[:32] or "license"
            extension = re.sub(r"[^A-Za-z0-9._-]+", "_", suffix.suffix)[:8]
            filename = f"{path_id}-{stem}{extension}"
            dst = target / f"{project}-{version}" / filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst.relative_to(bundle).as_posix())

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("PYTHON_LICENSE_FILE_MISSING")
    py_dst = target / f"Python-{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
    py_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_license, py_dst)
    copied.append(py_dst.relative_to(bundle).as_posix())

    embedded = {
        "Node.js": bundle / "NODE_LICENSE.txt",
        "TclTk": bundle / "_internal" / "_tk_data" / "license.terms",
        "socket-be": bundle / "_internal" / "app" / "education_runtime" / "node_modules" / "socket-be" / "LICENSE",
        "ws": bundle / "_internal" / "app" / "education_runtime" / "node_modules" / "ws" / "LICENSE",
    }
    for label, src in embedded.items():
        if not src.is_file():
            raise RuntimeError(f"EMBEDDED_LICENSE_FILE_MISSING:{label}")
        dst = target / "Bundled-Runtimes" / f"{label}-LICENSE{src.suffix or '.txt'}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst.relative_to(bundle).as_posix())
    return {"licenseFiles": len(copied), "directory": target.name}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m app.native_license DIST_MABSTUDIO_DIRECTORY")
    print(collect_notices(Path(sys.argv[1])))
