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
import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from okf.bundle import Bundle
from okf.page import Page, PageType, Status

#: A one-word title is vocabulary, not identity: "Life" and "Travel" are both
#: product titles on the real bundle, and a customer who writes "travel" has
#: not named a product.
MIN_NAME_WORDS = 2
#: How close a run of the customer's words must be to a name to count as a
#: misspelling of it, and how long the run must be for that to be safe.
FUZZY_RATIO = 0.86
FUZZY_MIN_CHARS = 7
#: A category phrase is at least this long. "ci plan" is six characters and a
#: real category; anything shorter is noise.
MIN_FAMILY_CHARS = 6

_PUNCT_RE = re.compile(r"[^\w\s-]")


#: Words that name a line of business rather than a product in it. A phrase
#: made of one of everything else plus these is a category ("travel cover"),
#: not a name; two products can both carry it. Shared with the router, which
#: decides on the same basis whether to ask which product was meant.
GENERIC_WORDS = frozenset(
    {"insurance", "cover", "coverage", "plan", "policy", "protection", "the", "a", "an"}
)


def distinguishing(phrase: str) -> int:
    """How many of a phrase's words separate one product from its neighbours."""
    return sum(1 for w in phrase.split() if w not in GENERIC_WORDS)


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
        # Most distinguishing first, then longest. Length alone let a
        # category alias that happens to be longer shadow the product's
        # own name: "will tiq travel cover ..." matched "travel cover",
        # whose single head word reads as the whole travel line, and the
        # customer who named the product was asked which one they meant.
        index.names.sort(key=lambda n: (-distinguishing(n.phrase), -len(n.phrase)))
        return index

    def named(self, question: str) -> list[Name]:
        """Products the question names outright, longest phrase first.

        Exact first. Where nothing matches exactly, a near miss is accepted —
        "tiq travle", "home insurnace", "maid insurence" — when a run of the
        question's words is within `FUZZY_RATIO` of a name and long enough
        that a slip could not have made it another name. Marked `fuzzy` so
        the trace says the customer was read, not quoted.
        """
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
        # A title outranks another product's alias. "Does Business Owners
        # Super Suite include work injury compensation?" names the suite by
        # its title and Casualty Insurance by an alias that is really a
        # benefit; the customer typed one product's full name, and that is
        # the product. Two titles remain two products.
        if any(k.kind == "title" for k in kept):
            kept = [k for k in kept if k.kind == "title"]
        if kept:
            return kept
        return self._fuzzy(haystack)

    def _fuzzy(self, haystack: str) -> list[Name]:
        words = haystack.split()
        best: tuple[float, Name, str] | None = None
        for n in range(min(5, len(words)), 1, -1):
            for i in range(len(words) - n + 1):
                run = " ".join(words[i : i + n])
                if len(run) < FUZZY_MIN_CHARS:
                    continue
                for name in self.names:
                    if abs(len(name.phrase) - len(run)) > 3:
                        continue
                    ratio = difflib.SequenceMatcher(None, run, name.phrase).ratio()
                    # Word by word too: "crop insurance" is within a slip of
                    # "pet insurance" as a string and nowhere near it as words.
                    # Every word of the run needs a near-twin in the name.
                    if (
                        ratio >= FUZZY_RATIO
                        and _words_match(run, name.phrase)
                        and (best is None or ratio > best[0])
                    ):
                        best = (ratio, name, run)
        if best is None:
            return []
        # Two different products within reach of the same slip is a tie, not
        # a reading; the caller falls through to the model or asks. Measured
        # against the customer's run, not name against name: "maid insurance"
        # and "pmd insurance" are close to each other and only one is close
        # to "maid insurence".
        run = best[2]
        ties = {
            name.page_id
            for name in self.names
            if abs(len(name.phrase) - len(run)) <= 3
            and difflib.SequenceMatcher(None, run, name.phrase).ratio() >= FUZZY_RATIO
        }
        if len(ties) > 1:
            return []
        return [Name(best[1].phrase, best[1].page_id, best[1].key, "fuzzy")]

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


def _words_match(run: str, phrase: str) -> bool:
    words = phrase.split()
    return all(
        any(w == p or difflib.SequenceMatcher(None, w, p).ratio() >= 0.75 for p in words) for w in run.split()
    )


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


#: Words a customer puts around a plan's name — "the Savvy plan", "plan B",
#: "Enhanced Gold tier". None of them are part of the name.
_PLAN_WORD_RE = re.compile(r"\b(?:plan|tier|package|option)\b")


def plan_tiers_in(question: str, tiers: Sequence[str]) -> set[str]:
    """Every plan of this product's that the question names.

    Tiers are stored slugged — `plan-a`, `enhanced-gold` — and customers type
    them spaced and cased however they like: "plan B", "the Enhanced Gold",
    "savvy". Matching is on the slug with its separators relaxed, whole words
    only, so "entry" matches "the entry plan" and not "entrylevel".
    """
    text = normalise(question)
    if not text:
        return set()
    return {
        tier
        for tier in tiers
        if tier != "ALL"
        and re.search(r"\b" + r"[\s-]+".join(re.escape(w) for w in tier.split("-")) + r"\b", text)
    }


def plan_tier_in(question: str, tiers: Sequence[str]) -> str | None:
    """The one plan the question names, or none.

    None where the question names no plan, and none where it names more than
    one: a question about two plans is a comparison, and answering it with
    either one's figures would be picking a side the customer did not.
    """
    found = plan_tiers_in(question, tiers)
    return found.pop() if len(found) == 1 else None
