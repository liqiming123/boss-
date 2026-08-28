import re

import pytest

from recruitment_collab.domain.enums import EngagementStage, MatchLevel, PlatformIdScope
from recruitment_collab.domain.normalization import CandidateNameNormalizer, JobNameNormalizer
from recruitment_collab.domain.services import CandidateDuplicateMatcher, ConflictKeyFactory, DomainError, EngagementStateMachine, MatchCandidate


def test_conservative_normalization():
    assert CandidateNameNormalizer().normalize("  张\u200b 女士  ") == "张 女士"
    assert JobNameNormalizer().normalize("Video， Editor") == "video, editor"
    assert CandidateNameNormalizer().normalize("李先生2") == "李先生2"


def test_conflict_key_is_order_independent():
    factory = ConflictKeyFactory()
    assert factory.create("c", "j", "张女士", "a", "b") == factory.create("c", "j", "张女士", "b", "a")
    assert re.fullmatch(r"[a-f0-9]{64}", factory.create("c", "j", "张女士", "a", "b"))


def test_match_levels_and_self_exclusion():
    current = MatchCandidate("s1", "r1", "小明", "j1", "视频", "pid", PlatformIdScope.COMPANY)
    others = [MatchCandidate("self", "r1", "小明", "j1", "视频", "pid", PlatformIdScope.COMPANY), MatchCandidate("s2", "r2", "小明", "j2", "其他", "pid", PlatformIdScope.GLOBAL), MatchCandidate("s3", "r3", "小明", "j1", "视频", None, PlatformIdScope.UNKNOWN)]
    matches = CandidateDuplicateMatcher().match(current, others)
    assert [item.match_level for item in matches] == [MatchLevel.CONFIRMED_PLATFORM_ID, MatchLevel.SUSPECTED_SAME_NAME_JOB]


def test_exclusion_and_state_machine():
    current = MatchCandidate("s1", "r1", "小明", "j1", "视频", None, PlatformIdScope.UNKNOWN)
    other = MatchCandidate("s2", "r2", "小明", "j1", "视频", None, PlatformIdScope.UNKNOWN)
    assert CandidateDuplicateMatcher().match(current, [other], [("s2", "s1")]) == []
    EngagementStateMachine().ensure_transition(EngagementStage.CLAIMED, EngagementStage.CONTACTED)
    with pytest.raises(DomainError): EngagementStateMachine().ensure_transition(EngagementStage.CLAIMED, EngagementStage.HIRED)

