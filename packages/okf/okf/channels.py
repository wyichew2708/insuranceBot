"""Distribution channels (§B.1).

A channel is a *route to market*, not a brand. Every product sold through
every channel is the same canonical Etiqa Insurance Pte. Ltd. product; the
channel decides who the customer talks to and how they buy, nothing else.

`www.etiqa.com.sg` and `www.tiq.com.sg` are two **surfaces of the same direct
channel**. They are not competing brands and must never be rendered as though
the customer had to choose between them — a customer starts from the product,
not from a brand. Both surfaces stay reachable: an answer in a direct-channel
session may cite either one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from okf.page import ChannelBinding

#: The one brand. There is no second brand to disambiguate against.
BRAND = "Etiqa"
UNDERWRITER = "Etiqa Insurance Pte. Ltd."
UEN = "201331905K"


class Channel(str, Enum):
    """Distribution routes. Values are page ids so a session binds straight to
    the wiki page describing the route."""

    direct = "channel/direct"
    bancassurance = "channel/bancassurance"
    agency = "channel/agency"
    broker = "channel/broker"
    ifa = "channel/ifa"
    # e.g. an inbound WhatsApp with no routing context — render every route
    # rather than guessing one (§C.4).
    unknown = "unknown"


@dataclass(frozen=True)
class Surface:
    """One reachable front door of a channel. A channel may have several; they
    are interchangeable, and citing any of them satisfies the coherence gate."""

    host: str
    landing: str
    hotline: str


@dataclass(frozen=True)
class ChannelSpec:
    ref: Channel
    name: str
    #: How the customer completes a purchase on this route.
    purchase: str
    #: Who the customer is actually dealing with. Drives the handoff wording.
    intermediary: str | None
    surfaces: tuple[Surface, ...]

    @property
    def landing(self) -> str:
        return self.surfaces[0].landing

    @property
    def hotline(self) -> str:
        return self.surfaces[0].hotline

    @property
    def hosts(self) -> tuple[str, ...]:
        return tuple(s.host for s in self.surfaces)

    def contact_values(self) -> tuple[str, ...]:
        """Every landing URL and hotline that legitimately belongs to this
        channel. The coherence gate diffs these between channels."""
        out: list[str] = []
        for surface in self.surfaces:
            out.append(surface.landing)
            out.append(surface.hotline)
        return tuple(out)


DIRECT = ChannelSpec(
    ref=Channel.direct,
    name="Direct",
    purchase="direct_online",
    intermediary=None,
    surfaces=(
        Surface("www.etiqa.com.sg", "https://www.etiqa.com.sg/", "+65 6336 0477"),
        # The same direct channel, a second front door. Kept reachable so
        # existing links and customer phrasing still resolve.
        Surface("www.tiq.com.sg", "https://www.tiq.com.sg/", "+65 6887 8777"),
    ),
)

BANCASSURANCE = ChannelSpec(
    ref=Channel.bancassurance,
    name="Bancassurance",
    purchase="via_bank_partner",
    intermediary="bank relationship manager",
    surfaces=(Surface("www.etiqa.com.sg", "https://www.etiqa.com.sg/bancassurance/", "+65 6336 0477"),),
)

AGENCY = ChannelSpec(
    ref=Channel.agency,
    name="Agency",
    purchase="via_tied_agent",
    intermediary="tied agent",
    surfaces=(Surface("www.etiqa.com.sg", "https://www.etiqa.com.sg/find-an-agent/", "+65 6336 0477"),),
)

BROKER = ChannelSpec(
    ref=Channel.broker,
    name="Broker",
    purchase="via_broker",
    intermediary="broker",
    surfaces=(Surface("www.etiqa.com.sg", "https://www.etiqa.com.sg/broker/", "+65 6336 0477"),),
)

IFA = ChannelSpec(
    ref=Channel.ifa,
    name="IFA",
    purchase="via_financial_adviser",
    intermediary="independent financial adviser",
    surfaces=(Surface("www.etiqa.com.sg", "https://www.etiqa.com.sg/financial-adviser/", "+65 6336 0477"),),
)

CHANNELS: dict[Channel, ChannelSpec] = {
    spec.ref: spec for spec in (DIRECT, BANCASSURANCE, AGENCY, BROKER, IFA)
}

#: Ordered for rendering when the channel is unknown and every route is shown.
ALL_CHANNELS: tuple[ChannelSpec, ...] = (DIRECT, BANCASSURANCE, AGENCY, BROKER, IFA)


def spec_for(channel: Channel | str) -> ChannelSpec | None:
    """The spec for a channel ref, or None for `unknown`/unrecognised."""
    if isinstance(channel, str):
        try:
            channel = Channel(channel)
        except ValueError:
            return None
    return CHANNELS.get(channel)


def channel_for_host(host: str) -> ChannelSpec | None:
    """Which channel a crawled host is a surface of.

    Both Etiqa and Tiq hosts answer `direct` — that is the whole point.
    """
    host = host.lower().removeprefix("www.")
    for spec in ALL_CHANNELS:
        for surface in spec.surfaces:
            if surface.host.lower().removeprefix("www.") == host:
                return spec
    # Fixture hosts (`.example`) mirror the real ones one label up.
    stem = host.split(".")[0]
    for spec in ALL_CHANNELS:
        if any(s.host.lower().removeprefix("www.").split(".")[0] == stem for s in spec.surfaces):
            return spec
    return None


def brand_for_host(host: str) -> str | None:
    """The consumer-facing name of a front door.

    A customer says "Tiq travel insurance" or "Etiqa travel insurance" and
    means the same product. These are the words they actually type, derived
    from the registered surface hosts rather than listed separately, so a new
    front door cannot be added without its name following it.
    """
    label = host.lower().removeprefix("www.").split(".")[0]
    return label.title() if label else None


def surface_brands() -> tuple[str, ...]:
    """Every front-door name across every channel, in registry order."""
    out: list[str] = []
    for spec in ALL_CHANNELS:
        for surface in spec.surfaces:
            name = brand_for_host(surface.host)
            if name and name not in out:
                out.append(name)
    return tuple(out)


def foreign_contact_values(channel: Channel) -> tuple[str, ...]:
    """Contact details belonging to *other* channels.

    Surfaces of the caller's own channel are excluded, so an answer that cites
    tiq.com.sg in a direct session is not a leak.
    """
    own_spec = spec_for(channel)
    own = set(own_spec.contact_values()) if own_spec else set()
    out: list[str] = []
    for spec in ALL_CHANNELS:
        if spec.ref == channel:
            continue
        out.extend(v for v in spec.contact_values() if v not in own)
    return tuple(dict.fromkeys(out))


# --- rendering --------------------------------------------------------------
#
# A page describes every route it is sold through, but an answer is delivered
# into *one* session on *one* route. Pages therefore carry channel tokens
# (`{{channel.direct.landing}}`) inside a channel-variant block, and the
# renderer — never the model — substitutes the values for the session's own
# route and drops the rest. That is the same separation `okf.tables` applies to
# numbers: the fact is fetched, the prose is composed.

CHANNEL_TOKEN_RE = re.compile(r"\{\{channel\.([a-z0-9_]+)\.([a-z0-9_]+)\}\}")
CHANNEL_VARIANT_RE = re.compile(
    r"<!--\s*okf:channel-variant\s*-->(?P<body>.*?)<!--\s*/okf:channel-variant\s*-->", re.S
)


def slug_for(channel: Channel) -> str:
    """The token name for a channel: `channel/direct` is written `direct`."""
    return channel.value.rsplit("/", 1)[-1]


CHANNEL_BY_SLUG: dict[str, Channel] = {slug_for(c): c for c in Channel if c is not Channel.unknown}


@dataclass(frozen=True)
class Route:
    """One distribution route resolved for rendering.

    Merges what a page's `ChannelBinding` declares (product deep links) with
    the registry `ChannelSpec` (the route's front doors, and who the customer
    actually deals with). A route with more than one front door carries them
    all: they are interchangeable addresses of the same route, not a choice
    the customer has to make.
    """

    channel: Channel
    name: str
    purchase: str | None = None
    intermediary: str | None = None
    #: (landing, hotline) per front door, primary first.
    front_doors: tuple[tuple[str, str | None], ...] = ()

    @property
    def slug(self) -> str:
        return slug_for(self.channel)

    @property
    def landing(self) -> str | None:
        return self.front_doors[0][0] if self.front_doors else None

    @property
    def hotline(self) -> str | None:
        return self.front_doors[0][1] if self.front_doors else None

    @property
    def surfaces(self) -> tuple[str, ...]:
        return tuple(landing for landing, _ in self.front_doors[1:])

    def contact_values(self) -> tuple[str, ...]:
        """Every value this route can put into an answer. The numeric-binding
        gate treats these as bound: the renderer substituted them."""
        out: list[str] = []
        for landing, hotline in self.front_doors:
            out.extend(v for v in (landing, hotline) if v)
        return tuple(dict.fromkeys(out))

    def value(self, attribute: str) -> str | None:
        """The value a `{{channel.<slug>.<attribute>}}` token stands for."""
        if attribute == "surfaces":
            return ", ".join(self.surfaces) or None
        value = {
            "landing": self.landing,
            "hotline": self.hotline,
            "name": self.name,
            "purchase": self.purchase,
            "intermediary": self.intermediary,
        }.get(attribute)
        return value or None

    def sentence(self) -> str:
        """How the route reads to a customer. Deterministic — the model is
        never asked where the customer should go next (§C.4)."""
        head = self.name
        if self.purchase:
            head = f"{head} ({self.purchase.replace('_', ' ')})"
        doors: list[str] = []
        for landing, hotline in self.front_doors:
            if landing and hotline:
                doors.append(f"{landing} or call {hotline}")
            elif landing:
                doors.append(landing)
            elif hotline:
                doors.append(f"call {hotline}")
        if not doors:
            return f"{head}."
        text = doors[0]
        if len(doors) > 1:
            text += ", also at " + ", ".join(doors[1:])
        return f"{head}: {text}."


def route_for(channel: Channel, binding: ChannelBinding | None = None) -> Route | None:
    """The renderable route for a channel, or None if there is nothing to
    render. A page binding wins over the registry — it carries the product's
    own deep link — with the registry filling in what a binding cannot say."""
    spec = spec_for(channel)
    if binding is not None:
        front_doors: list[tuple[str, str | None]] = [
            (binding.landing, binding.hotline or (spec.hotline if spec else None))
        ]
        front_doors.extend((surface, None) for surface in binding.surfaces)
        return Route(
            channel=channel,
            name=binding.name,
            purchase=binding.purchase,
            intermediary=spec.intermediary if spec else None,
            front_doors=tuple(front_doors),
        )
    if spec is None:
        return None
    return Route(
        channel=channel,
        name=spec.name,
        purchase=spec.purchase,
        intermediary=spec.intermediary,
        front_doors=tuple((s.landing, s.hotline) for s in spec.surfaces),
    )


def route_from_page(channel: Channel, declared: Mapping[str, Any]) -> Route | None:
    """The route as the channel's own wiki page declares it.

    That page is compiled from the website, so it outranks the registry
    constant below it: a hotline that changed on the site must be answered
    from the compiled page, never from a number baked into this module.
    """
    spec = spec_for(channel)
    landing = str(declared.get("landing") or "") or None
    hotline = str(declared.get("hotline") or "") or None
    if landing is None and hotline is None:
        return None
    front_doors: list[tuple[str, str | None]] = [(landing or "", hotline)]
    front_doors.extend((str(s), None) for s in (declared.get("surfaces") or []))
    return Route(
        channel=channel,
        name=str(declared.get("name") or (spec.name if spec else channel.value)),
        purchase=str(declared.get("purchase") or "") or (spec.purchase if spec else None),
        intermediary=str(declared.get("intermediary") or "") or (spec.intermediary if spec else None),
        front_doors=tuple(front_doors),
    )


def routes_for(
    bindings: Sequence[ChannelBinding] = (), declared: Mapping[str, Route] | None = None
) -> dict[str, Route]:
    """Routes by token slug, lowest authority first: the registry, then what
    each channel's compiled page declares, then the binding on the page being
    rendered — which carries the product's own deep link."""
    routes = {slug: route for slug, channel in CHANNEL_BY_SLUG.items() if (route := route_for(channel))}
    if declared:
        routes.update(declared)
    for binding in bindings:
        channel = CHANNEL_BY_SLUG.get(binding.ref.rsplit("/", 1)[-1])
        if channel is None:
            continue
        bound = route_for(channel, binding)
        if bound is not None:
            routes[slug_for(channel)] = bound
    return routes


