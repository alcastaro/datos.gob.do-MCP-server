"""Unit tests for download.py — encoding detection + capped streaming."""

from __future__ import annotations

import pytest

from datosgobdo_mcp import download


def test_detect_encoding_utf8():
    data = "ñ á é".encode()
    assert download._detect_encoding(data) == "utf-8"


def test_detect_encoding_latin1():
    data = "ñ á é".encode("latin-1")
    # chardet may return 'windows-1252' or 'iso-8859-1'; both normalize to cp1252
    assert download._detect_encoding(data) in ("cp1252", "iso-8859-1")


def test_detect_encoding_empty_bytes_defaults_to_utf8():
    assert download._detect_encoding(b"") == "utf-8"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CSV", "csv"),
        ("csv", "csv"),
        (".CSV", "csv"),
        (" .Xlsx ", "xlsx"),
        ("ODS", "ods"),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_format(raw, expected):
    assert download.normalize_format(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CSV", "csv"),
        ("xlsx", "xlsx"),
        ("JSON", "json"),
        ("ODS", "ods"),
        ("pdf", None),
        ("", None),
        (None, None),
    ],
)
def test_classify_format(raw, expected):
    assert download.classify_format(raw) == expected


async def test_download_capped_stops_at_max_bytes(httpx_mock):
    httpx_mock.add_response(
        url="https://example.test/big.bin",
        content=b"x" * 10_000,
    )
    data, truncated = await download.download_capped("https://example.test/big.bin", max_bytes=1000)
    assert len(data) <= 1000
    assert truncated is True


async def test_download_capped_under_limit_not_truncated(httpx_mock):
    httpx_mock.add_response(
        url="https://example.test/small.bin",
        content=b"x" * 500,
    )
    data, truncated = await download.download_capped(
        "https://example.test/small.bin", max_bytes=1000
    )
    assert len(data) == 500
    assert truncated is False


async def test_download_to_file_respects_cap(httpx_mock, tmp_path):
    httpx_mock.add_response(
        url="https://example.test/file.bin",
        content=b"y" * 10_000,
    )
    dest = tmp_path / "out.bin"
    n, truncated = await download.download_to_file(
        "https://example.test/file.bin", dest, max_bytes=2000
    )
    assert n == 2000
    assert dest.stat().st_size == 2000
    assert truncated is True


# ─── err_text ─────────────────────────────────────────────────────────────────


def test_err_text_falls_back_to_class_name_when_message_empty():
    """Real ConnectTimeout instances from the catalog carry no message, which
    produced the error "Could not load resource:" with nothing after it."""
    import httpx

    assert download.err_text(httpx.ConnectTimeout("")) == "ConnectTimeout"


def test_err_text_uses_the_message_when_there_is_one():
    assert download.err_text(ValueError("  boom  ")) == "boom"


@pytest.mark.parametrize(
    "head,expected",
    [
        (b"<!DOCTYPE html>\n<html>", True),
        (b"  \n <html lang='es'>", True),
        (b"\xef\xbb\xbf<!doctype HTML>", True),
        (b"<body>hola</body>", True),
        (b"nombre;sueldo\nANA;100", False),
        (b'{"a": 1}', False),
        (b"", False),
        (b"PK\x03\x04", False),  # xlsx/ods are zip archives
    ],
)
def test_looks_like_html(head, expected):
    assert download.looks_like_html(head) is expected


# ─── Codepage ladder (v0.7.4) ────────────────────────────────────────────────


def test_detect_encoding_prefers_cp850_over_cp1252_for_dos_exports():
    """`Año` in CP850/CP437 is 41 A4 6F. Decoded as CP1252 it reads `A¤o`.

    Five files in the catalog sweep reached users that way, and chardet had
    guessed right — at 5% confidence, below the old 0.7 threshold, so the guess
    was thrown away in favour of a blind CP1252 fallback.
    """
    data = "Categoria;Cantidad;Mes;Año\nQuejas;4;octubre;2017\n".encode("cp850")
    enc = download._detect_encoding(data)
    assert data.decode(enc).splitlines()[0].endswith("Año")


def test_mojibake_score_prefers_the_decoding_that_recovers_spanish():
    """Scoring suspicious characters was the wrong question: every candidate
    renders the same bytes as some odd symbol, and one character repeated 132
    times in a body outvoted the header. What separates a right decoding from a
    wrong one is how much Spanish it recovers."""
    data = "Sueldo Bruto;Año\n1000;2024\n".encode("cp850")
    assert download._mojibake_score(data.decode("cp850")) < download._mojibake_score(
        data.decode("cp1252")
    )


def test_detect_encoding_keeps_utf8_fast_path():
    assert download._detect_encoding("Año".encode()) == "utf-8"


def test_mojibake_score_penalises_dos_misreadings():
    """CP437 maps its high bytes to Greek letters and box drawing, so `Año`
    misread that way becomes `A±o` and `investigación` becomes `investigaci≤n`.
    Those characters cannot occur in this catalog and are the tell."""
    data = "Año;investigación;Préstamos\n".encode("cp1252")
    assert download._mojibake_score(data.decode("cp1252")) < download._mojibake_score(
        data.decode("cp437")
    )
    assert download._detect_encoding(data) == "cp1252"


def test_detect_encoding_ignores_implausible_low_confidence_guesses():
    """Latin-1 bytes decode without error as macroman, cp874 or cp424, and
    chardet volunteers those at single-digit confidence — so "it decoded" is no
    evidence. Only the codepages this catalog actually contains compete."""
    data = "Consulado;Cantidad;Mes;Año\n".encode("cp1252")
    assert download._detect_encoding(data) in download._CODEPAGE_LADDER


def test_a_repeated_body_character_cannot_outvote_the_header():
    """The failure this scorer was rewritten for: a real CP850 file whose body
    repeats one character 130+ times. Under the old occurrence-counting score
    that repetition alone chose the reading that turned `AÑO` into `A¥O`."""
    text = "MES;AÑO\n" + "".join(f"Mitad;{i}\n" for i in range(140))
    data = text.encode("cp850")
    assert download._detect_encoding(data) == "cp850"
