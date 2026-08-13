"""Resolving a page back to the file it should have been.

Every fixture here is a shape taken from the 2026-08-08 census of the whole
datos.gob.do catalog, where 37 of 1,056 resources answered with a page.
"""

from __future__ import annotations

import pytest

from datosgobdo_mcp import analytics, pagelink

SENASA = """
<html><body>
<a href="/transparencia/?wpdmpro=cartera-de-afiliados-regimen-subsidiado-senasa-noviembre-2025">Descargar</a>
<a href="/transparencia/?wpdmpro=nomina-de-empleados-fijos-2025">Nómina</a>
<a href="/contacto">Contacto</a>
</body></html>
"""

# The same file published three times, one per format — the commonest shape on
# these pages, and the reason the declared format has to outweigh name matching.
TRES_FORMATOS = """
<html><body>
<a href="/uploads/Titulos-entregados-DGBN-2016-2025.csv">Descargar</a>
<a href="/uploads/Titulos-entregados-DGBN-2016-2025.ods">Descargar</a>
<a href="/uploads/Titulos-entregados-DGBN-2016-2025.xlsx">Descargar</a>
</body></html>
"""

# Three genuinely different files, same format, names that say nothing. This is
# what real ambiguity looks like, and DGBN publishes exactly this.
DGBN = """
<html><body>
<a href="/wp-content/uploads/2024/05/clss.csv">Descargar</a>
<a href="/wp-content/uploads/2024/05/xls.csv">Descargar</a>
<a href="/wp-content/uploads/2024/05/otro.csv">Descargar</a>
</body></html>
"""


def test_navigation_links_are_not_candidates():
    found = pagelink.candidates(SENASA, "https://senasa.test/x?wpdmpro=algo")
    assert all("contacto" not in c["url"] for c in found)
    assert len(found) == 2


def test_the_requested_url_names_the_resource_it_wanted():
    """The hint comes from the request, not from a second API call.

    Measured over the twelve real cases: the URL-derived hint resolves 6, the
    CKAN resource name only 4. The cheaper signal is also the better one.
    """
    url = (
        "https://senasa.test/transparencia/"
        "?wpdmpro=cartera-de-afiliados-regimen-subsidiado-senasa-noviembre-2025"
    )
    target, found = pagelink.resolve(SENASA, url, "csv")
    assert target is not None
    assert "cartera-de-afiliados" in target
    assert found[0]["score"] > found[1]["score"]


def test_the_declared_format_separates_the_same_file_published_three_times():
    """The commonest real shape, and the one that took a live page to see.

    These portals publish one file as `.csv`, `.ods` and `.xlsx`. The three
    names are identical, so they score within 0.001 of each other and no amount
    of name matching will ever separate them. It does not have to: the caller
    already said which format the resource is registered as.
    """
    url = "https://bn.test/datos-abiertos/titulos-entregados-dgbn/2025/"
    target, found = pagelink.resolve(TRES_FORMATOS, url, "ods")
    assert target is not None and target.endswith(".ods")
    assert len(found) == 3


def test_meaningless_filenames_of_one_format_are_handed_back_not_guessed():
    """DGBN publishes files named `clss.csv` and `xls.csv`.

    No scoring function will match `Inventario de Almacén` to `clss.csv`, and
    the format cannot break the tie when every candidate shares it. Inventing a
    winner would put a plausible wrong table in front of someone with no way to
    check it.
    """
    url = "https://bn.test/datos-abiertos/inventario-de-almacen-dgbn-2018-2025"
    target, found = pagelink.resolve(DGBN, url, "csv")
    assert target is None
    assert len(found) == 3  # the caller still gets every option


def test_a_single_candidate_needs_no_margin():
    page = '<html><a href="/files/nomina.csv">x</a><a href="/inicio">home</a></html>'
    target, found = pagelink.resolve(page, "https://x.test/otra-cosa", "csv")
    assert target == "https://x.test/files/nomina.csv"
    assert len(found) == 1


def test_relative_links_resolve_against_the_page():
    page = '<html><a href="../data/serie.csv">s</a></html>'
    target, _ = pagelink.resolve(page, "https://x.test/a/b/serie", "csv")
    assert target == "https://x.test/a/data/serie.csv"


def test_hrefs_inside_scripts_do_not_become_candidates():
    """A regex over href= was the first attempt and matched commented markup."""
    page = (
        "<html><body><!-- <a href='/old/borrado.csv'>viejo</a> -->"
        '<a href="/data/vigente.csv">actual</a></body></html>'
    )
    found = pagelink.candidates(page, "https://x.test/vigente")
    assert [c["url"] for c in found] == ["https://x.test/data/vigente.csv"]


@pytest.mark.parametrize(
    "page,expected",
    [
        ('<html><input type="password"></html>', "login"),
        ("<html><table><tr><td>1</td></tr></table></html>", "table"),
        ("<html><body>Nada por aquí</body></html>", "no data file"),
    ],
)
def test_the_message_says_which_of_the_four_it_is(page, expected):
    """Thirty-seven resources used to share one message.

    They are four situations and the reader's next move differs in each: a page
    with no file is a broken publication to report to the institution, a login
    means the data is not actually open, a table means the data is there in
    another shape.
    """
    assert expected in pagelink.describe(page).lower()


