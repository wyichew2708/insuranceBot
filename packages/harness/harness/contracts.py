"""Typed contracts — every step is a contract, not a prose instruction (§F.1).

The model emits `GroundedAnswer` under guided decoding. `unresolved` matters
more than it looks: an agent that can say "I could not establish the sub-limit
for this tier" degrades honestly instead of confabulating.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

# The distribution-channel taxonomy is domain knowledge, so it lives in okf and
# is re-exported here: a session binds to a route to market, never to a brand.
from okf.channels import Channel as Channel
from pydantic import BaseModel, ConfigDict, Field


class AuthLevel(str, Enum):
    anonymous = "L0"
    identified = "L1"
    authenticated = "L2"


class PolicyContext(BaseModel):
    """The customer's in-force policy, from the system of record — never from
    the wiki, which describes only what is currently sold (§B.2)."""

    policy_id: str
    product_id: str
    version: str
    tier: str
    inception: dt.date | None = None
    in_force: bool = True


class Session(BaseModel):
    session_id: str
    channel: Channel = Channel.unknown
    auth_level: AuthLevel = AuthLevel.anonymous
    policy: PolicyContext | None = None
    locale: str = "en-SG"
    today: dt.date = Field(default_factory=dt.date.today)


class Claim(BaseModel):
    """One factual assertion, bound to where it came from."""

    text: str
    source_id: str  # wiki page id, or a raw/ path
    locator: str | None = None


class Figure(BaseModel):
    """A number. Must bind to a benefit-tables row or an SOR field — the
    numeric-binding gate blocks anything else (§F.2)."""

    model_config = ConfigDict(frozen=True)

    label: str
    text: str
    table_row_id: str | None = None
    sor_field: str | None = None
    # Promotions are effective-dated and have no benefit-table row; a figure
    # lifted verbatim from an in-window promotion page binds here instead.
    page_ref: str | None = None
    # A figure quoted verbatim from a policy document, bound to the raw
    # locator it was transcribed from (`raw/wordings/x.md#p7`). A contract's
    # numbers cannot become benefit-table rows — a notice period is not a
    # benefit — and cannot be paraphrased away without changing what was
    # agreed. Transcription is the third option, and it is checkable: the
    # numeric-binding gate re-reads the document and looks for the figure.
    quote_ref: str | None = None

    @property
    def is_bound(self) -> bool:
        return bool(self.table_row_id or self.sor_field or self.page_ref or self.quote_ref)


class ChannelRender(BaseModel):
    """Deep links resolved deterministically from session.channel (§C.4).

    There is no brand decision to make — every route sells the same Etiqa
    products. What varies is who the customer deals with and where they buy.
    A channel may expose several interchangeable surfaces (the direct channel
    answers on both etiqa.com.sg and tiq.com.sg); `landing` is the primary one
    and `surfaces` carries the rest, all equally valid to cite.
    """

    channel: Channel
    name: str | None = None
    purchase: str | None = None
    intermediary: str | None = None
    landing: str | None = None
    hotline: str | None = None
    surfaces: list[str] = Field(default_factory=list)
    # Channel unknown: every route offered rather than one guessed.
    all_routes_shown: bool = False


class Verdict(str, Enum):
    pass_ = "pass"
    fail = "fail"
    skip = "skip"


class GateResult(BaseModel):
    gate: str
    verdict: Verdict
    detail: str = ""
    #: Page ids that would let this gate pass, where the gate knows them.
    #: Typed rather than parsed back out of `detail`: a caller that repairs a
    #: failure should not have to read English to find out what was wanted.
    #: Loading these is necessary and never sufficient — the answer has to be
    #: *recomposed* in their presence and re-gated, or the check goes vacuous.
    missing: list[str] = Field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.verdict == Verdict.fail


class GroundedAnswer(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)
    channel_render: ChannelRender | None = None
    advice_flag: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    unresolved: list[str] = Field(default_factory=list)
    # Set when the answer is a refusal/handoff rather than a factual reply;
    # the coverage-assertion gates do not apply to it.
    handoff: bool = False
    # Set when the turn was a pleasantry — a greeting, thanks, a farewell, or
    # "what can you do" — and the reply is conversational rather than factual.
    # Distinct from `handoff`: nothing is being passed to a colleague, and
    # telling a customer who said "hi" that we are passing them on is a worse
    # answer than the greeting they expected. Like a handoff it carries no
    # claims, so the provenance gates have nothing to check and skip.
    smalltalk: bool = False


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session: Session
    #: The customer's own earlier turns, oldest first. Supplied by the client
    #: rather than held server-side, so the service stays stateless and a turn
    #: is reproducible from its own request. Used only to restore a subject to
    #: a turn that names none — "what's the coverages" after "term life" — and
    #: ignored entirely by a turn that stands on its own.
    history: list[str] = Field(default_factory=list, max_length=20)


class AnswerEnvelope(BaseModel):
    """What the API returns: the answer plus everything needed to debug it."""

    answer: GroundedAnswer
    gates: list[GateResult] = Field(default_factory=list)
    delivered: bool = True
    trace_id: str = ""
