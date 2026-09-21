"""Compare published benefit rows without choosing a plan or inventing figures."""

from __future__ import annotations

import re

from harness import AnswerEnvelope, BudgetExhausted, Claim, Figure, GroundedAnswer, Trace
from harness.ask import Ask
from harness.contracts import ComparisonColumn, ComparisonRow, ComparisonTable, Link
from harness.intent import comparison_requested
from harness.trace import LoadedPage
from okf.names import index_for, normalise
from okf.tables import TableRow

from api.handlers.contracts import Evidence, Turn
from api.handlers.shared import CONTACT_LINK, _fail_closed, _finish, _refusal
from api.suggest import product_label, servable
from okf import Bundle, Page, landing_for


def _products(bundle: Bundle, question: str, ask: Ask) -> list[Page]:
    index = index_for(bundle)
    # Read each side independently so a title on one side cannot hide an alias
    # on the other. More than two distinct products remains a clarification.
    phrases = re.split(r"\b(?:with|and|versus|vs|between)\b", question, flags=re.I)
    ids = list(dict.fromkeys(hit.page_id for phrase in phrases for hit in index.named(phrase)))
    if not ids and ask.product_page:
        ids = [ask.product_page]
    return [p for pid in ids if (p := bundle.get(pid)) is not None]


def _named_tiers(bundle: Bundle, question: str, page: Page) -> list[str]:
    version = page.frontmatter.version_in_force or ""
    tiers = [t for t in bundle.tables.tiers_for(bundle.product_key(page), version) if t != "ALL"]
    text = f" {normalise(question)} "
    return [t for t in tiers if f" {normalise(t)} " in text]


