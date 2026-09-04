#!/usr/bin/env python3
"""Capture BOSS job-list responses through a real Chrome CDP session.

The browser performs the search itself; this utility only observes the
page's Network responses and reads the job-list response body.  In particular
it prefers ``salaryDesc`` from the API, which is not affected by the font
obfuscation used by the rendered DOM.

It deliberately does not accept cookies/passwords, inject XHR/fetch calls, or
persist page HTML.  Use a dedicated Chrome profile and keep request volume
small (the default is one search page).
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    import websocket
except ImportError:  # pragma: no cover - exercised by the CLI error path
    websocket = None

API_PATH = "/wapi/zpgeek/search/joblist.json"
DEFAULT_PORT = 9222
MAX_JOBS = 300


def map_job(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a BOSS API item to a stable, non-sensitive record."""
    job_id = str(raw.get("encryptJobId") or "").strip()
    brand_id = str(raw.get("encryptBrandId") or "").strip()
    location = "·".join(
        str(raw.get(key) or "").strip()
        for key in ("cityName", "areaDistrict", "businessDistrict")
        if raw.get(key)
    )
    return {
        "job_id": job_id,
        "title": str(raw.get("jobName") or "").strip(),
        "salary": str(raw.get("salaryDesc") or "").strip(),
        "salary_source": "api" if raw.get("salaryDesc") else "api_empty",
        "location": location,
        "experience": str(raw.get("jobExperience") or "").strip(),
        "degree": str(raw.get("jobDegree") or "").strip(),
        "company": str(raw.get("brandName") or "").strip(),
        "company_scale": str(raw.get("brandScaleName") or "").strip(),
        "company_stage": str(raw.get("brandStageName") or "").strip(),
        "industry": str(raw.get("brandIndustry") or "").strip(),
        "skills": " | ".join(str(x).strip() for x in (raw.get("skills") or []) if str(x).strip()),
        "labels": " | ".join(str(x).strip() for x in (raw.get("jobLabels") or []) if str(x).strip()),
        "job_url": f"https://www.zhipin.com/job_detail/{job_id}.html" if job_id else "",
        "company_url": f"https://www.zhipin.com/gongsi/{brand_id}.html" if brand_id else "",
    }


def map_payload(payload: Any) -> list[dict[str, Any]]:
    """Extract and deduplicate jobs from common BOSS response shapes."""
    if isinstance(payload, dict):
        candidates: Any = payload.get("zpData", {}).get("jobList", [])
        if not candidates:
            candidates = payload.get("data", {}).get("jobList", [])
        if not candidates:
            candidates = payload.get("jobList", [])
    else:
        candidates = payload
    if not isinstance(candidates, list):
        return []
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        row = map_job(item)
        key = row["job_id"] or f"{row['title']}|{row['company']}|{row['location']}"
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output[:MAX_JOBS]


class CDP:
    def __init__(self, ws_url: str, timeout: float = 25):
        if websocket is None:
            raise RuntimeError("缺少 websocket-client，请安装 scripts/requirements-boss-cdp.txt")
        self.ws = websocket.create_connection(ws_url, timeout=timeout)
        self.seq = 0

    def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.seq += 1
        ident = self.seq
        self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        while True:
            event = json.loads(self.ws.recv())
            if event.get("id") == ident:
                if "error" in event:
                    raise RuntimeError(f"CDP {method} failed: {event['error'].get('message', 'unknown')}")
                return event.get("result", {})

    def close(self) -> None:
        self.ws.close()


def cdp_page(port: int) -> dict[str, Any]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as response:
        pages = json.load(response)
    for page in pages:
        if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
            return page
    raise RuntimeError("CDP 中没有可用的页面")


def search_url(keyword: str, city: str, page: int = 1) -> str:
    query = urllib.parse.urlencode({"query": keyword, "city": city, "page": page})
    return f"https://www.zhipin.com/web/geek/job?{query}"


def capture(keyword: str, city: str, port: int = DEFAULT_PORT, wait: float = 12) -> list[dict[str, Any]]:
    """Navigate a real page and passively capture its first job-list response."""
    page = cdp_page(port)
    cdp = CDP(page["webSocketDebuggerUrl"])
    try:
        cdp.command("Network.enable")
        cdp.command("Page.enable")
        cdp.command("Page.navigate", {"url": search_url(keyword, city)})
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            event = json.loads(cdp.ws.recv())
            if event.get("method") != "Network.responseReceived":
                continue
            response = event.get("params", {}).get("response", {})
            url = str(response.get("url") or "")
            if API_PATH not in url or int(response.get("status", 0)) != 200:
                continue
            request_id = event["params"].get("requestId")
            if not request_id:
                continue
            body = cdp.command("Network.getResponseBody", {"requestId": request_id}).get("body", "")
            try:
                return map_payload(json.loads(body))
            except (json.JSONDecodeError, TypeError):
                continue
        raise TimeoutError("等待 BOSS 职位列表响应超时；请确认 Chrome 已登录且页面未触发风控")
    finally:
        cdp.close()


def write_output(path: str, jobs: list[dict[str, Any]], keyword: str, city: str) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"keyword": keyword, "city": city, "source": "boss-cdp", "jobs": jobs}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = target.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(jobs[0]) if jobs else ["job_id", "title", "salary"])
        writer.writeheader()
        writer.writerows(jobs)


def main() -> int:
    parser = argparse.ArgumentParser(description="通过真实 Chrome CDP 被动采集 BOSS 职位 API")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--city", required=True, help="BOSS 城市码或城市名；城市名按当前页面解析")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--wait", type=float, default=12)
    parser.add_argument("--output", default="./data/boss/jobs.json")
    args = parser.parse_args()
    jobs = capture(args.keyword, args.city, args.cdp_port, args.wait)
    write_output(args.output, jobs, args.keyword, args.city)
    print(f"已捕获 {len(jobs)} 条职位；salary 来源为 API salaryDesc（未做 DOM 薪资解密）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