# ─── end to end, over the real load path ──────────────────────────────────────


async def test_a_download_page_resolves_to_its_file(httpx_mock, tmp_cache_dir):
    page_url = "https://example.test/descargas/nomina-empleados-2025"
    file_url = "https://example.test/files/nomina-empleados-2025.csv"
    page = f'<html><a href="{file_url}">Descargar</a><a href="/otro.csv">Otro</a></html>'
    httpx_mock.add_response(url=page_url, method="HEAD", headers={"etag": "p1"})
    httpx_mock.add_response(url=page_url, method="GET", content=page.encode())
    httpx_mock.add_response(
        url=file_url, method="GET", content=b"Empleado;Sueldo\nAna;100\nLuis;200\n"
    )
    out = await analytics.get_resource_schema(page_url, "csv")
    assert "error" not in out, out
    assert out["row_count"] == 2


async def test_following_a_link_is_declared_in_the_reply(httpx_mock, tmp_cache_dir):
    """The caller asked for one URL and got data from another.

    Staying silent about that would break the trail an audit depends on.
    """
    page_url = "https://example.test/descargas/serie-precios-2025"
    file_url = "https://example.test/files/serie-precios-2025.csv"
    page = f'<html><a href="{file_url}">Descargar</a><a href="/x.csv">x</a></html>'
    httpx_mock.add_response(url=page_url, method="HEAD", headers={"etag": "p2"})
    httpx_mock.add_response(url=page_url, method="GET", content=page.encode())
    httpx_mock.add_response(url=file_url, method="GET", content=b"a;b\n1;2\n")
    out = await analytics.summarize_resource(page_url, "csv")
    assert out["cache"]["resolved_from"] == {"page": page_url, "followed": file_url}


async def test_an_ambiguous_page_returns_the_choice_not_a_dead_end(httpx_mock, tmp_cache_dir):
    """The caller is an assistant holding the user's question.

    It will choose between three files better than a string-similarity ratio
    can, so the server's job is to stop being a full stop.
    """
    page_url = "https://example.test/datos-abiertos/inventario"
    httpx_mock.add_response(url=page_url, method="HEAD", headers={"etag": "p3"})
    httpx_mock.add_response(url=page_url, method="GET", content=DGBN.encode())
    out = await analytics.get_resource_schema(page_url, "csv")
    assert "error" in out
    assert len(out["linked_files"]) == 3
    assert all(c["url"].startswith("https://example.test/") for c in out["linked_files"])
    assert "next_step" in out


async def test_a_page_linking_another_page_stops_after_one_hop(httpx_mock, tmp_cache_dir):
    """Following the chain would eventually walk the site's navigation into
    something that merely parses."""
    page_url = "https://example.test/descargas/informe-anual-2025"
    second = "https://example.test/files/informe-anual-2025.csv"
    httpx_mock.add_response(url=page_url, method="HEAD", headers={"etag": "p4"})
    httpx_mock.add_response(
        url=page_url, method="GET", content=f'<html><a href="{second}">d</a></html>'.encode()
    )
    httpx_mock.add_response(url=second, method="GET", content=b"<html><body>otra</body></html>")
    out = await analytics.get_resource_schema(page_url, "csv")
    assert "error" in out
    assert "one hop" in out["error"].lower()


async def test_the_second_call_still_says_where_the_data_came_from(httpx_mock, tmp_cache_dir):
    """Provenance that only the download path reports is provenance heard once.

    The warm path serves every call but the first. A caller that asks twice and
    is told about the substitution once has no way to know, on the reply it
    actually quotes, that it is holding data from a different URL.
    """
    page_url = "https://example.test/descargas/padron-2026"
    file_url = "https://example.test/files/padron-2026.csv"
    page = f'<html><a href="{file_url}">Descargar</a><a href="/z.csv">z</a></html>'
    httpx_mock.add_response(url=page_url, method="HEAD", headers={"etag": "p3"})
    httpx_mock.add_response(url=page_url, method="GET", content=page.encode())
    httpx_mock.add_response(url=file_url, method="GET", content=b"a;b\n1;2\n")

    first = await analytics.get_resource_schema(page_url, "csv")
    assert first["cache"]["cache"] == "miss"
    assert first["cache"]["resolved_from"] == {"page": page_url, "followed": file_url}

    second = await analytics.get_resource_schema(page_url, "csv")
    assert second["cache"]["cache"] == "hit", "expected the warm path"
    assert second["cache"]["resolved_from"] == {"page": page_url, "followed": file_url}


def test_an_embedded_file_counts_as_a_link():
    """A page that embeds the file instead of linking it still names it."""
    html = (
        '<html><iframe src="https://drive.google.com/uc?export=download&id=ABC123"></iframe></html>'
    )
    target, found = pagelink.resolve(html, "https://portal.test/datos/nomina-2026", "csv")
    assert found, "the iframe src must be seen"
    assert "ABC123" in found[0]["url"]


