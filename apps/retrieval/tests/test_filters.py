"""Filter SQL is the enforcement point for hard product rule 6."""

import datetime as dt

from contracts.api import SearchFilters, SearchIndex
from contracts.okf import Audience, Brand
from retrieval.search import build_filter_sql, build_kb_filter_sql, build_web_filter_sql


def filters(**kw: object) -> SearchFilters:
    base: dict[str, object] = {
        "brand": Brand.tiq,
        "audience": Audience.public,
        "active_on": dt.date(2026, 7, 31),
    }
    base.update(kw)
    return SearchFilters.model_validate(base)


def test_public_session_excludes_internal_twice() -> None:
    q = build_kb_filter_sql(filters(audience=Audience.public))
    assert "metadata->>'audience' != 'internal'" in q.where
    assert q.params["audiences"] == ["public"]


def test_policyholder_session_gets_public_and_policyholder() -> None:
    q = build_kb_filter_sql(filters(audience=Audience.policyholder))
    assert "metadata->>'audience' != 'internal'" in q.where
    assert q.params["audiences"] == ["public", "policyholder"]


def test_internal_session_may_see_all_audiences() -> None:
    q = build_kb_filter_sql(filters(audience=Audience.internal))
    assert "!= 'internal'" not in q.where.replace("TRUE", "")
    assert q.params["audiences"] == ["public", "policyholder", "internal"]


def test_mandatory_conditions_always_present() -> None:
    q = build_kb_filter_sql(filters())
    for fragment in [
        "active = true",
        "status' = 'published'",
        "brand' ? %(brand)s",
        "language' = %(language)s",
        "jurisdiction' = %(jurisdiction)s",
        "effective_from')::date <= %(today)s",
        "effective_to')::date, 'infinity'::date) > %(today)s",
    ]:
        assert fragment in q.where, fragment
    assert q.params["today"] == dt.date(2026, 7, 31)


def test_optional_line_and_product_filters() -> None:
    q = build_kb_filter_sql(filters(line="personal/travel", product_code="TIQ-TRV"))
    assert "'common'" in q.where and q.params["line"] == "personal/travel"
    assert "'ALL'" in q.where and q.params["product_code"] == "TIQ-TRV"
    q2 = build_kb_filter_sql(filters())
    assert "line" not in q2.params and "product_code" not in q2.params


def test_web_filter_enforces_brand_ttl_and_demotion() -> None:
    q = build_web_filter_sql(filters())
    assert "brand = %(brand)s" in q.where
    assert "expires_at > %(now)s" in q.where
    assert "demoted = false" in q.where


def test_dispatch_by_index() -> None:
    assert "audience" in build_filter_sql(SearchIndex.kb, filters()).where
    assert "expires_at" in build_filter_sql(SearchIndex.web, filters()).where
