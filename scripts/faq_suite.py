"""A customer-phrased suite, seeded from the site's own FAQs.

The generated suite is written *from the corpus*: it asks in the corpus's
words, so it cannot see a customer whose words differ, and it passed all four
of the questions that failed in front of a customer on 2026-09-03. The
field-test suite saw them, and it is 109 cases written by hand.

Every FAQ question on every product page is a question a real customer asked,
in their own words, that the insurer thought worth answering. The corpus holds
them — `raw/faq/*.md`, one `## question` per pair — and the compiler already
maps each file to its product through the product's `/faq` page `authority`.
That is a few hundred cases, free, shaped like the traffic.

The expectation is deliberately loose: the answer must be delivered and must
cite the product the FAQ belongs to. It does not pin the FAQ's own wording —
the bot answers from wordings and product pages as readily as from the FAQ,
and a better answer from a different page is an improvement, not a failure.
A case that fails here is a case where a real customer's question, about the
product they named, got either a handoff or another product.

    uv run python scripts/faq_suite.py --bundle okf-real --out evals/suites/faq-customer.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / p) for p in ("packages/okf", "packages/harness")]

from okf import Bundle, PageType  # noqa: E402

_QUESTION_RE = re.compile(r"^## (.+?)\s*$", re.M)
#: Questions that are not about the product: app mechanics, account admin,
#: and the site's own navigation. They belong to a different bot.
_SKIP_RE = re.compile(
    r"\b(app store|google play|download the app|reset my password|log ?in|otp|singpass|browser|cookie)\b",
    re.I,
)
#: A heading that is not a question at all.
_NOT_A_QUESTION_RE = re.compile(r"^(published faqs|frequently asked questions|faq)\b", re.I)


def _questions(path: Path) -> list[str]:
    text = path.read_text(errors="replace")
    out: list[str] = []
    for match in _QUESTION_RE.finditer(text):
        q = " ".join(match.group(1).split())
        if _NOT_A_QUESTION_RE.match(q) or _SKIP_RE.search(q) or len(q.split()) < 3:
            continue
        # The site's numbering — "3. How do you define a child?" — is not
        # something a customer types.
        q = re.sub(r"^\d+[.)]\s*", "", q)
        out.append(q)
    return out


def build(bundle_root: Path) -> list[dict[str, object]]:
    bundle = Bundle.load(bundle_root)
    cases: list[dict[str, object]] = []
    seen: set[str] = set()
    for page in sorted(bundle.pages.values(), key=lambda p: p.id):
        if page.frontmatter.type != PageType.product or not page.id.endswith("/faq"):
            continue
        product_page = bundle.get(page.id.rsplit("/", 1)[0])
        if product_page is None:
            continue
        key = bundle.product_key(product_page)
        fm = product_page.frontmatter
        names = [fm.title.lower()] + [a.lower() for a in fm.aliases if len(a.split()) >= 2]
        for ref in page.frontmatter.authority:
            path = bundle_root / ref
            if not path.exists() or not ref.startswith("raw/faq/"):
                continue
            for n, question in enumerate(_questions(path), start=1):
                norm = " ".join(question.lower().split())
                if norm in seen:
                    continue
                seen.add(norm)
                case: dict[str, object] = {"id": f"faq-{key}-{n:02d}"}
                if any(name in norm for name in names):
                    case["question"] = question
                else:
                    # "How can I benefit from this plan?" names no product on
                    # its own; on the site it sits under the product's
                    # heading. A customer asks it the same way — after naming
                    # the product — so the case is that conversation, and the
                    # expectation applies to the last turn.
                    case["turns"] = [fm.title, question]
                case["expect"] = {"cite_product": key, "delivered": True}
                cases.append(case)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", type=Path, default=Path("okf-real"))
    parser.add_argument("--out", type=Path, default=Path("evals/suites/faq-customer.yaml"))
    args = parser.parse_args()
    cases = build(args.bundle)
    header = (
        "# Customer-phrased suite, generated from the site's own FAQs by\n"
        "# scripts/faq_suite.py. Do not edit by hand; regenerate after a compile.\n"
        "#\n"
        "# Every question here was asked by a real customer, in their words, about\n"
        "# a product they named. The expectation is that the answer is delivered\n"
        "# and cites that product — nothing about the wording, since a better answer\n"
        "# from a wording page is an improvement over the FAQ's own text.\n"
        "#\n"
        f"# {len(cases)} cases from {args.bundle}\n\n"
    )
    args.out.write_text(header + yaml.safe_dump(cases, sort_keys=False, allow_unicode=True, width=1000))
    products = len({c["expect"]["cite_product"] for c in cases})  # type: ignore[index]
    print(f"wrote {len(cases)} cases across {products} products to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
