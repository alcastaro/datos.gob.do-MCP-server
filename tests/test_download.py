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


def test_a_drive_share_link_becomes_the_download_address():
    """Five resources in the catalog are registered as the viewer page.

    That page is HTML, so they were counted as unreadable files. The catalog
    also registers four the other way — `uc?export=download&id=` — and those
    read fine, which is what makes this a normalisation and not a workaround:
    the target is the form the publisher uses when they get it right.
    """
    got = download.direct_download_url(
        "https://drive.google.com/file/d/1qVjNMHbgo8uABOhX_TZ8aKtHpUv6jAGT/view"
    )
    assert got == (
        "https://drive.google.com/uc?export=download&id=1qVjNMHbgo8uABOhX_TZ8aKtHpUv6jAGT"
    )


def test_the_open_form_is_recognised_too():
    got = download.direct_download_url("https://drive.google.com/open?id=ABC1234567890")
    assert got.endswith("id=ABC1234567890")
    assert "export=download" in got


def test_an_address_already_serving_bytes_is_left_alone():
    url = "https://drive.google.com/uc?export=download&id=ABC1234567890"
    assert download.direct_download_url(url) == url


def test_anything_unrecognised_is_returned_untouched():
    """A rewrite that guessed would read a different document than was asked for."""
    for url in (
        "https://hacienda.gob.do/datos/nomina.csv",
        "https://drive.google.com/drive/folders/ABC1234567890",
        "",
    ):
        assert download.direct_download_url(url) == url


def test_google_gets_no_fetch_metadata_headers():
    """Its download endpoint answers 403 to Sec-Fetch-Site: cross-site.

    Omitting the headers is not the same as sending false ones. The true
    description of this request is a cross-site programmatic fetch; claiming
    navigate/document to get past a check would be a lie about it.
    """
    sent = download.headers_for("https://drive.google.com/uc?export=download&id=ABC1234567890")
    assert "Sec-Fetch-Site" not in sent
    assert sent["User-Agent"] == download.RESOURCE_HEADERS["User-Agent"]


def test_every_other_host_still_states_its_context():
    sent = download.headers_for("https://migracion.gob.do/x.xlsx")
    assert sent["Sec-Fetch-Site"] == "cross-site"


# ─── What the file actually is, whatever the catalog says ──────────────────────


def _zip_of(entries: dict[str, bytes], mimetype: bytes | None = None) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if mimetype is not None:
            z.writestr("mimetype", mimetype)
        for name, body in entries.items():
            z.writestr(name, body)
    return buf.getvalue()


def test_an_ods_declares_itself_and_is_not_guessed_to_be_xlsx(tmp_path):
    """`PK` is how both start, so the signature alone cannot separate them. An ODS
    registered as CSV used to be routed to `read_xlsx`, which answered "No
    [Content_Types].xml found in xlsx file" — about a file whose own name ended in
    `.ods`."""
    p = tmp_path / "x.bin"
    p.write_bytes(
        _zip_of(
            {"content.xml": b"<x/>"},
            mimetype=b"application/vnd.oasis.opendocument.spreadsheet",
        )
    )
    assert download.sniff_container(p) == ("ods", None)


def test_a_workbook_part_makes_it_an_xlsx(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(_zip_of({"xl/workbook.xml": b"<w/>", "[Content_Types].xml": b"<c/>"}))
    assert download.sniff_container(p) == ("xlsx", None)


def test_a_zip_holding_one_data_file_is_an_archive(tmp_path):
    """Three MIVHED resources are declared JSON and are a zipped-up `.json`. Read
    as a spreadsheet they produced a message about a missing workbook part; what
    they need is unpacking."""
    p = tmp_path / "x.bin"
    p.write_bytes(_zip_of({"listado.json": b'[{"a": 1}]'}))
    assert download.sniff_container(p) == ("zip:listado.json", None)


def test_a_zip_of_several_data_files_is_not_unpacked(tmp_path):
    """Which of the two is 'the data' is not something to guess."""
    p = tmp_path / "x.bin"
    p.write_bytes(_zip_of({"a.csv": b"x\n1", "b.csv": b"y\n2"}))
    assert download.sniff_container(p) == (None, None)


def test_a_legacy_xls_is_refused_in_terms_of_the_file(tmp_path):
    """`d0cf11e0` is OLE2, the pre-2007 container. `read_xlsx` cannot read BIFF and
    complains about a missing zip member, which sounds like corruption."""
    p = tmp_path / "old.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    fmt, refusal = download.sniff_container(p)
    assert fmt is None
    assert refusal is not None
    assert "pre-2007" in refusal and "XLSX" in refusal


def test_a_truncated_zip_says_so(tmp_path):
    p = tmp_path / "cut.xlsx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    fmt, refusal = download.sniff_container(p)
    assert fmt is None
    assert refusal is not None
    assert "truncated" in refusal


def test_plain_text_is_left_unidentified(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes(b"a,b\n1,2\n")
    assert download.sniff_container(p) == (None, None)


@pytest.mark.parametrize(
    "head,expected",
    [
        (b"Provincia,Cantidad,Mes\r\nDN,12347,octubre\r\n", "csv"),
        (b"a;b;c\n1;2;3\n", "csv"),
        (b'[{"a": 1}]', "json"),
        (b'\xef\xbb\xbf{"a": 1}', "json"),
        (b"just one sentence with no delimiter at all", None),
        (b"", None),
    ],
)
def test_looks_like_text_table(head, expected):
    assert download.looks_like_text_table(head) == expected


def test_markup_is_never_taken_for_a_csv():
    """The dangerous half of the text sniff. A page served under an `.ods` name
    that does not start with one of the recognised HTML markers reaches this
    function, and a minified head almost always carries a comma:
    `content="width=device-width, initial-scale=1.0"`. Calling that CSV hands the
    model markup as data, with a correction note asserting the bytes are CSV —
    worse than the refusal it would replace."""
    minified = (
        b'<br /><b>Warning</b>: session_start() failed<meta name="viewport" '
        b'content="width=device-width, initial-scale=1.0"><title>Acceso</title>'
    )
    assert download.looks_like_text_table(minified) is None
