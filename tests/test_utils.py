"""Tests for src/utils.py.

The `safe_id` cases are security-critical: every FreeAgent ID is interpolated into a
request path, so `safe_id` is the gate that stops a path-traversal payload reaching
`FreeAgentClient` in the first place (client.py's origin guard is the second gate).
"""

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from src.utils import SafeId, build_body, build_params, safe_id


class TestSafeId:
    @pytest.mark.parametrize(
        "value",
        ["123", "abc", "ABC", "a1b2c3", "with-hyphen", "with_underscore", "007", "a"],
    )
    def test_accepts_valid_ids(self, value: str) -> None:
        assert safe_id(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "",  # empty
            "../etc/passwd",  # traversal
            "..",  # bare dots
            "1/2",  # path separator
            "1\\2",  # windows separator
            "https://evil.com",  # absolute url
            "1 2",  # whitespace
            "1?x=2",  # query string injection
            "1#frag",
            "1%2f2",  # percent-encoded separator
            "café",  # non-ascii
            "1.2",  # dot
        ],
    )
    def test_rejects_invalid_ids(self, value: str) -> None:
        with pytest.raises(ValueError):
            safe_id(value)

    def test_safe_id_annotation_validates_inside_a_model(self) -> None:
        """SafeId is the form tool signatures use, so it must reject via pydantic too."""

        class Params(BaseModel):
            contact_id: SafeId

        assert Params(contact_id="abc-123").contact_id == "abc-123"
        with pytest.raises(ValidationError):
            Params(contact_id="../../users/me")


class TestBuildParams:
    def test_drops_none_values(self) -> None:
        assert build_params(view="all", contact=None) == {"view": "all"}

    def test_passes_native_types_through(self) -> None:
        """build_params no longer stringifies; httpx renders values at request time."""
        assert build_params(page=2, per_page=50) == {"page": 2, "per_page": 50}

    def test_keeps_falsy_non_none_values(self) -> None:
        assert build_params(page=0, query="") == {"page": 0, "query": ""}

    def test_empty_input_gives_empty_dict(self) -> None:
        assert build_params() == {}

    def test_httpx_renders_booleans_lowercase(self) -> None:
        """The lowercase-boolean guarantee now lives in httpx, which builds the URL.

        str(True) is 'True', which FreeAgent rejects; this pins the behaviour we rely on
        by delegating rendering to httpx instead of stringifying in build_params.
        """
        params = build_params(nested_bill_items=True, sub_accounts=False)
        assert str(httpx.QueryParams(params)) == "nested_bill_items=true&sub_accounts=false"


class TestBuildBody:
    def test_drops_none_values(self) -> None:
        assert build_body(reference="INV-1", comments=None) == {"reference": "INV-1"}

    def test_preserves_native_types(self) -> None:
        """Unlike build_params, body values must NOT be stringified."""
        body = build_body(budget=1000, is_ir35=True, items=[{"price": "10.00"}])
        assert body == {"budget": 1000, "is_ir35": True, "items": [{"price": "10.00"}]}

    def test_serialises_pydantic_models_in_lists(self) -> None:
        """Line items arrive as Pydantic models and must reach httpx as plain dicts."""

        class Item(BaseModel):
            description: str
            total_value: str
            sales_tax_rate: str | None = None

        body = build_body(bill_items=[Item(description="Hosting", total_value="99.00")])
        assert body == {"bill_items": [{"description": "Hosting", "total_value": "99.00"}]}

    def test_keeps_falsy_non_none_values(self) -> None:
        assert build_body(budget=0, is_ir35=False) == {"budget": 0, "is_ir35": False}
