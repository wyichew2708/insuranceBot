"""A synthetic insurer site on two hosts, served in-process for the crawler.

This exists because the real hosts are not reachable from this environment. It
is **not** real Etiqa content: the hosts are IANA-reserved `.example` names,
every page carries a fixture banner, and every number is invented. What it does
reproduce faithfully is the *structure* the crawler must cope with — robots.txt,
a sitemap index, WordPress-style furniture, cookie banners, canonical tags,
benefit tables, PDF links, and the same insurer answering on two addresses.

The two hosts are **not two brands**. They are two front doors of the one
direct channel, which is why one can fall out of date on a figure while the
other is current — a website defect to file, not a product difference.

The product namespace mirrors §B.3 of the design so the compile step is
exercised against the taxonomy it will meet in production.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

import httpx

ETIQA = "www.etiqa.example"
TIQ = "www.tiq.example"

BANNER = (
    '<div class="fixture-banner">SYNTHETIC FIXTURE SITE — invented content for '
    "pipeline testing. Not real product information.</div>"
)

TIERS = ["Basic", "Standard", "Premier"]

# Products where the second front door is out of date on its headline benefit.
# The compile loop must notice and file a website defect rather than pick
# silently — the same product cannot have two different limits.
STALE_ON_SECOND_SURFACE = {"travel", "private-car"}


@dataclass(frozen=True)
class Product:
    slug: str
    line: str
    title: str
    aliases: tuple[str, ...]
    benefits: tuple[tuple[str, str], ...]  # (benefit label, unit)
    tiered: bool = True
    regulated_advice: bool = False
    hosts: tuple[str, ...] = (ETIQA, TIQ)
    exclusions: tuple[str, ...] = (
        "Pre-existing medical conditions",
        "War, civil commotion and unlawful acts",
        "Wear, tear and gradual deterioration",
    )


PRODUCTS: list[Product] = [
    Product(
        "travel",
        "general",
        "Travel Insurance",
        ("trip cover", "holiday insurance"),
        (
            ("Overseas medical expenses", "S$"),
            ("Trip cancellation", "S$"),
            ("Baggage loss", "S$"),
            ("Travel delay benefit", "S$"),
        ),
    ),
    Product(
        "home",
        "general",
        "Home Insurance",
        ("household insurance", "contents cover"),
        (("Household contents", "S$"), ("Alternative accommodation", "S$"), ("Renovation cover", "S$")),
    ),
    Product(
        "hdb-fire",
        "general",
        "HDB Fire Insurance",
        ("fire insurance", "hdb cover"),
        (("Structure reinstatement", "S$"), ("Fixtures and fittings", "S$")),
        tiered=False,
    ),
    Product(
        "maid",
        "general",
        "Maid Insurance",
        ("helper insurance", "domestic helper cover"),
        (("Medical expenses", "S$"), ("Personal accident", "S$"), ("Security bond", "S$")),
    ),
    Product(
        "pet",
        "general",
        "Pet Insurance",
        ("dog insurance", "cat insurance"),
        (("Veterinary expenses", "S$"), ("Third-party liability", "S$")),
    ),
    Product(
        "personal-cyber",
        "general",
        "Personal Cyber Insurance",
        ("cyber cover", "online fraud cover"),
        (("Online fraud loss", "S$"), ("Cyber extortion", "S$"), ("Data restoration", "S$")),
    ),
    Product(
        "personal-mobility",
        "general",
        "Personal Mobility Insurance",
        ("pmd insurance", "e-scooter cover"),
        (("Third-party liability", "S$"), ("Personal accident", "S$")),
        tiered=False,
    ),
    Product(
        "private-car",
        "motor",
        "Private Car Insurance",
        ("car insurance", "motor cover"),
        (("Own damage excess", "S$"), ("Third-party property damage", "S$"), ("No-claim discount", "%")),
        tiered=False,
    ),
    Product(
        "motorcycle",
        "motor",
        "Motorcycle Insurance",
        ("bike insurance", "motorbike cover"),
        (("Own damage excess", "S$"), ("Third-party property damage", "S$")),
        tiered=False,
    ),
    Product(
        "commercial-vehicle",
        "motor",
        "Commercial Vehicle Insurance",
        ("van insurance", "fleet cover"),
        (("Own damage excess", "S$"), ("Third-party property damage", "S$")),
        tiered=False,
        hosts=(ETIQA,),
    ),
    Product(
        "term-life",
        "protection",
        "Term Life Insurance",
        ("term assurance", "life cover"),
        (("Death benefit", "S$"), ("Terminal illness benefit", "S$")),
        regulated_advice=True,
        hosts=(ETIQA,),
    ),
    Product(
        "whole-life",
        "protection",
        "Whole Life Insurance",
        ("whole life plan",),
        (("Death benefit", "S$"), ("Surrender value", "S$")),
        regulated_advice=True,
        hosts=(ETIQA,),
    ),
    Product(
        "cancer",
        "protection",
        "Cancer Protection",
        ("cancer plan", "cancer cover"),
        (("Lump sum payout", "S$"), ("Early stage payout", "S$")),
        regulated_advice=True,
        hosts=(ETIQA,),
    ),
    Product(
        "critical-illness",
        "protection",
        "Critical Illness Protection",
        ("ci cover", "critical illness plan"),
        (("Lump sum payout", "S$"), ("Multiple claim limit", "S$")),
        regulated_advice=True,
        hosts=(ETIQA,),
    ),
    Product(
        "personal-accident",
        "protection",
        "Personal Accident Insurance",
        ("pa insurance", "accident cover"),
        (("Accidental death", "S$"), ("Medical reimbursement", "S$"), ("Daily hospital income", "S$")),
    ),
    Product(
        "cashsaver",
        "savings-retirement",
        "CashSaver Endowment",
        ("savings plan", "endowment"),
        (("Guaranteed maturity", "S$"), ("Annual payout", "S$")),
        regulated_advice=True,
        hosts=(ETIQA,),
    ),
    Product(
        "retirement-income",
        "savings-retirement",
        "Retirement Income Plan",
        ("retirement plan", "annuity"),
        (("Monthly income", "S$"), ("Guaranteed period", "years")),
        regulated_advice=True,
        hosts=(ETIQA,),
    ),
    Product(
        "invest-linked",
        "investments",
        "Investment-Linked Plan",
        ("ilp", "investment plan"),
        (("Minimum single premium", "S$"), ("Fund switching fee", "S$")),
        regulated_advice=True,
        tiered=False,
    ),
    Product(
        "business-package",
        "business",
        "Business Owners Package",
        ("sme insurance", "business cover"),
        (("Property damage", "S$"), ("Business interruption", "S$"), ("Public liability", "S$")),
        hosts=(ETIQA,),
    ),
    Product(
        "corporate-travel",
        "business",
        "Corporate Travel Insurance",
        ("business travel cover",),
        (("Overseas medical expenses", "S$"), ("Trip curtailment", "S$")),
        hosts=(ETIQA,),
    ),
    Product(
        "marine-cargo",
        "business",
        "Marine Cargo Insurance",
        ("cargo insurance", "goods in transit"),
        (("Cargo value limit", "S$"), ("Excess per shipment", "S$")),
        tiered=False,
        hosts=(ETIQA,),
    ),
    Product(
        "property-all-risks",
        "business",
        "Property All Risks",
        ("commercial property cover",),
        (("Building reinstatement", "S$"), ("Contents limit", "S$")),
        tiered=False,
        hosts=(ETIQA,),
    ),
]

# One insurer, one brand. Both hosts are surfaces of the direct channel; only
# the phone number on the page differs, and both reach the same insurer.
BRAND = "Etiqa"
HOTLINE = {ETIQA: "+65 6336 0477", TIQ: "+65 6887 8777"}

# How the intermediated routes are described on the site, so the compiler has
# a real source sentence for each of them.
DISTRIBUTION = [
    ("bank relationship manager", "through a bank relationship manager at our partner banks"),
    ("tied agent", "through a tied agent who represents us directly"),
    ("broker", "through a broker who arranges cover on your behalf"),
    ("independent financial adviser", "through an independent financial adviser"),
]


def _chrome(host: str, title: str, canonical: str, body: str, description: str = "") -> str:
    """Realistic furniture: cookie banner, nav, breadcrumbs, footer. The
    extractor has to survive all of it."""
    brand = BRAND
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>{title} | {brand}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="https://{host}{canonical}">
</head><body>
<div class="cookie-consent"><p>We use cookies to improve your experience.</p>
<button>Accept all</button></div>
<header class="site-header"><a class="logo" href="/">{brand}</a></header>
<nav class="main-nav"><ul>
<li><a href="/personal/">Personal</a></li><li><a href="/business/">Business</a></li>
<li><a href="/claims/">Claims</a></li><li><a href="/policy-services/">Policy Services</a></li>
<li><a href="/faqs/">FAQs</a></li><li><a href="/promotions/">Promotions</a></li>
</ul></nav>
<ul class="breadcrumb"><li><a href="/">Home</a></li><li>{title}</li></ul>
{BANNER}
<main>{body}</main>
<aside class="newsletter"><h3>Subscribe</h3><p>Get our newsletter.</p></aside>
<footer class="site-footer"><p>Underwritten by Meridian Assurance (Fixture) Pte. Ltd.,
UEN 000000000X. Copyright 2026.</p>
<ul><li><a href="/privacy-policy/">Privacy</a></li><li><a href="/terms/">Terms</a></li></ul>
</footer></body></html>"""


