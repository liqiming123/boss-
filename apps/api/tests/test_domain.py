import re

from recruitment_collab.domain.normalization import CandidateNameNormalizer, JobNameNormalizer
from recruitment_collab.domain.services import candidate_source_identity, conflict_key


def test_conservative_normalization():
    assert CandidateNameNormalizer().normalize("  张\u200b 女士  ") == "张 女士"
    assert JobNameNormalizer().normalize("Video， Editor") == "video, editor"
    assert CandidateNameNormalizer().normalize("李先生2") == "李先生2"


def test_conflict_key_is_order_independent():
    assert conflict_key("c", "j", "张女士", "a", "b") == conflict_key("c", "j", "张女士", "b", "a")
    assert re.fullmatch(r"[a-f0-9]{64}", conflict_key("c", "j", "张女士", "a", "b"))


def test_platform_candidate_identity_keeps_jobs_separate():
    first = candidate_source_identity("boss", "account", "张女士", "岗位甲", "", "candidate-id")
    second = candidate_source_identity("boss", "account", "张女士", "岗位乙", "", "candidate-id")
    assert first != second