def test_a_page_offering_only_other_formats_says_so():
    """ "No data file" would be false, and the caller can judge a PDF better."""
    html = '<html><a href="/informe-2026.pdf">Informe</a></html>'
    target, found = pagelink.resolve(html, "https://portal.test/datos/informe-2026", "csv")
    assert target is None
    assert found and found[0]["url"].endswith(".pdf")
    assert "not the declared format" in found[0]["note"]


def test_a_section_page_is_told_apart_from_a_dead_link():
    """Sixteen pages measured: hundreds of anchors, no file, list built by the browser."""
    html = "<html>" + '<a href="/x">n</a>' * 150 + "<script>admin-ajax.php</script></html>"
    assert "browser" in pagelink.describe(html)
    assert "hops" in pagelink.describe(html)


def test_a_plain_dead_link_keeps_its_own_wording():
    assert "dead or moved" in pagelink.describe("<html><p>nada</p></html>")


def test_an_embed_with_no_file_id_is_not_a_candidate():
    """One page embeds drive.google.com/file/d//preview — the id is missing.

    Accepting it turned "no candidate" into a confident answer pointing at
    nothing, which is worse than no answer.
    """
    html = (
        '<html><iframe src="https://drive.google.com/file/d//preview?rm=minimal"></iframe></html>'
    )
    target, found = pagelink.resolve(html, "https://portal.test/datos/actos-2024", "ods")
    assert target is None
    assert not [c for c in found if "/file/d//" in c["url"]]


# ─── URLs a page navigates to instead of linking ──────────────────────────────

# Shape taken verbatim from tribunalconstitucional.gob.do, which publishes its
# three formats this way and so appeared to link no data file at all.
_TC_PAGE = """
<html><body>
  <div class="file-block" onclick="window.location.assign('https://store.test/media/69475/nomina-ogtic-datos-abiertos-ene-2022-jun-2026.csv')">
    <table><tbody><tr><td><ul><li>Formato: csv</li><li>Tamaño: 3.54 MB</li></ul></td></tr></tbody></table>
    <p>DESCARGAR</p>
  </div>
  <div class="file-block" onclick="window.location.assign('https://store.test/media/69476/nomina-ogtic-datos-abiertos-ene-2022-jun-2026.ods')">
    <p>DESCARGAR</p>
  </div>
  <div class="file-block" onclick="window.location.assign('https://store.test/media/69477/nomina-ogtic-datos-abiertos-ene-2022-jun-2026.xlsx')">
    <p>DESCARGAR</p>
  </div>
</body></html>
"""

_PAGE_URL = "https://portal.test/transparencia/datos-abiertos/nómina/2022-2026/"


@pytest.mark.parametrize("fmt,expected", [("csv", "csv"), ("ods", "ods"), ("xlsx", "xlsx")])
def test_a_file_opened_from_onclick_is_a_candidate(fmt, expected):
    """Three formats of the same table, told apart only by the declared format —
    the same tie-break the `href` case already relied on."""
    target, found = pagelink.resolve(_TC_PAGE, _PAGE_URL, fmt)
    assert target is not None, found
    assert target.endswith("." + expected)
    assert len(found) == 3


@pytest.mark.parametrize(
    "handler",
    [
        "window.location.assign('https://store.test/a/nomina.csv')",
        "window.location.replace(&quot;https://store.test/a/nomina.csv&quot;)",
        "window.open('https://store.test/a/nomina.csv')",
        "location.href='https://store.test/a/nomina.csv'",
        "window.location = 'https://store.test/a/nomina.csv'",
    ],
)
def test_every_spelling_of_the_navigation_call(handler):
    """Including the escaped double quotes a page must use inside a double-quoted
    attribute — the parser resolves the entity before the pattern ever sees it."""
    html = f'<html><body><div onclick="{handler}">DESCARGAR</div></body></html>'
    target, _ = pagelink.resolve(html, "https://portal.test/datos/nomina/", "csv")
    assert target == "https://store.test/a/nomina.csv"


def test_a_data_attribute_holding_the_url_counts_too():
    html = '<html><body><button data-href="/files/nomina-2026.csv">Bajar</button></body></html>'
    target, _ = pagelink.resolve(html, "https://portal.test/datos/nomina-2026/", "csv")
    assert target == "https://portal.test/files/nomina-2026.csv"


def test_an_onclick_that_navigates_nowhere_useful_is_not_a_candidate():
    """The extraction is not a licence to accept anything in a handler: the same
    filter applies as to an `href`, so a script that toggles a menu adds nothing."""
    html = (
        "<html><body>"
        "<div onclick=\"window.location.assign('/transparencia/index.php?view=category')\">Ver</div>"
        "<div onclick=\"toggleMenu('open')\">Menú</div>"
        "</body></html>"
    )
    target, found = pagelink.resolve(html, "https://portal.test/datos/nomina/", "csv")
    assert target is None
    assert found == []