def requested(bundle: Bundle, question: str, ask: Ask) -> bool:
    if not comparison_requested(question):
        return False
    products = _products(bundle, question, ask)
    if len(products) >= 2 or re.search(r"\b(?:compare|comparison|versus|vs)\b", question, re.I):
        return True
    return any(len(_named_tiers(bundle, question, p)) >= 2 for p in products)


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    turn.require(Evidence.wiki)
    products = _products(turn.bundle, turn.question, turn.ask)
    columns: list[tuple[Page, str]] = []
    if len(products) == 1:
        tiers = _named_tiers(turn.bundle, turn.question, products[0])
        if len(tiers) == 2:
            columns = [(products[0], t) for t in tiers]
    elif len(products) == 2:
        for product in products:
            tiers = _named_tiers(turn.bundle, turn.question, product)
            available = turn.bundle.tables.tiers_for(
                turn.bundle.product_key(product),
                product.frontmatter.version_in_force or "",
            )
            varying = [t for t in available if t != "ALL"]
            if len(tiers) > 1 or (varying and not tiers):
                break
            columns.append((product, tiers[0] if tiers else "ALL"))
    if len(columns) != 2:
        return _finish(
            turn.trace,
            GroundedAnswer(
                answer=(
                    "Which two products or plan tiers would you like to compare? "
                    "Please name both, and the product if you mean two tiers."
                ),
                clarifying=True,
                confidence=1.0,
            ),
            turn.bundle,
            turn.session,
            turn.question,
            turn.raw_root,
            [],
            ask=turn.ask,
        )
    pages: list[Page] = []
    for product, _ in columns:
        for page in (product, turn.bundle.get(f"{product.id}/exclusions")):
            if page is not None and page not in pages:
                if not servable(page, turn.session.today):
                    return _finish(
                        turn.trace,
                        GroundedAnswer(
                            answer=(
                                "The current approved pages do not support this comparison. "
                                "Our team can help check the published documents."
                            ),
                            handoff=True,
                            destinations=[CONTACT_LINK],
                            confidence=0.0,
                        ),
                        turn.bundle,
                        turn.session,
                        turn.question,
                        turn.raw_root,
                        [],
                        ask=turn.ask,
                    )
                pages.append(page)
    try:
        for page in pages:
            turn.budget.charge_page()
            turn.budget.check_clock()
            turn.trace.loaded.append(LoadedPage(page_id=page.id, title=page.frontmatter.title, via="compare"))
    except BudgetExhausted as exc:
        answer = GroundedAnswer(
            answer="I cannot complete this comparison within this turn. Our team can help.",
            handoff=True,
            destinations=[CONTACT_LINK],
            unresolved=[str(exc)],
        )
        turn.trace.budget = turn.budget.snapshot()
        turn.trace.delivered = False
        return AnswerEnvelope(answer=answer, delivered=False, trace_id=turn.trace.trace_id), turn.trace
    lookups: list[dict[tuple[str, str], TableRow]] = []
    labels: list[ComparisonColumn] = []
    missing: list[str] = []
    for product, tier in columns:
        key, version = turn.bundle.product_key(product), product.frontmatter.version_in_force or ""
        # Tier-specific rows override ALL, the same rule as BenefitTables.fetch.
        rows = [
            r
            for r in turn.bundle.tables.rows
            if r.product == key and r.version == version and r.tier in {"ALL", tier}
        ]
        rows.sort(key=lambda r: r.tier != "ALL")
        lookups.append({(r.benefit_code, r.attribute): r for r in rows})
        label = product_label(product) + (f" — {tier}" if tier != "ALL" else "")
        labels.append(ComparisonColumn(label=label, product_page=product.id, version=version, tier=tier))
        if not rows:
            missing.append(product_label(product))
    figures: list[Figure] = []
    table_rows: list[ComparisonRow] = []
    for benefit, attribute in sorted(set(lookups[0]) | set(lookups[1])):
        cells: list[Figure | None] = []
        for lookup in lookups:
            row = lookup.get((benefit, attribute))
            figure = (
                Figure(label=f"{benefit} {attribute}", text=row.rendered(), table_row_id=row.row_id)
                if row
                else None
            )
            cells.append(figure)
            if figure is not None:
                figures.append(figure)
        table_rows.append(
            ComparisonRow(
                benefit_code=benefit,
                attribute=attribute,
                label=f"{benefit.replace('_', ' ')} — {attribute.replace('_', ' ')}",
                cells=cells,
            )
        )
    table = ComparisonTable(columns=labels, rows=table_rows) if table_rows else None
    text = (
        "Here are the published benefit figures side by side. "
        "This is a factual comparison, not a recommendation."
    )
    if missing:
        text += " I do not have a published benefit table for " + " and ".join(missing) + "."
    if table:
        text += (
            " A blank cell means no matching published row was found; "
            "it does not mean the benefit is excluded."
        )
        for compared in table.rows:
            text += (
                "\n- "
                + compared.label
                + ": "
                + "; ".join(
                    f"{column.label}: {cell.text if cell else 'not published'}"
                    for column, cell in zip(table.columns, compared.cells, strict=True)
                )
            )
    links = [
        Link(label=product_label(p), url=url, desk="product")
        for p in products
        if (url := landing_for(p, turn.session.channel))
    ]
    answer = GroundedAnswer(
        answer=text,
        table=table,
        figures=figures,
        claims=[Claim(text=product_label(p), source_id=p.id, locator=p.id) for p in products],
        destinations=links,
        guidance=not bool(table),
        confidence=1.0,
        advice_flag=any(p.frontmatter.regulated_advice for p in products),
    )
    with turn.trace.stage("guardrail-output") as detail:
        outgoing = turn.guard.screen_output(
            turn.question,
            "\n".join([*(p.frontmatter.title for p in products), *(f.text for f in figures)]),
            answer.answer,
            [f.text for f in figures],
        )
        detail.update(risk=outgoing.risk.value, checked_by=outgoing.checked_by)
    if outgoing.blocked or _fail_closed(outgoing, turn.settings):
        return _refusal(turn.trace, outgoing, "guardrail-output", "comparison refused by output screening")
    try:
        turn.budget.charge_tokens((len(answer.model_dump_json()) + 3) // 4)
        turn.budget.check_clock()
    except BudgetExhausted as exc:
        turn.trace.budget = turn.budget.snapshot()
        turn.trace.delivered = False
        return AnswerEnvelope(
            answer=GroundedAnswer(
                answer="I cannot complete this comparison within this turn. Our team can help.",
                handoff=True,
                destinations=[CONTACT_LINK],
                unresolved=[str(exc)],
            ),
            delivered=False,
            trace_id=turn.trace.trace_id,
        ), turn.trace
    turn.trace.budget = turn.budget.snapshot()
    envelope, trace = _finish(
        turn.trace,
        answer,
        turn.bundle,
        turn.session,
        turn.question,
        turn.raw_root,
        [p.id for p in pages],
        ask=turn.ask,
    )
    envelope.gates.append(outgoing.as_gate("guardrail-output"))
    trace.gates = envelope.gates
    if not envelope.delivered:
        trace.blocked_draft = answer.answer
        envelope.answer = GroundedAnswer(
            answer="I cannot verify this comparison against the published documents. Our team can help.",
            handoff=True,
            destinations=[CONTACT_LINK],
            advice_flag=answer.advice_flag,
            unresolved=[g.detail for g in envelope.gates if g.blocking],
        )
        trace.answer = envelope.answer.model_dump(mode="json")
    return envelope, trace
