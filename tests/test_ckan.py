"""Unit tests for the CKAN client (no network)."""

from __future__ import annotations

import pytest

from datosgobdo_mcp import ckan


# ─── Solr escape ──────────────────────────────────────────────────────────────


def test_escape_solr_passes_safe_chars():
    assert ckan._escape_solr("safe-slug-123") == "safe-slug-123".replace("-", r"\-")


@pytest.mark.parametrize(
    "raw,expected_contains",
    [
        ('with "quote"', r'\"quote\"'),
        ("with : colon", r"\:"),
        ("(parens)", r"\(parens\)"),
        ("a+b", r"a\+b"),
        ("a&b", r"a\&b"),
        ("path/slash", r"path\/slash"),
        ("back\\slash", r"back\\slash"),
        ("star*", r"star\*"),
    ],
)
def test_escape_solr_escapes_reserved(raw, expected_contains):
    out = ckan._escape_solr(raw)
    assert expected_contains in out


def test_fq_term_quotes_when_value_has_space():
    out = ckan._fq_term("organization", "ministerio de salud")
    assert out.startswith('organization:"')
    assert out.endswith('"')


def test_fq_term_no_quotes_when_value_simple():
    out = ckan._fq_term("organization", "ministerio-de-salud")
    assert '"' not in out
    assert out.startswith("organization:")


def test_fq_term_escapes_special_in_value():
    out = ckan._fq_term("tags", 'has "quote" inside')
    # Must contain the escaped quote, never raw `"` outside outer quotes.
    assert r'\"' in out
    # Outer must wrap with non-escaped quotes since value has a space.
    assert out.startswith('tags:"')
    assert out.endswith('"')


# ─── Formatters ───────────────────────────────────────────────────────────────


def test_truncate_short_string_returned_as_is():
    assert ckan._truncate("hola", 100) == "hola"


def test_truncate_long_string_gets_ellipsis():
    long = "x" * 500
    out = ckan._truncate(long, 50)
    assert out is not None
    assert len(out) <= 51  # 50 chars + ellipsis
    assert out.endswith("…")


def test_truncate_none_returns_none():
    assert ckan._truncate(None, 10) is None


def test_format_dataset_minimum_fields():
    raw = {
        "id": "abc",
        "name": "presupuesto-2024",
        "title": "Presupuesto 2024",
        "organization": {"name": "minhacienda", "title": "Min. de Hacienda"},
        "notes": "Descripción larga " * 100,
        "tags": [{"name": "finanzas"}, {"name": "presupuesto"}],
        "groups": [{"title": "Economía"}],
        "resources": [{"format": "CSV"}, {"format": "XLSX"}, {"format": "CSV"}],
        "metadata_modified": "2026-01-01T00:00:00",
    }
    d = ckan.format_dataset(raw)
    assert d["id"] == "abc"
    assert d["name"] == "presupuesto-2024"
    assert d["organization"] == "Min. de Hacienda"
    assert d["organization_slug"] == "minhacienda"
    assert d["resource_count"] == 3
    assert set(d["formats"]) == {"CSV", "XLSX"}  # deduped
    assert d["url"] == "https://datos.gob.do/dataset/presupuesto-2024"
    assert len(d["notes"]) <= ckan.NOTES_TRUNC + 1  # +1 for ellipsis


def test_format_resource_handles_missing_fields():
    r = ckan.format_resource({"id": "uuid"})
    assert r["id"] == "uuid"
    assert r["name"] is None
    assert r["format"] is None


def test_format_organization_short_strips_description():
    o = ckan.format_organization(
        {
            "id": "org1",
            "name": "minhac",
            "title": "Min. Hacienda",
            "description": "x" * 5000,
            "package_count": 12,
        },
        short=True,
    )
    assert "description" not in o
    assert o["dataset_count"] == 12


def test_format_organization_full_truncates_description():
    o = ckan.format_organization(
        {
            "id": "org1",
            "name": "minhac",
            "description": "x" * 5000,
            "package_count": 12,
        },
        short=False,
    )
    assert "description" in o
    assert len(o["description"]) <= ckan.DESC_TRUNC + 1
