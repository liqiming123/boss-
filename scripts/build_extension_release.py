"""Package the built extension with matching version, digest and metadata."""
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
dist = root / "apps/extension/dist"
manifest = json.loads((dist / "manifest.json").read_text())
package = root / "artifacts/recruitment-collab-extension.zip"
with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(dist.rglob("*")):
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(dist).parts):
            archive.write(path, path.relative_to(dist))
metadata = {"version": manifest["version"], "published_at": datetime.now(timezone.utc).isoformat(),
    "sha256": hashlib.sha256(package.read_bytes()).hexdigest(), "file_name": package.name, "size_bytes": package.stat().st_size}
(root / "artifacts/extension-release.json").write_text(json.dumps(metadata, indent=2) + "\n")
print(json.dumps(metadata))
