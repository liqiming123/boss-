import hashlib
import re
import unicodedata

ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")
WHITESPACE = re.compile(r"\s+")
PUNCT_TRANSLATION = str.maketrans({"，": ",", "。": ".", "：": ":", "（": "(", "）": ")"})


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = ZERO_WIDTH.sub("", value)
    value = value.translate(PUNCT_TRANSLATION)
    return WHITESPACE.sub(" ", value).strip().lower()


class CandidateNameNormalizer:
    def normalize(self, value: str) -> str:
        return _normalize(value)


class JobNameNormalizer:
    def normalize(self, value: str) -> str:
        return _normalize(value)


EDUCATION_ALIASES = {
    "中专": "中专",
    "高中": "高中",
    "大专": "大专",
    "专科": "大专",
    "本科": "本科",
    "学士": "本科",
    "硕士": "硕士",
    "mba": "硕士",
    "博士": "博士",
}


def normalize_experience(value: str) -> str:
    normalized = _normalize(value).replace("工作经验", "").strip()
    cohort = re.fullmatch(r"(\d{2})\s*届", normalized)
    if cohort:
        return f"{int(cohort.group(1)):02d}届"
    if normalized in {"应届生", "应届", "在校生"}:
        return "应届生"
    if normalized in {"无经验", "经验不限", "不限"}:
        return normalized
    match = re.search(r"(\d+)\s*年", normalized)
    return f"{int(match.group(1))}年" if match else normalized


def normalize_education(value: str) -> str:
    normalized = _normalize(value)
    for alias, canonical in EDUCATION_ALIASES.items():
        if alias in normalized:
            return canonical
    return normalized


def candidate_identity_signature(name: str, age: int, experience: str, education: str) -> str:
    canonical = "|".join([CandidateNameNormalizer().normalize(name), str(age), normalize_experience(experience), normalize_education(education)])
    return hashlib.sha256(canonical.encode()).hexdigest()