def _drift(value: str) -> str:
    """One host published a stale figure. Half the amount, same formatting."""
    match = re.fullmatch(r"(S\$)([\d,]+)", value)
    if not match:
        return value
    return f"{match.group(1)}{int(match.group(2).replace(',', '')) // 2:,}"


def _benefit_table(product: Product, rng: random.Random, drift: bool = False) -> str:
    header = ["Benefit"] + (TIERS if product.tiered else ["Cover"])
    rows = []
    for index, (label, unit) in enumerate(product.benefits):
        if unit == "%":
            values = [f"{rng.choice([30, 40, 50])}%"] * (len(TIERS) if product.tiered else 1)
        elif unit == "years":
            values = [f"{rng.choice([10, 15, 20])} years"] * (len(TIERS) if product.tiered else 1)
        else:
            base = rng.choice([1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000])
            values = (
                [f"S${base:,}", f"S${base * 2:,}", f"S${base * 4:,}"] if product.tiered else [f"S${base:,}"]
            )
        if drift and index == 0:
            values = [_drift(v) for v in values]
        rows.append(f"<tr><td>{label}</td>" + "".join(f"<td>{v}</td>" for v in values) + "</tr>")
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in header) + "</tr>"
    return f'<table class="benefits">{head}{"".join(rows)}</table>'


