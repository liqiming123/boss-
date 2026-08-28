import json
from pathlib import Path

from recruitment_collab.main import app

target = Path("packages/api-client/openapi.json")
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {target}")
