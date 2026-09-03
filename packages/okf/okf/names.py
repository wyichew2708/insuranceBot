"""Every name a customer has seen for a product, and what a phrase names.

A product has one canonical title — the underwriter's — and any number of
names customers actually type: the shopfront's title ("Tiq Travel Insurance"),
the brand-and-category short form ("tiq travel"), the aliases the compiler
records. Recognising a name is the single most consequential reading of a
question: a customer who typed one has answered "which product" themselves,
and nothing downstream may overrule them.

Before this module, three places did that reading three different ways —
titles only in one, aliases in another, a brand word stripped in a third — and
"tiq travel" named nothing while "tiq travel covid" named the add-on. One
index, built once per bundle, is the only place a name is recognised now.

Two readings come out of it:

* **named** — the phrase is a title or alias of exactly the products it names,
  longest phrase first. "tiq travel covid" absorbs the "tiq travel" inside it;
  the customer typed the long form, and the flagship is not a second candidate.
  One product counts once however many of its names the customer used.

* **family** — the phrase names no product but sits inside two or more titles
  ("cancer insurance" inside "Major Cancer Insurance" and "Cancer Insurance
  with No Claim Discount"). That is a category, and one member is a guess.
  Where one member's title *is* the category, that member is the flagship and
  the family is answered by it, with the others mentioned.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field

from okf.bundle import Bundle
from okf.page import Page, PageType, Status

#: A one-word title is vocabulary, not identity: "Life" and "Travel" are both
#: product titles on the real bundle, and a customer who writes "travel" has
#: not named a product.
MIN_NAME_WORDS = 2
#: A category phrase is at least this long. "ci plan" is six characters and a
#: real category; anything shorter is noise.
MIN_FAMILY_CHARS = 6

_PUNCT_RE = re.compile(r"[^\w\s-]")


def normalise(text: str) -> str:
    return " ".join(_PUNCT_RE.sub(" ", text.lower()).split())


@dataclass(frozen=True)
class Name:
    phrase: str
    page_id: str
    key: str
    #: `title` or `alias` — what kind of name matched. Recorded on the Ask so a
    #: trace says how the product was identified.
    kind: str


@dataclass(frozen=True)
class Family:
    phrase: str
    #: Page ids, flagship first where there is one.
    members: tuple[str, ...]
    flagship: str | None = None


@dataclass
class ProductNameIndex:
    names: list[Name] = field(default_factory=list)
    #: page id → normalised title, for the family reading.
    titles: dict[str, str] = field(default_factory=dict)
    keys: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, bundle: Bundle) -> ProductNameIndex:
        index = cls()
        for page in bundle.pages.values():
            fm = page.frontmatter
            if fm.type != PageType.product or page.id.count("/") != 2:
                continue
            key = bundle.product_key(page)
            index.keys[page.id] = key
            title = normalise(fm.title)
            index.titles[page.id] = title
            if len(title.split()) >= MIN_NAME_WORDS:
                index.names.append(Name(title, page.id, key, "title"))
            for alias in fm.aliases:
                phrase = normalise(alias)
                if len(phrase.split()) >= MIN_NAME_WORDS and phrase != title:
                    index.names.append(Name(phrase, page.id, key, "alias"))
        index.names.sort(key=lambda n: -len(n.phrase))
        return index

    def named(self, question: str) -> list[Name]:
        """Products the question names outright, longest phrase first."""
        haystack = normalise(question)
        if not haystack:
            return []
        padded = f" {haystack} "
        kept: list[Name] = []
        for name in self.names:
            if f" {name.phrase} " not in padded:
                continue
            if any(name.page_id == k.page_id for k in kept):
                continue
            if any(name.phrase in k.phrase for k in kept):
                continue
            kept.append(name)
        return kept

    def family(
        self, question: str, approved_only: bool = True, bundle: Bundle | None = None
    ) -> Family | None:
        """The category the question names, where it names one and not a product."""
        if self.named(question):
            return None
        words = normalise(question).split()
        for n in range(len(words), 1, -1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i : i + n])
                if len(phrase) < MIN_FAMILY_CHARS:
                    continue
                members = [
                    page_id
                    for page_id, title in self.titles.items()
                    if f" {phrase} " in f" {title} "
                    and (not approved_only or bundle is None or _approved(bundle, page_id))
                ]
                if len(members) < 2:
                    continue
                flagship = next(
                    (
                        m
                        for m in members
                        if self.titles[m] in (phrase, f"{phrase} insurance", f"{phrase} plan")
                    ),
                    None,
                )
                ordered = sorted(members, key=lambda m: (m != flagship, -len(self.titles[m])))
                return Family(phrase=phrase, members=tuple(ordered), flagship=flagship)
        return None

    def bare(self, page_id: str, question: str, filler: frozenset[str]) -> bool:
        """The question is one of this product's names and nothing else."""
        words = normalise(question).split()
        if not words:
            return False
        joined = " ".join(words)
        candidates = sorted(
            [n.phrase for n in self.names if n.page_id == page_id] + [self.titles.get(page_id, "")],
            key=len,
            reverse=True,
        )
        for phrase in candidates:
            if phrase and f" {phrase} " in f" {joined} ":
                residue = f" {joined} ".replace(f" {phrase} ", " ", 1).split()
                return all(w in filler for w in residue)
        return False


def _approved(bundle: Bundle, page_id: str) -> bool:
    page = bundle.get(page_id)
    return page is not None and page.frontmatter.status == Status.approved


def index_for(bundle: Bundle) -> ProductNameIndex:
    """One index per bundle object, built on first use."""
    cached = getattr(bundle, "_name_index", None)
    if cached is None:
        cached = ProductNameIndex.build(bundle)
        # A frozen or slotted bundle just rebuilds next time.
        with contextlib.suppress(Exception):
            bundle._name_index = cached  # type: ignore[attr-defined]
    return cached


def names_of(page: Page) -> list[str]:
    """This page's own names, longest first — for readers that have a page and
    no bundle."""
    fm = page.frontmatter
    out = {normalise(fm.title)} | {normalise(a) for a in fm.aliases}
    return sorted((n for n in out if len(n.split()) >= MIN_NAME_WORDS), key=len, reverse=True)