def _product_page(product: Product, host: str, rng: random.Random, drift: bool = False) -> str:
    other = TIQ if host == ETIQA else ETIQA
    # The same cover at our other address — a second front door, not a second
    # insurer and not a different product.
    handoff = (
        f"<p>The same {product.title} is also at "
        f'<a href="https://{other}/personal/{product.slug}/">{other}</a>, '
        "our other address for buying direct.</p>"
        if other in product.hosts
        else ""
    )
    advice = (
        "<p>This plan is advised. Speak to a licensed financial adviser before buying.</p>"
        if product.regulated_advice
        else ""
    )
    exclusions = "".join(f"<li>{item}</li>" for item in product.exclusions)
    body = f"""<h1>{product.title}</h1>
<p>{product.title} from {BRAND} protects you against the costs described below.
Also known as {", ".join(product.aliases)}.</p>
{advice}
<h2>What is covered</h2>
{_benefit_table(product, rng, drift)}
<h2>What is not covered</h2><ul>{exclusions}</ul>
<h2>How to buy</h2>
<p>Buy online, or call {BRAND} on {HOTLINE[host]} to speak with us.</p>
{handoff}
<h2>Documents</h2>
<ul><li><a href="/policy-wordings/{product.slug}-2026.pdf">Policy wording (PDF)</a></li>
<li><a href="/policy-wordings/{product.slug}-summary-2026.pdf">Product summary (PDF)</a></li></ul>
<p>See how to <a href="/claims/{product.slug}/">make a claim</a> or
<a href="/faqs/{product.slug}/">read the FAQs</a>.</p>"""
    return _chrome(
        host, product.title, f"/personal/{product.slug}/", body, f"{product.title} cover and limits."
    )


