"""Conservative, self-reported tool inputs; these never establish eligibility.

Only explicit shapes are extracted. Unknown/ambiguous values stay unset. The
small destination vocabulary normalises names, not an insurer's covered areas.
Dates use ISO format; ambiguous local date formats are deliberately not guessed.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Authored country-name vocabulary, deliberately independent of service URLs.
# Expand alongside destination tests; absence does not mean excluded coverage.
DESTINATIONS = {
    "australia": "AU",
    "china": "CN",
    "hong kong": "HK",
    "india": "IN",
    "indonesia": "ID",
    "japan": "JP",
    "malaysia": "MY",
    "new zealand": "NZ",
    "philippines": "PH",
    "singapore": "SG",
    "south korea": "KR",
    "taiwan": "TW",
    "thailand": "TH",
    "vietnam": "VN",
    "united kingdom": "GB",
    "uk": "GB",
    "united states": "US",
    "usa": "US",
    "france": "FR",
    "germany": "DE",
    "italy": "IT",
    "spain": "ES",
}
NUMBERS = dict(
    zip(
        [
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ],
        range(1, 21),
        strict=True,
    )
)


class EntitySlots(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    age: int | None = Field(default=None, ge=0, le=120)
    destination: str | None = None
    trip_start: dt.date | None = None
    trip_end: dt.date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=366)
    tier: str | None = Field(default=None, max_length=100)
    sum_insured: Decimal | None = Field(default=None, gt=0, le=1_000_000_000, allow_inf_nan=False)
    currency: Literal["SGD", "USD", "MYR"] | None = None
    vehicle: str | None = Field(default=None, max_length=60)
    occupation: str | None = Field(default=None, max_length=60)


def restore_slots(value: Any) -> EntitySlots:
    """Old or malformed persisted state is never trusted as tool input."""
    try:
        slots = EntitySlots.model_validate(value)
    except (ValidationError, TypeError):
        return EntitySlots()
    if slots.destination is not None and slots.destination not in DESTINATIONS.values():
        return EntitySlots()
    return slots


def extract_slots(
    question: str, previous: EntitySlots | None = None, *, tiers: tuple[str, ...] = ()
) -> EntitySlots:
    """Merge explicit inputs, clearing conflicting/invalid updates instead of guessing.

    The caller resets `previous` on a product change. Tier candidates must come
    from that product's tables. Nothing here infers customer attributes from a
    coverage question (e.g. 'does this cover people aged 65?').
    """
    values = (previous or EntitySlots()).model_dump()
    text = question.lower()
    if re.search(r"\b(?:what if|suppose|if i|if my)\b", text):
        return previous or EntitySlots()
    if values["tier"] not in tiers:
        values["tier"] = None

    def capture(field: str, pattern: str, convert: Any = str) -> None:
        matches = re.findall(pattern, text)
        if not matches:
            return
        try:
            candidates = {convert(m) for m in matches}
            values[field] = candidates.pop() if len(candidates) == 1 else None
        except (ValueError, ArithmeticError):
            values[field] = None

    for slot, label in {
        "age": "age",
        "duration_days": "duration",
        "trip_start": "trip start",
        "trip_end": "trip end",
        "vehicle": "vehicle",
        "occupation": "occupation",
    }.items():
        if re.search(r"\b" + label + r"\s*[:=]", text):
            values[slot] = None
    capture("age", r"\b(?:i am|i'm|my age is|age\s*[:=])\s*(-?\d{1,3})(?!\d)", int)
    capture(
        "duration_days",
        r"\b(?:for|duration\s*[:=])\s+(\d+|" + "|".join(NUMBERS) + r")\s+days?\b",
        lambda n: NUMBERS[n] if n in NUMBERS else int(n),
    )
    capture(
        "trip_start", r"\b(?:from|starting|trip start\s*[:=])\s*(\d{4}-\d{2}-\d{2})\b", dt.date.fromisoformat
    )
    capture(
        "trip_end", r"\b(?:until|to|ending|trip end\s*[:=])\s*(\d{4}-\d{2}-\d{2})\b", dt.date.fromisoformat
    )
    names = "|".join(re.escape(n) for n in sorted(DESTINATIONS, key=len, reverse=True))
    countries = {DESTINATIONS[n] for n in re.findall(r"\b(?:" + names + r")\b", text)}
    destination_cue = re.search(r"\b(?:to |destination\s*[:=])", text)
    if countries and (destination_cue or text.strip(" .?!") in DESTINATIONS):
        values["destination"] = countries.pop() if len(countries) == 1 else None
    # Reject explicitly labelled unknown destinations rather than retaining an old one.
    elif re.search(r"\b(?:destination\s*[:=]|(?:travelling|traveling|going|flying) to )", text):
        values["destination"] = None
    if re.search(r"\bnot (?:travelling |traveling |going )?to\b", text):
        values["destination"] = None
    mentioned_tiers = {tier for tier in tiers if re.search(r"\b" + re.escape(tier) + r"\b", text)}
    if mentioned_tiers:
        values["tier"] = mentioned_tiers.pop() if len(mentioned_tiers) == 1 else None
    elif re.search(r"\btier\s*[:=]", text):
        values["tier"] = None
    amounts = re.findall(
        r"\bsum insured\s*(?:is|:|=)?\s*(sgd|usd|myr|s\$)?\s*"
        r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)(?![\d.,])",
        text,
    )
    if re.search(r"\bsum insured\b", text):
        values["sum_insured"] = None
        values["currency"] = None
    if len(set(amounts)) == 1:
        currency, amount = amounts[0]
        values["sum_insured"] = Decimal(amount.replace(",", ""))
        values["currency"] = "SGD" if currency == "s$" else currency.upper() if currency else None
    for field in ("vehicle", "occupation"):
        # Explicit labels only; no inferred classifications or unrestricted model text.
        capture(field, rf"\b{field}\s*[:=]\s*([a-z][a-z -]{{0,59}})(?=[,.;!?]|$)")
    # Validate each field independently, preserving good updates when another is bad.
    for name in values:
        try:
            EntitySlots.model_validate({name: values[name]})
        except ValidationError:
            values[name] = None
    start, end = values["trip_start"], values["trip_end"]
    if start and end:
        if end < start or (end - start).days >= 366:
            values["trip_end"] = None
            values["duration_days"] = None
        else:
            inclusive_days = (end - start).days + 1
            if values["duration_days"] not in (None, inclusive_days):
                # Conflicting duration/date information must be clarified by the tool handler.
                values["duration_days"] = None
            else:
                values["duration_days"] = inclusive_days
    return EntitySlots.model_validate(values)