@dataclass
class ChannelTransclusion:
    """Result of resolving `{{channel.*}}` tokens in a page body."""

    text: str
    #: Values the renderer substituted, so the numeric-binding gate can prove
    #: the digits inside them were not produced by the model.
    values: list[str] = field(default_factory=list)
    #: Channels actually rendered — empty when the page offers no route this
    #: session may be sent down.
    routes: list[Channel] = field(default_factory=list)
    #: Tokens dropped because they belong to another distribution channel.
    #: Not a data gap: withholding them is the point (§F.2 channel coherence).
    suppressed: list[str] = field(default_factory=list)
    #: Tokens naming a channel or attribute that resolves to nothing.
    unresolved: list[str] = field(default_factory=list)


def resolve_channel_tokens(
    body: str,
    channel: Channel,
    bindings: Sequence[ChannelBinding] = (),
    declared: Mapping[str, Route] | None = None,
) -> ChannelTransclusion:
    """Render a page body for one session's route.

    A channel-variant block is machine markup, not customer copy: it declares
    which routes the page is sold through, and this replaces the whole block
    with a rendered sentence per route the session may actually be sent down.
    With a known channel that is the session's own route and nothing else —
    offering a second route's contact is what the channel-coherence gate exists
    to block. With the channel unknown, every route the block declares is
    rendered, which is the same "offer them all rather than guess one" rule the
    rest of the harness follows (§C.4).
    """
    routes = routes_for(bindings, declared)
    own = slug_for(channel) if channel is not Channel.unknown else None
    out = ChannelTransclusion(text=body)

    def render(slug: str, attribute: str) -> str | None:
        """The substituted value, or None if this session may not see it."""
        if slug not in CHANNEL_BY_SLUG:
            # Names no route at all: an authoring slip, not a withheld contact.
            out.unresolved.append(f"{slug}.{attribute}")
            return None
        if own is not None and slug != own:
            out.suppressed.append(f"{slug}.{attribute}")
            return None
        route = routes.get(slug)
        value = route.value(attribute) if route is not None else None
        if value is None:
            out.unresolved.append(f"{slug}.{attribute}")
            return None
        out.values.append(value)
        return value

    def replace_block(match: re.Match[str]) -> str:
        declared: list[str] = []
        for token in CHANNEL_TOKEN_RE.finditer(match.group("body")):
            if token.group(1) not in declared:
                declared.append(token.group(1))
        if not declared:
            # Nothing here for the renderer to produce. Dropping the block
            # silently would delete authored copy, so declare the gap instead.
            out.unresolved.append("variant-block-declares-no-route")
            return ""
        # A slug naming no route is an authoring slip; a slug naming someone
        # else's route is withheld on purpose. They are not the same failure.
        out.unresolved.extend(f"{s}.*" for s in declared if s not in CHANNEL_BY_SLUG)
        known = [s for s in declared if s in CHANNEL_BY_SLUG]
        wanted = [s for s in known if s == own] if own is not None else known
        out.suppressed.extend(f"{s}.*" for s in known if s not in wanted)
        sentences: list[str] = []
        for slug in wanted:
            route = routes.get(slug)
            if route is None:
                out.unresolved.append(f"{slug}.*")
                continue
            sentences.append(route.sentence())
            out.values.extend(route.contact_values())
            out.routes.append(route.channel)
        return "\n".join(sentences)

    text = CHANNEL_VARIANT_RE.sub(replace_block, body)

    # Any token authored outside a variant block still resolves, under the same
    # rule — a page may name its own route in prose.
    def replace_token(match: re.Match[str]) -> str:
        return render(match.group(1), match.group(2)) or ""

    out.text = CHANNEL_TOKEN_RE.sub(replace_token, text)
    out.values = list(dict.fromkeys(out.values))
    return out


def find_channel_tokens(body: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in CHANNEL_TOKEN_RE.finditer(body)]
