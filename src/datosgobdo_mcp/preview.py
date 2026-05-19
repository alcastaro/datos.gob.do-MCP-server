"""Preview de recursos: baja primeras N filas de CSV / XLSX / JSON.

DataStore no está instalado en datos.gob.do, así que parseamos del cliente.
Stream con tope de bytes para no descargar archivos gigantes.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import httpx

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_ROWS = 20
MAX_ROWS = 200
DOWNLOAD_TIMEOUT = 30.0
USER_AGENT = "datosgobdo-mcp/0.1 (MCP Server)"


async def _download_capped(url: str, max_bytes: int) -> tuple[bytes, bool]:
    """Baja URL con límite de bytes. Devuelve (bytes, truncated)."""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DOWNLOAD_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            buf = bytearray()
            truncated = False
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    truncated = True
                    break
            return bytes(buf[:max_bytes]), truncated


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _preview_csv(data: bytes, rows: int) -> dict[str, Any]:
    text = _decode_text(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    try:
        header = next(reader)
    except StopIteration:
        return {"format": "csv", "error": "Archivo CSV vacío"}
    out_rows: list[list[str]] = []
    for i, row in enumerate(reader):
        if i >= rows:
            break
        out_rows.append(row)
    return {
        "format": "csv",
        "delimiter": dialect.delimiter,
        "columns": header,
        "rows_returned": len(out_rows),
        "rows": out_rows,
    }


def _preview_xlsx(data: bytes, rows: int) -> dict[str, Any]:
    try:
        import openpyxl
    except ImportError:
        return {"format": "xlsx", "error": "openpyxl no instalado"}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        return {"format": "xlsx", "error": f"No se pudo abrir XLSX: {e}"}
    sheet = wb.active
    if sheet is None:
        return {"format": "xlsx", "error": "Workbook sin hojas"}
    iterator = sheet.iter_rows(values_only=True)
    try:
        header_row = next(iterator)
    except StopIteration:
        wb.close()
        return {"format": "xlsx", "error": "Hoja vacía"}
    header = [str(c) if c is not None else "" for c in header_row]
    out_rows: list[list[Any]] = []
    for i, row in enumerate(iterator):
        if i >= rows:
            break
        out_rows.append([_jsonable(c) for c in row])
    sheets = wb.sheetnames
    wb.close()
    return {
        "format": "xlsx",
        "active_sheet": sheet.title,
        "all_sheets": sheets,
        "columns": header,
        "rows_returned": len(out_rows),
        "rows": out_rows,
    }


def _jsonable(v: Any) -> Any:
    """Convert openpyxl cell values (datetime etc) to JSON-friendly types."""
    import datetime

    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    return v


def _preview_json(data: bytes, rows: int) -> dict[str, Any]:
    text = _decode_text(data)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return {"format": "json", "error": f"JSON inválido: {e}"}
    if isinstance(obj, list):
        return {
            "format": "json-array",
            "total_items": len(obj),
            "rows_returned": min(rows, len(obj)),
            "rows": obj[:rows],
        }
    if isinstance(obj, dict):
        # Common shape: { data: [...], ... }
        for key in ("data", "results", "items", "records"):
            inner = obj.get(key)
            if isinstance(inner, list):
                return {
                    "format": "json-object",
                    "data_key": key,
                    "total_items": len(inner),
                    "rows_returned": min(rows, len(inner)),
                    "other_keys": [k for k in obj.keys() if k != key],
                    "rows": inner[:rows],
                }
        return {"format": "json-object", "keys": list(obj.keys()), "data": obj}
    return {"format": "json-scalar", "value": obj}


async def preview_resource_data(
    url: str,
    fmt: str | None,
    rows: int = DEFAULT_ROWS,
) -> dict[str, Any]:
    """Baja un recurso y devuelve preview tabular.

    Args:
        url: URL directa al archivo (de resource.url en CKAN).
        fmt: Formato declarado en CKAN (csv, xlsx, json, ods, pdf...).
        rows: Filas a devolver (cap MAX_ROWS).

    Returns:
        Dict con {format, rows, columns, ...} o {error}.
    """
    rows = min(max(int(rows), 1), MAX_ROWS)
    fmt_norm = (fmt or "").lower().strip().lstrip(".")

    if fmt_norm not in ("csv", "tsv", "xlsx", "xls", "xlsm", "json"):
        return {
            "error": f"Formato '{fmt}' no soportado para preview",
            "supported": ["CSV", "TSV", "XLSX", "JSON"],
            "hint": "Descarga manual desde la URL del recurso.",
        }

    try:
        data, truncated = await _download_capped(url, MAX_DOWNLOAD_BYTES)
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code} al bajar el recurso"}
    except httpx.HTTPError as e:
        return {"error": f"Error de red bajando recurso: {e}"}

    if fmt_norm in ("csv", "tsv"):
        out = _preview_csv(data, rows)
    elif fmt_norm in ("xlsx", "xls", "xlsm"):
        out = _preview_xlsx(data, rows)
    else:  # json
        out = _preview_json(data, rows)

    out["source_url"] = url
    out["bytes_downloaded"] = len(data)
    out["download_truncated"] = truncated
    return out
