"""Tests for non-echoing data asset query parsing."""

import pytest
from starlette.datastructures import QueryParams

from telescreen.api.routers.data_common import parse_filters
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.inventory.queries import AssetFilters
from telescreen.masking.redact import Redactor
from tests.support.samples import Sample, github_token


def test_parse_filters_rejects_sensitive_search_without_echo() -> None:
    """A search containing a detector hit is rejected with a static detail."""
    sample: Sample = github_token()

    with pytest.raises(TelescreenError) as caught:
        parse_filters(QueryParams({"q": sample.value}), Redactor.default())

    assert caught.value.code is ErrorCode.RAW_VALUE_DETECTED
    assert sample.value not in caught.value.detail


def test_parse_filters_rejects_invalid_and_excess_field_filters() -> None:
    """Malformed field filters fail without returning submitted filter text."""
    with pytest.raises(TelescreenError) as caught:
        parse_filters(
            QueryParams("field=one&field=two&field=three&field=four&field=five&field=six"),
            Redactor.default(),
        )
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert "six" not in caught.value.detail

    with pytest.raises(TelescreenError):
        parse_filters(QueryParams({"field": "broken"}), Redactor.default())


def test_parse_filters_accepts_supported_tab_and_sort() -> None:
    """The list parser keeps the selected tab and sort fields."""
    filters: AssetFilters = parse_filters(
        QueryParams({"tab": "overdue", "sort": "-name", "limit": "25"}), Redactor.default()
    )

    assert filters.tab == "overdue"
    assert filters.sort == "-name"
    assert filters.limit == 25
