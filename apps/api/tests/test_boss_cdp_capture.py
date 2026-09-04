from scripts.boss_cdp_capture import map_job, map_payload, search_url


def test_map_job_prefers_plain_api_salary_and_drops_unneeded_fields():
    row = map_job(
        {
            "encryptJobId": "job-1",
            "encryptBrandId": "brand-1",
            "jobName": "Python 工程师",
            "salaryDesc": "20-35K·13薪",
            "cityName": "上海",
            "areaDistrict": "浦东新区",
            "businessDistrict": "张江",
            "brandName": "示例公司",
            "skills": ["Python", "FastAPI"],
        }
    )
    assert row["salary"] == "20-35K·13薪"
    assert row["salary_source"] == "api"
    assert row["location"] == "上海·浦东新区·张江"
    assert row["job_url"].endswith("/job_detail/job-1.html")
    assert "cookie" not in row and "html" not in row


def test_map_payload_supports_zpdata_and_deduplicates():
    payload = {
        "zpData": {
            "jobList": [
                {"encryptJobId": "same", "jobName": "A", "salaryDesc": "10K"},
                {"encryptJobId": "same", "jobName": "A", "salaryDesc": "10K"},
            ]
        }
    }
    assert len(map_payload(payload)) == 1


def test_search_url_is_a_page_navigation_with_no_injected_request():
    url = search_url("AI Agent", "101020100")
    assert url.startswith("https://www.zhipin.com/web/geek/job?")
    assert "query=AI+Agent" in url
