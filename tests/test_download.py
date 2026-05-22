"""Unit tests for download.py — encoding detection + capped streaming."""

from __future__ import annotations

import pytest

from datosgobdo_mcp import download


def test_detect_encoding_utf8():
    data = "ñ á é".encode("utf-8")
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
    data, truncated = await download.download_capped(
        "https://example.test/big.bin", max_bytes=1000
    )
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
