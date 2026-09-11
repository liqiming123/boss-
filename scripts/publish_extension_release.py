#!/usr/bin/env python3
"""Validate and publish the extension to the API's configured download paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

DEFAULT_PACKAGE = Path("artifacts/recruitment-collab-extension.zip")
DEFAULT_METADATA = Path("artifacts/extension-release.json")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False)
    temporary = Path(handle.name)
    handle.close()
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--target-package", type=Path, default=Path(os.environ.get("EXTENSION_PACKAGE_PATH", DEFAULT_PACKAGE)))
    parser.add_argument("--target-metadata", type=Path, default=Path(os.environ.get("EXTENSION_RELEASE_METADATA_PATH", DEFAULT_METADATA)))
    args = parser.parse_args()
    package, metadata = args.package.resolve(), args.metadata.resolve()
    if not package.is_file() or not metadata.is_file():
        raise SystemExit("extension package and metadata must exist")
    try:
        release = json.loads(metadata.read_text(encoding="utf-8"))
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"invalid extension release: {exc}") from exc
    package_hash, package_size = digest(package), package.stat().st_size
    if release.get("version") != manifest.get("version"):
        raise SystemExit("release metadata version does not match manifest version")
    if release.get("sha256") != package_hash or release.get("size_bytes") != package_size:
        raise SystemExit("release metadata hash or size does not match package")
    atomic_copy(package, args.target_package.resolve())
    atomic_copy(metadata, args.target_metadata.resolve())
    print(json.dumps({"version": release["version"], "package": str(args.target_package.resolve()), "metadata": str(args.target_metadata.resolve()), "sha256": package_hash, "size_bytes": package_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
