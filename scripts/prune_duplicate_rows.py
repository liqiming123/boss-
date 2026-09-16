"""Remove candidate rows that duplicate the same person on the same BOSS account.

The duplicate rows were created before the job-label canonicalisation fix: BOSS
renders the first chat line in the same text run as the job title, that text was
part of the candidate's identity key, and every distinct chat text produced
another row for one candidate (张雨庭 ×4, 程丹丽 ×2).

Deletion is deliberately narrow. A row is only removed when it is in the same
(account, candidate name, identity signature) group as another row AND has the
same canonical job label AND carries no engagement and no event — so the row that
holds the business history is never the one that goes. The Feishu record is
deleted first; a failure there leaves the database untouched.

    python scripts/prune_duplicate_rows.py            # dry run
    python scripts/prune_duplicate_rows.py --apply    # delete
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

# The server keeps its secrets in an EnvironmentFile that systemd injects; a
# plain shell has none of them. Read that file here — before the package is
# imported, because importing it builds the database engine — and keep JSON
# values such as CORS_ORIGINS intact (shell `source` would strip their quotes).
ENV_FILE = Path(
    os.environ.get(
        "RECRUITMENT_ENV_FILE", "/home/ubuntu/apps/recruitment-collab/shared/.env"
    )
)
if ENV_FILE.is_file():
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from recruitment_collab.application.collaboration import (  # noqa: E402
    _canonical_job_name,
    _job_label_extends,
)
from recruitment_collab.config.settings import get_settings  # noqa: E402
from recruitment_collab.domain.normalization import JobNameNormalizer  # noqa: E402
from recruitment_collab.infrastructure.bitable import BitableSyncClient  # noqa: E402

# Business references that make a row undeletable. Every foreign key pointing at
# recruitment_candidate_sources is listed here except the sync queue below.
REFERENCING = (
    ("recruitment_engagements", "candidate_source_id"),
    ("recruitment_events", "candidate_source_id"),
    ("recruitment_interviews", "candidate_source_id"),
    ("recruitment_conflicts", "left_candidate_source_id"),
    ("recruitment_conflicts", "right_candidate_source_id"),
    ("recruitment_conflict_exclusions", "left_candidate_source_id"),
    ("recruitment_conflict_exclusions", "right_candidate_source_id"),
)
# The pending-Feishu queue entry is operational: it belongs to the row and goes
# with it.
DEPENDENT = (("recruitment_candidate_sync_outbox", "candidate_source_id"),)

QUERY = """
SELECT id::text, company_id::text, platform_account_id::text, candidate_display_name,
       candidate_normalized_name, coalesce(candidate_identity_signature, ''),
       raw_job_name, coalesce(feishu_record_id, ''), created_at,
       (SELECT count(*) FROM recruitment_engagements e WHERE e.candidate_source_id = cs.id),
       (SELECT count(*) FROM recruitment_events v WHERE v.candidate_source_id = cs.id)
FROM recruitment_candidate_sources cs
ORDER BY candidate_normalized_name, platform_account_id, created_at
"""

# The table the system actually writes to is the ACTIVE row here, not the
# FEISHU_BITABLE_* environment defaults (those may point at a retired table).
ACTIVE_TABLES = """
SELECT company_id::text, app_token, candidate_table_id
FROM recruitment_feishu_bitable_configs
WHERE status = 'ACTIVE'
"""


def duplicates(rows: list[dict]) -> list[tuple[dict, list[dict]]]:
    """Return (keeper, redundant) pairs, most-referenced row kept."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[
            (row["account"], row["normalized_name"], row["signature"])
        ].append(row)
    found = []
    for items in groups.values():
        if len(items) < 2:
            continue
        canonical = {
            item["id"]: JobNameNormalizer().normalize(_canonical_job_name(item["job"]))
            for item in items
        }
        duplicates_of = [
            item
            for item in items
            if any(
                other["id"] != item["id"]
                and (
                    canonical[other["id"]] == canonical[item["id"]]
                    or _job_label_extends(canonical[other["id"]], canonical[item["id"]])
                )
                for other in items
            )
        ]
        if not duplicates_of:
            continue
        # The row holding the history wins; ties go to the earlier row.
        keeper = max(
            duplicates_of,
            key=lambda item: (item["engagements"] + item["events"], -item["index"]),
        )
        redundant = [
            item
            for item in duplicates_of
            if item["id"] != keeper["id"] and not item["engagements"] and not item["events"]
        ]
        if redundant:
            found.append((keeper, redundant))
    return found


def references(connection, source_id: str) -> dict[str, int]:
    counts = {}
    for table, column in REFERENCING:
        count = connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :id"),
            {"id": source_id},
        ).scalar()
        if count:
            counts[f"{table}.{column}"] = count
    return counts


def main() -> int:
    apply = "--apply" in sys.argv
    only = sys.argv[sys.argv.index("--name") + 1] if "--name" in sys.argv else None
    settings = get_settings()
    if settings.feishu_mode != "real":
        raise SystemExit("refusing to run: the server is not in real Feishu mode")
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        rows = [
            dict(zip(
                ("id", "company", "account", "name", "normalized_name", "signature", "job",
                 "feishu", "created_at", "engagements", "events"),
                record,
                strict=True,
            ))
            for record in connection.execute(text(QUERY)).all()
        ]
        tables = {
            company: (app_token, table_id)
            for company, app_token, table_id in connection.execute(text(ACTIVE_TABLES)).all()
        }
    if not tables:
        raise SystemExit("no ACTIVE Feishu table configured; refusing to run")
    clients = {
        company: BitableSyncClient(settings, app_token=app_token, candidate_table_id=table_id)
        for company, (app_token, table_id) in tables.items()
    }
    for index, row in enumerate(rows):
        row["index"] = index

    plan = duplicates(rows)
    if only:
        plan = [entry for entry in plan if entry[0]["name"] == only]
    if not plan:
        print("no duplicate rows found")
        return 0

    blocked = False
    for keeper, redundant in plan:
        print(f"\n{keeper['name']}  (account {keeper['account'][:8]})")
        print(f"  KEEP    {keeper['job'][:56]!r} 跟进={keeper['engagements']} 事件={keeper['events']}")
        for item in redundant:
            with engine.connect() as connection:
                refs = references(connection, item["id"])
            if refs:
                blocked = True
                print(f"  BLOCKED {item['job'][:56]!r} 仍被引用: {refs}")
                continue
            print(f"  DELETE  {item['job'][:56]!r} 跟进=0 事件=0 飞书={'有' if item['feishu'] else '无'}")

    if not apply:
        print("\n(dry run — pass --apply to delete)")
        return 0

    deleted = 0
    for keeper, redundant in plan:
        for item in redundant:
            with engine.connect() as connection:
                if references(connection, item["id"]):
                    print(f"skip {item['id']}: still referenced")
                    continue
            if item["feishu"]:
                client = clients.get(item["company"])
                if client is None:
                    print(f"skip {item['id']}: no ACTIVE Feishu table for its company")
                    continue
                client.delete_candidate(item["feishu"])
            with engine.begin() as connection:
                for table, column in DEPENDENT:
                    connection.execute(
                        text(f"DELETE FROM {table} WHERE {column} = :id"),
                        {"id": item["id"]},
                    )
                connection.execute(
                    text("DELETE FROM recruitment_candidate_sources WHERE id = :id"),
                    {"id": item["id"]},
                )
            deleted += 1
            print(f"deleted {item['id']}  {item['job'][:48]!r}")
    print(f"\nremoved {deleted} duplicate row(s)")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
