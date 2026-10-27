"""Does the request text authorize deletion? (A1) Rules only; an LLM confirmation comes later.

Non-deletion wins when the text explicitly limits or refuses deletion; otherwise any
deletion verb (6 languages) authorizes it. Anything else is NOT_APPLICABLE.
"""
from __future__ import annotations

import re

NEGATIVE = [
    r"not asking for deletion", r"not a request to erase", r"do not close", r"don'?t delete",
    r"without removing", r"marketing lists only", r"leave (the|my) (account|profile)", r"keep the account as it is",
    r"continue using the service",
    # the same refusals in the dataset's other languages (FR, ES, PT, JA, KO)
    r"ne (demande|souhaite|veux) pas (la )?(suppression|supprim)", r"pas de suppression",
    r"no (quiero|deseo|solicito|pido) (eliminar|borrar|la eliminaci|el borrado)", r"no (eliminen|borren)",
    r"n[ãa]o (quero|desejo|solicito|pe[çc]o) (excluir|apagar|a exclus)", r"n[ãa]o (excluam|apaguem)",
    r"削除(は|を)?(希望しません|望みません|しないで|不要)",
    r"삭제(를|는)?\s*(원하지\s*않|하지\s*마|하지\s*말)",
]
POSITIVE = [
    r"\bdelet", r"\berase", r"\bremov", r"\bclose[ds]?\b", r"\bclosed\b",
    r"exclus[ãa]o", r"elimina", r"suppression", r"삭제", r"削除",
    r"profile is gone", r"cannot identify me",
]
NEG_RE = re.compile("|".join(NEGATIVE), re.I)
POS_RE = re.compile("|".join(POSITIVE), re.I)


def classify(text: str) -> tuple[bool, str]:
    """-> (authorizes_deletion, reason)"""
    m = NEG_RE.search(text)
    if m:
        return False, f"explicit non-deletion phrase {m.group(0)!r}"
    m = POS_RE.search(text)
    if m:
        return True, f"deletion phrase {m.group(0)!r}"
    return False, "no deletion phrase"
