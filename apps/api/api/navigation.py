"""The greeting's catalogue tree and service destinations, built from live metadata."""

from __future__ import annotations

import datetime as dt

from harness import Session
from harness.contracts import NavigationNode

from api.suggest import QUESTIONS, _available, product_label, servable
from okf import DESTINATIONS, Bundle, Desk, PageType, landing_for

TOPIC_LABELS = {
    "coverage": "What it covers",
    "exclusion": "What is not covered",
    "limit": "Cover limits",
    "eligibility": "Who can buy",
    "application": "How to buy",
    "claim": "How to claim",
    "documents": "Claim documents",
    "contact": "Contact the team",
}
TOPIC_ORDER = ("coverage", "exclusion", "limit", "eligibility", "application", "claim", "documents")


def destination(desk: Desk, label: str | None = None) -> NavigationNode:
    target = DESTINATIONS[desk]
    return NavigationNode(kind="destination", label=label or target.label, url=target.url)


def greeting_map(bundle: Bundle, session: Session) -> list[NavigationNode]:
    lines: dict[str, list[NavigationNode]] = {}
    claim_plans: list[NavigationNode] = []
    buy_plans: list[NavigationNode] = []
    products = sorted(
        (
            p
            for p in bundle.pages.values()
            if p.frontmatter.type is PageType.product and p.id.count("/") == 2 and servable(p, session.today)
        ),
        key=lambda p: (p.id.split("/")[1], p.frontmatter.title),
    )
    for product in products:
        name = product_label(product)
        available = _available(bundle, product, today=session.today)
        topics = [
            NavigationNode(
                kind="question", label=TOPIC_LABELS[topic], question=QUESTIONS[topic].format(product=name)
            )
            for topic in TOPIC_ORDER
            if topic in available
        ]
        lines.setdefault(product.id.split("/")[1], []).append(
            NavigationNode(kind="group", label=name, children=topics)
        )
        if "claim" in available:
            claim_plans.append(
                NavigationNode(kind="question", label=name, question=QUESTIONS["claim"].format(product=name))
            )
        url = landing_for(product, session.channel)
        if url:
            buy_plans.append(NavigationNode(kind="destination", label=name, url=url))
    return [
        NavigationNode(
            kind="group",
            label="Product information",
            children=[
                NavigationNode(kind="group", label=line.replace("-", " & ").title(), children=plans)
                for line, plans in lines.items()
            ],
        ),
        NavigationNode(kind="group", label="Claims", children=[destination(Desk.claims), *claim_plans]),
        NavigationNode(
            kind="group", label="My policy", children=[destination(Desk.portal), destination(Desk.renewal)]
        ),
        NavigationNode(
            kind="group",
            label="Buy and quote",
            children=[*buy_plans, destination(Desk.contact, "Ask an adviser")],
        ),
        NavigationNode(kind="group", label="Promotions", children=[destination(Desk.promotions)]),
        NavigationNode(kind="group", label="Help and contact", children=[destination(Desk.contact)]),
    ]


def starter_questions(bundle: Bundle, today: dt.date, limit: int = 4) -> list[str]:
    """Plain clients get actual catalogue questions, never hard-coded product names."""
    products = sorted(
        (
            p
            for p in bundle.pages.values()
            if p.frontmatter.type is PageType.product and p.id.count("/") == 2 and servable(p, today)
        ),
        key=lambda p: p.id,
    )
    # Spread the first hop across lines before offering a second plan in one.
    ordered = []
    seen: set[str] = set()
    for product in products:
        line = product.id.split("/")[1]
        if line not in seen:
            ordered.append(product)
            seen.add(line)
    ordered.extend(p for p in products if p not in ordered)
    return [QUESTIONS["coverage"].format(product=product_label(p)) for p in ordered[:limit]]
