"""Deterministic gateway language detection for the English-only corpus.

This is a bounded lexicon, not a translator. No policy facts are translated
without an approved source in that language. Ambiguous Latin text stays English.
"""

from __future__ import annotations

import re
from typing import Literal

Language = Literal["en", "ms", "zh"]
_MALAY = frozenset(
    [
        "saya",
        "boleh",
        "apakah",
        "bagaimana",
        "berapa",
        "adakah",
        "insurans",
        "perlindungan",
        "tuntutan",
        "polisi",
        "mahu",
        "ingin",
        "untuk",
        "dengan",
        "tidak",
        "caruman",
        "bahasa",
        "melayu",
        "terima",
        "kasih",
    ]
)


def detect_language(question: str) -> Language:
    if len(re.findall(r"[\u3400-\u9fff]", question)) >= 2:
        return "zh"
    words = set(re.findall(r"[a-z]+", question.lower()))
    if len(words & _MALAY) >= 2 or {"bahasa", "melayu"} <= words:
        return "ms"
    return "en"


FALLBACK = {
    "ms": (
        "Dokumen produk yang diluluskan untuk pembantu ini kini hanya tersedia dalam bahasa Inggeris. "
        "Saya tidak dapat mengesahkan jawapan polisi dalam bahasa Melayu daripada sumber tersebut. "
        "Sila hubungi pasukan kami untuk bantuan dalam bahasa Melayu."
    ),
    "zh": (
        "本助手目前只有经过批准的英文产品文件。无法根据中文资料核实保单答案。请联系我们的团队寻求中文协助。"
    ),
}
CONTACT_LABEL = {"ms": "Hubungi pasukan kami", "zh": "联系我们"}
REFUSAL = {
    "ms": "Saya tidak dapat membantu dengan permintaan ini. Sila hubungi pasukan kami untuk bantuan.",
    "zh": "我无法协助处理此请求。请联系我们的团队寻求帮助。",
}
