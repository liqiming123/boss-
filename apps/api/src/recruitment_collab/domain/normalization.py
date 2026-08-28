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