def _claims_page(product: Product, host: str) -> str:
    body = f"""<h1>Making a {product.title} claim</h1>
<h2>Before you start</h2>
<p>Have your policy number, supporting receipts and any incident reports ready.</p>
<h2>Steps</h2>
<ol><li>Log in to the customer portal.</li><li>Select the policy and choose Make a claim.</li>
<li>Upload the supporting documents and submit.</li></ol>
<h2>How long it takes</h2>
<p>Straightforward claims are assessed within ten working days of complete documents.</p>"""
    return _chrome(host, f"{product.title} claims", f"/claims/{product.slug}/", body)


def _faq_page(product: Product, host: str) -> str:
    body = f"""<h1>{product.title} FAQs</h1>
<h2>Who can buy this plan?</h2>
<p>Singapore residents holding a valid identity document may buy this plan.</p>
<h2>When does cover start?</h2>
<p>Cover starts on the commencement date shown in your policy schedule.</p>
<h2>Can I cancel?</h2>
<p>You may cancel during the free-look period for a refund of premium paid.</p>"""
    return _chrome(host, f"{product.title} FAQs", f"/faqs/{product.slug}/", body)


SERVICING = [
    ("change-address", "Change your address", "Update your correspondence address in the portal."),
    ("update-bank-details", "Update bank details", "Change the account used for premium payment."),
    ("change-nomination", "Change your nomination", "Update the nominees recorded on your policy."),
    ("renew-policy", "Renew your policy", "Renew before the expiry date shown in your schedule."),
    ("cancel-policy", "Cancel your policy", "Request cancellation and any pro-rata refund."),
    ("request-documents", "Request policy documents", "Download or request a copy of your documents."),
]


def _servicing_page(slug: str, title: str, blurb: str, host: str) -> str:
    body = f"""<h1>{title}</h1>
<h2>What you need</h2><p>{blurb} You will need your policy number to continue.</p>
<h2>Steps</h2><ol><li>Sign in to the customer portal.</li><li>Choose the policy.</li>
<li>Submit the request and keep the acknowledgement reference.</li></ol>
<h2>How long it takes</h2><p>Most requests are completed within three working days.</p>"""
    return _chrome(host, title, f"/policy-services/{slug}/", body)


def _static_pages(host: str) -> dict[str, str]:
    brand = BRAND
    return {
        f"https://{host}/": _chrome(
            host,
            "Home",
            "/",
            "<h1>Insurance made straightforward</h1>"
            '<p>Browse our <a href="/personal/">personal</a> and '
            '<a href="/business/">business</a> insurance.</p>',
        ),
        f"https://{host}/promotions/": _chrome(
            host,
            "Promotions",
            "/promotions/",
            "<h1>Current promotions</h1><h2>Online purchase offer</h2>"
            "<p>Save 15% on selected plans bought online before 31 August 2026 "
            "with code SAVE15. Information is accurate as of 1 August 2026.</p>",
        ),
        f"https://{host}/privacy-policy/": _chrome(
            host,
            "Privacy policy",
            "/privacy-policy/",
            "<h1>Privacy policy</h1><h2>How we use your data</h2>"
            "<p>We collect and use personal data in line with the Personal Data "
            "Protection Act and our published notice.</p>",
        ),
        f"https://{host}/claims/": _chrome(
            host, "Claims", "/claims/", "<h1>Claims</h1><p>Choose your product to see how to claim.</p>"
        ),
        f"https://{host}/how-to-buy/": _chrome(
            host,
            "How to buy",
            "/how-to-buy/",
            "<h1>How to buy</h1><p>You can buy directly from us online at either "
            "of our addresses, or through someone who advises you. Whichever "
            "route you take, the cover, limits and exclusions are the same — "
            "start from the product you want, not from where you buy it.</p>"
            "<h2>Ways to buy</h2><ul>"
            + "".join(f"<li>You can buy {blurb}.</li>" for _, blurb in DISTRIBUTION)
            + "</ul>",
        ),
        f"https://{host}/policy-services/": _chrome(
            host,
            "Policy services",
            "/policy-services/",
            "<h1>Policy services</h1><p>Manage your policy online.</p>",
        ),
        f"https://{host}/blog/choosing-cover/": _chrome(
            host,
            "Choosing cover",
            "/blog/choosing-cover/",
            "<h1>How to choose cover</h1><p>Think about what you would struggle "
            f"to replace. {brand} has options for most situations.</p>",
        ),
        # Transactional surface: recorded as a link, never crawled.
        f"https://{host}/buy-online/travel/": _chrome(
            host, "Buy online", "/buy-online/travel/", "<h1>Buy online</h1><p>Quote form.</p>"
        ),
    }


