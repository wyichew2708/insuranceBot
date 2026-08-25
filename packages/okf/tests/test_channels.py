"""Channel-token resolution — the renderer's half of the channel contract.

A page declares every route it is sold through; a session is on exactly one.
These pin the two failures that must never be confused: a route withheld on
purpose, and a token that names nothing.
"""

from okf import Channel, resolve_channel_tokens, route_for

BLOCK = "<!-- okf:channel-variant -->\n%s\n<!-- /okf:channel-variant -->"
DIRECT_ROW = "| {{channel.direct.landing}} | {{channel.direct.hotline}} |"
AGENCY_ROW = "| {{channel.agency.landing}} | {{channel.agency.hotline}} |"


def test_the_session_route_renders_and_the_others_are_dropped() -> None:
    out = resolve_channel_tokens(BLOCK % f"{DIRECT_ROW}\n{AGENCY_ROW}", Channel.direct)
    assert "{{" not in out.text and "|" not in out.text
    assert "https://www.etiqa.com.sg/" in out.text
    assert "find-an-agent" not in out.text
    assert out.routes == [Channel.direct]
    # Withholding another route is the point, not a gap in the corpus.
    assert out.suppressed == ["agency.*"]
    assert not out.unresolved


def test_an_unknown_channel_offers_every_declared_route() -> None:
    out = resolve_channel_tokens(BLOCK % f"{DIRECT_ROW}\n{AGENCY_ROW}", Channel.unknown)
    assert out.routes == [Channel.direct, Channel.agency]
    assert "find-an-agent" in out.text
    assert not out.suppressed and not out.unresolved


def test_a_token_naming_no_route_is_a_gap_not_a_withheld_contact() -> None:
    """An authoring slip must degrade honestly (§F.1) rather than look like a
    contact this session was not allowed to see."""
    out = resolve_channel_tokens(BLOCK % "| {{channel.nosuch.landing}} |", Channel.direct)
    assert out.unresolved == ["nosuch.*"]
    assert not out.suppressed
    assert "{{" not in out.text


def test_a_block_with_nothing_to_render_is_declared_not_silently_dropped() -> None:
    out = resolve_channel_tokens(BLOCK % "| Call the usual number |", Channel.direct)
    assert out.unresolved == ["variant-block-declares-no-route"]
    assert out.text.strip() == ""


def test_a_token_outside_a_block_resolves_under_the_same_rule() -> None:
    own = resolve_channel_tokens("Call {{channel.direct.hotline}}.", Channel.direct)
    assert own.text == "Call +65 6336 0477."
    foreign = resolve_channel_tokens("Call {{channel.agency.hotline}}.", Channel.direct)
    assert "6336" not in foreign.text
    assert foreign.suppressed == ["agency.hotline"]


def test_a_binding_deep_link_beats_the_registry_landing() -> None:
    """The registry knows the route; the page knows the product's own door."""
    from okf import ChannelBinding

    binding = ChannelBinding(
        ref="channel/direct",
        name="Direct",
        purchase="direct_online",
        landing="https://www.etiqa.com.sg/personal/travel-insurance/",
        surfaces=["https://www.tiq.com.sg/product/travel-insurance/"],
    )
    out = resolve_channel_tokens(BLOCK % DIRECT_ROW, Channel.direct, [binding])
    assert "personal/travel-insurance" in out.text
    assert "tiq.com.sg/product/travel-insurance" in out.text
    # The hotline the binding omits still comes from the registry.
    assert "+65 6336 0477" in out.text


def test_both_direct_front_doors_keep_their_own_hotline() -> None:
    route = route_for(Channel.direct)
    assert route is not None
    assert route.contact_values() == (
        "https://www.etiqa.com.sg/",
        "+65 6336 0477",
        "https://www.tiq.com.sg/",
        "+65 6887 8777",
    )
