import re
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from recruitment_collab.api.schemas import ContextResolveRequest
from recruitment_collab.domain.normalization import CandidateNameNormalizer, JobNameNormalizer
from recruitment_collab.domain.services import candidate_source_identity, conflict_key


def test_conservative_normalization():
    assert CandidateNameNormalizer().normalize("  张\u200b 女士  ") == "张 女士"
    assert JobNameNormalizer().normalize("Video， Editor") == "video, editor"
    assert CandidateNameNormalizer().normalize("李先生2") == "李先生2"


@pytest.mark.parametrize(
    "invalid_name",
    ["未填写工作经历", "有剧本吗", "测试时间有限制吗", "嗯嗯好的感谢"],
)
def test_boss_context_rejects_chat_sentences_as_candidate_names(invalid_name):
    with pytest.raises(ValidationError, match="BOSS_CANDIDATE_IDENTITY_UNCERTAIN"):
        ContextResolveRequest(
            platform="boss",
            page_url="https://www.zhipin.com/web/chat/index",
            candidate_display_name=invalid_name,
            candidate_age=27,
            candidate_experience="7年",
            candidate_education="大专",
            job_display_name="AI生成师(抽卡师)",
            account_display_name="成珈莉",
            observed_at=datetime.now(timezone.utc),
            client_event_id="candidate-test-1",
            extractor_version="boss-adapter-resilient-10",
        )


def test_conflict_key_is_order_independent():
    assert conflict_key("c", "j", "张女士", "a", "b") == conflict_key("c", "j", "张女士", "b", "a")
    assert re.fullmatch(r"[a-f0-9]{64}", conflict_key("c", "j", "张女士", "a", "b"))


def test_platform_candidate_identity_keeps_jobs_separate():
    first = candidate_source_identity("boss", "account", "张女士", "岗位甲", "", "candidate-id")
    second = candidate_source_identity("boss", "account", "张女士", "岗位乙", "", "candidate-id")
    assert first != second