@dataclass
class Site:
    pages: dict[str, str] = field(default_factory=dict)
    robots: dict[str, str] = field(default_factory=dict)
    sitemaps: dict[str, str] = field(default_factory=dict)


def build_site(seed: int = 7) -> Site:
    site = Site()

    for host in (ETIQA, TIQ):
        site.pages.update(_static_pages(host))
        urls: list[str] = [f"https://{host}/"]
        for product in PRODUCTS:
            if host not in product.hosts:
                continue
            base = f"https://{host}/personal/{product.slug}/"
            # Seeded per product, not per page: both front doors of the one
            # channel publish the same numbers — except where one has gone stale.
            product_rng = random.Random(f"{seed}:{product.slug}")
            drift = host == TIQ and product.slug in STALE_ON_SECOND_SURFACE
            site.pages[base] = _product_page(product, host, product_rng, drift)
            site.pages[f"https://{host}/claims/{product.slug}/"] = _claims_page(product, host)
            site.pages[f"https://{host}/faqs/{product.slug}/"] = _faq_page(product, host)
            urls += [base, f"https://{host}/claims/{product.slug}/", f"https://{host}/faqs/{product.slug}/"]
        for slug, title, blurb in SERVICING:
            url = f"https://{host}/policy-services/{slug}/"
            site.pages[url] = _servicing_page(slug, title, blurb, host)
            urls.append(url)
        urls += [
            u
            for u in site.pages
            if u.startswith(f"https://{host}/") and u not in urls and "/buy-online/" not in u
        ]

        # A sitemap index pointing at two child sitemaps, as WordPress emits.
        half = len(urls) // 2
        site.sitemaps[f"https://{host}/wp-sitemap-1.xml"] = _urlset(urls[:half])
        site.sitemaps[f"https://{host}/wp-sitemap-2.xml"] = _urlset(urls[half:])
        site.sitemaps[f"https://{host}/wp-sitemap.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?><sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<sitemap><loc>https://{host}/wp-sitemap-{n}.xml</loc></sitemap>" for n in (1, 2))
            + "</sitemapindex>"
        )
        site.robots[host] = (
            "User-agent: *\n"
            "Disallow: /buy-online/\n"
            "Disallow: /wp-admin/\n"
            "Crawl-delay: 0\n"
            f"Sitemap: https://{host}/wp-sitemap.xml\n"
        )
    return site


def _urlset(urls: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><urlset '
        'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        + "</urlset>"
    )


def transport(site: Site | None = None) -> httpx.MockTransport:
    """Serve the site in-process, so the real Crawler runs unmodified."""
    site = site or build_site()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        host = request.url.host
        if request.url.path == "/robots.txt":
            body = site.robots.get(host)
            return (
                httpx.Response(200, text=body, headers={"content-type": "text/plain"})
                if body
                else httpx.Response(404)
            )
        if url in site.sitemaps:
            return httpx.Response(200, text=site.sitemaps[url], headers={"content-type": "application/xml"})
        for candidate in (url, url.rstrip("/") + "/", url + "/"):
            if candidate in site.pages:
                return httpx.Response(
                    200, text=site.pages[candidate], headers={"content-type": "text/html; charset=utf-8"}
                )
        if url.endswith(".pdf"):
            return httpx.Response(
                200, content=b"%PDF-1.4 fixture", headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)
