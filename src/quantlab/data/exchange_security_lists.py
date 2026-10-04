"""Direct, provenance-preserving downloads of public SSE/SZSE security lists.

This module is intentionally isolated from the market-data Providers. It opts
out of process proxy settings only for the three public exchange list endpoints
below and never changes global Requests or Windows network configuration.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import requests
from requests import Response, Session
from requests.exceptions import ConnectionError, HTTPError, Timeout

from quantlab.data.symbols import normalize_ashare_symbol


SECURITY_MASTER_SCHEMA_VERSION = "1.0"
SSE_API_URL = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SSE_SOURCE_URL = "https://www.sse.com.cn/assortment/stock/list/share/"
SZSE_API_URL = "https://www.szse.cn/api/report/ShowReport"
SZSE_SOURCE_URL = "https://www.szse.cn/market/product/stock/list/index.html"
SSE_LISTS = {
    "sse_main_board_a": {"stock_type": "1", "security_type": "A_share_main_board"},
    "sse_star_market": {"stock_type": "8", "security_type": "A_share_star_market"},
}
MAX_SSE_PAGES = 25
SSE_PAGE_SIZE = 10_000
MAX_ATTEMPTS = 2
DEFAULT_TIMEOUT = (5.0, 20.0)
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.75


class ExchangeListError(RuntimeError):
    """A source response was unavailable or failed list-integrity checks."""


@dataclass(frozen=True)
class ExchangeListResult:
    list_id: str
    exchange: str
    records: pd.DataFrame
    source_files: tuple[dict[str, object], ...]
    page_count: int
    returned_fields: tuple[str, ...]
    reported_row_count: int | None
    reported_page_count: int | None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_session(session_factory: Callable[[], Session]) -> Session:
    session = session_factory()
    # Deliberately local to this exchange-list request path only.
    session.trust_env = False
    return session


def _request(
    session: Session,
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str],
    timeout: tuple[float, float],
    retries: int,
    retry_delay: float,
    sleeper: Callable[[float], None],
) -> Response:
    if retries < 1 or retries > MAX_ATTEMPTS:
        raise ValueError(f"retries must be between 1 and {MAX_ATTEMPTS}.")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code in (401, 403, 429):
                raise ExchangeListError(f"Exchange endpoint refused access (HTTP {response.status_code}).")
            if response.status_code >= 500 and attempt < retries:
                sleeper(retry_delay)
                continue
            response.raise_for_status()
            return response
        except ExchangeListError:
            raise
        except HTTPError as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403, 429):
                raise ExchangeListError(f"Exchange endpoint refused access (HTTP {status}).") from None
            if status is None or status < 500 or attempt >= retries:
                break
        except (Timeout, ConnectionError) as exc:
            last_error = exc
            if attempt >= retries:
                break
        if attempt < retries:
            sleeper(retry_delay)
    raise ExchangeListError(f"Exchange request failed after {retries} bounded attempt(s) ({type(last_error).__name__}).") from None


def _store_bytes(directory: Path, filename: str, content: bytes) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as temp:
        temp.write(content)
        temp_path = Path(temp.name)
    temp_path.replace(target)
    return {
        "path": str(target.resolve()),
        "original_filename": None,
        "stored_filename": filename,
        "byte_count": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "downloaded_at": _utc_now().isoformat(),
    }


def _page_totals(payload: dict[str, object]) -> tuple[int | None, int | None]:
    page_help = payload.get("pageHelp")
    if not isinstance(page_help, dict):
        return None, None
    total_rows = None
    total_pages = None
    for key, value in page_help.items():
        normalized = re.sub(r"[^a-z]", "", str(key).lower())
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if normalized in {"total", "totalcount", "totalrecord", "totalrecords", "totalrows", "recordcount", "recordstotal"}:
            total_rows = number
        elif normalized in {"totalpage", "totalpages", "pagecount"}:
            total_pages = number
    return total_rows, total_pages


def _sse_page_rows(payload: object) -> tuple[list[dict[str, object]], dict[str, object], tuple[str, ...]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        raise ExchangeListError("SSE response schema is missing a top-level result list.")
    rows = payload["result"]
    if not all(isinstance(row, dict) for row in rows):
        raise ExchangeListError("SSE result contains a non-object row.")
    fields = tuple(sorted({str(key) for row in rows for key in row}))
    page_help = payload.get("pageHelp")
    return rows, page_help if isinstance(page_help, dict) else {}, fields


def _normalize_sse_row(row: dict[str, object], list_id: str, exchange: str, snapshot_time: str) -> dict[str, object]:
    code_key = "A_STOCK_CODE"
    required = (code_key, "SEC_NAME_CN", "LIST_DATE")
    missing = [key for key in required if key not in row]
    if missing:
        raise ExchangeListError(f"SSE response is missing required field(s): {', '.join(missing)}.")
    raw_code = str(row[code_key]).strip()
    if not re.fullmatch(r"\d{6}", raw_code):
        raise ExchangeListError("SSE returned a non-six-digit A-share code.")
    symbol = f"{raw_code}.{ 'SH' if exchange == 'SSE' else 'SZ' }"
    try:
        identity = normalize_ashare_symbol(symbol)
    except ValueError as exc:
        raise ExchangeListError(f"SSE list code {symbol} has invalid exchange mapping: {exc}") from None
    if identity.exchange != exchange:
        raise ExchangeListError(f"SSE list code {symbol} maps to a different exchange.")
    listing_date = pd.to_datetime(row.get("LIST_DATE"), errors="coerce")
    raw_delisting = row.get("DELIST_DATE")
    delisting_date = pd.to_datetime(raw_delisting, errors="coerce") if raw_delisting not in (None, "", "-") else pd.NaT
    return {
        "market": "CN_A", "symbol": identity.symbol, "exchange": exchange,
        "security_type": SSE_LISTS[list_id]["security_type"],
        "name": str(row["SEC_NAME_CN"]).strip() or None,
        "listing_date": listing_date.date().isoformat() if pd.notna(listing_date) else None,
        "delisting_date": delisting_date.date().isoformat() if pd.notna(delisting_date) else None,
        "industry": str(row.get("CSRC_CODE_DESC")).strip() or None if row.get("CSRC_CODE_DESC") not in (None, "", "-") else None,
        "metadata_source": "SSE public A-share list",
        "metadata_snapshot_time": snapshot_time,
        "validation_status": "source_fields_valid_optional_fields_unknown",
    }


def _sse_params(stock_type: str, page_no: int) -> dict[str, str]:
    return {
        "STOCK_TYPE": stock_type, "REG_PROVINCE": "", "CSRC_CODE": "", "STOCK_CODE": "",
        "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L", "COMPANY_STATUS": "2,4,5,7,8",
        "type": "inParams", "isPagination": "true", "pageHelp.cacheSize": "1",
        "pageHelp.beginPage": "1", "pageHelp.pageSize": str(SSE_PAGE_SIZE),
        "pageHelp.pageNo": str(page_no), "pageHelp.endPage": str(page_no),
    }


def fetch_sse_list(
    list_id: str,
    *,
    raw_dir: Path | str,
    snapshot_time: str,
    session_factory: Callable[[], Session] = requests.Session,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = MAX_ATTEMPTS,
    retry_delay: float = 1.0,
    request_interval: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> ExchangeListResult:
    if list_id not in SSE_LISTS:
        raise ValueError(f"Unsupported SSE list {list_id!r}.")
    session = _new_session(session_factory)
    saved_files: list[dict[str, object]] = []
    all_rows: list[dict[str, object]] = []
    all_fields: set[str] = set()
    expected_rows: int | None = None
    expected_pages: int | None = None
    try:
        for page_no in range(1, MAX_SSE_PAGES + 1):
            if page_no > 1:
                sleeper(request_interval)
            response = _request(
                session, SSE_API_URL, params=_sse_params(SSE_LISTS[list_id]["stock_type"], page_no),
                headers={"Referer": SSE_SOURCE_URL, "User-Agent": "QuantResearchAgent/0.1"},
                timeout=timeout, retries=retries, retry_delay=retry_delay, sleeper=sleeper,
            )
            content = response.content
            file = _store_bytes(Path(raw_dir), f"{list_id}_page_{page_no:03d}.json", content)
            file.update({"source_url": SSE_API_URL, "reference_url": SSE_SOURCE_URL, "page": page_no,
                         "request_params": _sse_params(SSE_LISTS[list_id]["stock_type"], page_no),
                         "content_type": response.headers.get("Content-Type")})
            saved_files.append(file)
            try:
                payload = response.json()
            except ValueError as exc:
                raise ExchangeListError("SSE response was not valid JSON.") from exc
            rows, page_help, fields = _sse_page_rows(payload)
            all_fields.update(fields)
            total_rows, total_pages = _page_totals({"pageHelp": page_help})
            if total_rows is not None:
                if expected_rows is not None and expected_rows != total_rows:
                    raise ExchangeListError("SSE pagination total changed between pages.")
                expected_rows = total_rows
            if total_pages is not None:
                if expected_pages is not None and expected_pages != total_pages:
                    raise ExchangeListError("SSE page count changed between pages.")
                expected_pages = total_pages
            all_rows.extend(rows)
            if expected_pages is not None and page_no >= expected_pages:
                break
            if expected_rows is not None and len(all_rows) >= expected_rows:
                break
            if expected_rows is None and expected_pages is None:
                raise ExchangeListError("SSE pagination response lacks a verifiable total row/page count.")
            if not rows:
                break
        else:
            raise ExchangeListError(f"SSE pagination exceeded the bounded {MAX_SSE_PAGES}-page limit.")
    finally:
        session.close()
    if expected_rows is not None and len(all_rows) != expected_rows:
        raise ExchangeListError(f"SSE returned {len(all_rows)} rows but pagination metadata reports {expected_rows}.")
    if expected_rows is None and expected_pages is None:
        raise ExchangeListError("SSE pagination response lacks a verifiable total row/page count.")
    if expected_pages is not None and len(saved_files) != expected_pages:
        raise ExchangeListError(f"SSE returned {len(saved_files)} pages but pagination metadata reports {expected_pages}.")
    records = [_normalize_sse_row(row, list_id, "SSE", snapshot_time) for row in all_rows]
    frame = pd.DataFrame(records)
    if frame.empty:
        raise ExchangeListError("SSE returned an empty list.")
    if frame["symbol"].duplicated().any():
        raise ExchangeListError("SSE list contains duplicate symbols.")
    return ExchangeListResult(list_id, "SSE", frame, tuple(saved_files), len(saved_files), tuple(sorted(all_fields)),
                              expected_rows, expected_pages)


def _find_column(columns: Iterable[object], *, exact: tuple[str, ...], contains: tuple[str, ...]) -> object | None:
    normalized = {str(column).strip().lower(): column for column in columns}
    for key in exact:
        if key.lower() in normalized:
            return normalized[key.lower()]
    for column in columns:
        value = str(column).strip().lower()
        if any(part.lower() in value for part in contains):
            return column
    return None


def _normalize_szse_frame(raw: pd.DataFrame, snapshot_time: str) -> tuple[pd.DataFrame, tuple[str, ...]]:
    if raw.empty:
        raise ExchangeListError("SZSE workbook contains no rows.")
    code_col = _find_column(raw.columns, exact=("A股代码", "A股代碼"), contains=("a股代码", "a股代碼"))
    name_col = _find_column(raw.columns, exact=("A股简称", "A股簡稱"), contains=("a股简称", "a股簡稱"))
    if code_col is None or name_col is None:
        raise ExchangeListError("SZSE workbook does not expose recognizable A-share code/name columns.")
    date_col = _find_column(raw.columns, exact=("A股上市日期",), contains=("a股上市日期", "a股上市时间"))
    industry_col = _find_column(raw.columns, exact=("所属行业",), contains=("所属行业",))
    board_col = _find_column(raw.columns, exact=("板块",), contains=("板块",))
    output = []
    for _, row in raw.iterrows():
        value = row[code_col]
        if pd.isna(value):
            continue
        code_text = str(value).strip()
        if re.fullmatch(r"\d+\.0+", code_text):
            code_text = code_text.split(".", 1)[0]
        if not re.fullmatch(r"\d{1,6}", code_text):
            raise ExchangeListError("SZSE workbook contains an invalid A-share code value.")
        code_text = code_text.zfill(6)
        try:
            identity = normalize_ashare_symbol(f"{code_text}.SZ")
        except ValueError as exc:
            raise ExchangeListError(f"SZSE official A-share code {code_text} failed exchange validation: {exc}") from None
        name = str(row[name_col]).strip()
        listing_date = pd.to_datetime(row[date_col], errors="coerce") if date_col is not None else pd.NaT
        board = str(row[board_col]).strip() if board_col is not None and pd.notna(row[board_col]) else ""
        if board and board not in {"主板", "创业板"}:
            raise ExchangeListError("SZSE workbook contains an unrecognized A-share board classification.")
        security_type = {"主板": "A_share_main_board", "创业板": "A_share_growth_enterprise_market"}.get(board, "A_share_common")
        output.append({
            "market": "CN_A", "symbol": identity.symbol, "exchange": "SZSE",
            "security_type": security_type, "name": name or None,
            "listing_date": listing_date.date().isoformat() if pd.notna(listing_date) else None,
            "delisting_date": None,
            "industry": str(row[industry_col]).strip() if industry_col is not None and pd.notna(row[industry_col]) else None,
            "metadata_source": "SZSE public A-share list",
            "metadata_snapshot_time": snapshot_time,
            "validation_status": "source_fields_valid_optional_fields_unknown",
        })
    frame = pd.DataFrame(output)
    if frame.empty or frame["symbol"].duplicated().any():
        raise ExchangeListError("SZSE normalized list is empty or contains duplicate symbols.")
    return frame, tuple(str(column) for column in raw.columns)


def fetch_szse_list(
    *,
    raw_dir: Path | str,
    snapshot_time: str,
    session_factory: Callable[[], Session] = requests.Session,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = MAX_ATTEMPTS,
    retry_delay: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> ExchangeListResult:
    session = _new_session(session_factory)
    try:
        response = _request(
            session, SZSE_API_URL,
            params={"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1", "random": "0"},
            headers={"Referer": SZSE_SOURCE_URL, "User-Agent": "QuantResearchAgent/0.1"},
            timeout=timeout, retries=retries, retry_delay=retry_delay, sleeper=sleeper,
        )
        content = response.content
        file = _store_bytes(Path(raw_dir), "szse_a_share_list.xlsx", content)
        file.update({"source_url": SZSE_API_URL, "reference_url": SZSE_SOURCE_URL,
                     "request_params": {"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1", "random": "0"},
                     "content_disposition": response.headers.get("Content-Disposition"),
                     "content_type": response.headers.get("Content-Type")})
        disposition = response.headers.get("Content-Disposition", "")
        filename_match = re.search(r"filename\s*=\s*\"?([^\";]+)", disposition, flags=re.IGNORECASE)
        if filename_match:
            source_filename = filename_match.group(1).strip()
            try:
                source_filename = source_filename.encode("latin-1").decode("gbk")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            file["original_filename"] = source_filename
    finally:
        session.close()
    try:
        raw = pd.read_excel(BytesIO(content), dtype=str)
    except Exception as exc:
        raise ExchangeListError(f"SZSE response was not a readable workbook ({type(exc).__name__}).") from None
    records, fields = _normalize_szse_frame(raw, snapshot_time)
    file.update({"row_count": len(records), "fields": list(fields)})
    return ExchangeListResult("szse_a_shares", "SZSE", records, (file,), 1, fields, len(records), 1)


def fetch_and_save_security_master(
    *,
    output_root: Path | str | None = None,
    session_factory: Callable[[], Session] = requests.Session,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = MAX_ATTEMPTS,
    retry_delay: float = 1.0,
    request_interval: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    root = Path(output_root) if output_root else _project_root() / "data"
    timestamp = _utc_now()
    snapshot_time = timestamp.isoformat()
    run_name = timestamp.strftime("%Y%m%dT%H%M%SZ")
    raw_dir = root / "raw" / "security_master" / run_name
    results: list[ExchangeListResult] = []
    failures: list[dict[str, str]] = []
    tasks: list[tuple[str, Callable[[], ExchangeListResult]]] = [
        ("sse_main_board_a", lambda: fetch_sse_list(
            "sse_main_board_a", raw_dir=raw_dir, snapshot_time=snapshot_time,
            session_factory=session_factory, timeout=timeout, retries=retries, retry_delay=retry_delay,
            request_interval=request_interval, sleeper=sleeper)),
        ("sse_star_market", lambda: fetch_sse_list(
            "sse_star_market", raw_dir=raw_dir, snapshot_time=snapshot_time,
            session_factory=session_factory, timeout=timeout, retries=retries, retry_delay=retry_delay,
            request_interval=request_interval, sleeper=sleeper)),
        ("szse_a_shares", lambda: fetch_szse_list(
            raw_dir=raw_dir, snapshot_time=snapshot_time, session_factory=session_factory,
            timeout=timeout, retries=retries, retry_delay=retry_delay, sleeper=sleeper)),
    ]
    for index, (list_id, fetch) in enumerate(tasks):
        if index:
            sleeper(request_interval)
        try:
            results.append(fetch())
        except Exception as exc:
            failures.append({"list_id": list_id, "error_type": type(exc).__name__, "message": str(exc)})

    manifest: dict[str, object] = {
        "schema_version": SECURITY_MASTER_SCHEMA_VERSION,
        "status": "metadata_source_ready" if not failures and len(results) == 3 else "metadata_access_still_blocked",
        "source_organization": ["Shanghai Stock Exchange", "Shenzhen Stock Exchange"],
        "metadata_snapshot_time": snapshot_time,
        "publication_date": None,
        "retrieved_at": snapshot_time,
        "trust_env": False,
        "list_results": [
            {"list_id": item.list_id, "exchange": item.exchange, "source_files": list(item.source_files),
             "fields": list(item.returned_fields), "page_count": item.page_count,
             "row_count": len(item.records), "reported_row_count": item.reported_row_count,
             "reported_page_count": item.reported_page_count, "duplicate_count": 0}
            for item in results
        ],
        "failures": failures,
    }
    if not failures and len(results) == 3:
        master = pd.concat([item.records for item in results], ignore_index=True)
        duplicate_mask = master["symbol"].duplicated(keep=False)
        if duplicate_mask.any():
            manifest["status"] = "metadata_access_still_blocked"
            manifest["failures"].append({"list_id": "combined", "error_type": "DuplicateSymbolError",
                                         "message": "Combined exchange lists contain duplicate symbols."})
        else:
            manifest["security_master_row_count"] = len(master)
            manifest["exchange_counts"] = master.groupby("exchange").size().astype(int).to_dict()
            manifest["source_sha256"] = hashlib.sha256(
                "".join(sorted(file["sha256"] for item in results for file in item.source_files)).encode("ascii")
            ).hexdigest()
            validated_dir = root / "validated" / "security_master"
            validated_dir.mkdir(parents=True, exist_ok=True)
            stem = f"security_master_{manifest['source_sha256'][:16]}"
            parquet_path = validated_dir / f"{stem}.parquet"
            temp_path = parquet_path.with_name(parquet_path.stem + ".tmp.parquet")
            master.to_parquet(temp_path, index=False)
            temp_path.replace(parquet_path)
            manifest["security_master_path"] = str(parquet_path.resolve())
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
