from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import requests

from quantlab.data import exchange_security_lists as lists


class FakeResponse:
    def __init__(self, payload=None, content=b"raw", status_code=200, headers=None):
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.trust_env = True
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def sse_payload(code: str, *, page: int = 1, pages: int = 1, total: int = 1):
    return {
        "pageHelp": {"totalRecord": total, "totalPage": pages},
        "result": [{"A_STOCK_CODE": code, "SEC_NAME_CN": "测试证券", "LIST_DATE": "2020-01-02"}],
    }


def fake_store(directory: Path, filename: str, content: bytes) -> dict[str, object]:
    return {"path": str(directory / filename), "original_filename": filename,
            "byte_count": len(content), "sha256": hashlib.sha256(content).hexdigest()}


class ExchangeSecurityListTests(unittest.TestCase):
    def test_sse_is_direct_paginated_and_saves_source_hash(self) -> None:
        session = FakeSession([FakeResponse(sse_payload("600000", pages=2, total=2), b"page1"),
                               FakeResponse(sse_payload("601000", page=2, pages=2, total=2), b"page2")])
        with patch.object(lists, "_store_bytes", side_effect=fake_store):
            result = lists.fetch_sse_list(
                "sse_main_board_a", raw_dir=Path("raw"), snapshot_time="2026-10-05T00:00:00+00:00",
                session_factory=lambda: session, retries=1, request_interval=0, sleeper=lambda _: None,
            )
            self.assertEqual(result.page_count, 2)
            self.assertEqual(result.records["symbol"].tolist(), ["600000.SH", "601000.SH"])
            self.assertEqual(result.returned_fields, ("A_STOCK_CODE", "LIST_DATE", "SEC_NAME_CN"))
            self.assertFalse(session.trust_env)
            self.assertTrue(session.closed)
            self.assertEqual(len(session.calls), 2)
            self.assertEqual(session.calls[1][1]["params"]["pageHelp.pageNo"], "2")
            self.assertEqual(result.source_files[0]["sha256"], hashlib.sha256(b"page1").hexdigest())
            self.assertEqual(result.source_files[0]["request_params"]["STOCK_TYPE"], "1")

    def test_sse_requires_verifiable_pagination_totals(self) -> None:
        payload = {"result": [{"A_STOCK_CODE": "600000", "SEC_NAME_CN": "测试", "LIST_DATE": "2020-01-01"}]}
        session = FakeSession([FakeResponse(payload)])
        with patch.object(lists, "_store_bytes", side_effect=fake_store), \
                self.assertRaisesRegex(lists.ExchangeListError, "lacks a verifiable"):
            lists.fetch_sse_list("sse_main_board_a", raw_dir=Path("raw"), snapshot_time="snapshot",
                                 session_factory=lambda: session, retries=1, sleeper=lambda _: None)

    def test_exchange_mapping_and_duplicate_codes_are_checked(self) -> None:
        row = {"A_STOCK_CODE": "000001", "SEC_NAME_CN": "平安银行", "LIST_DATE": "1991-04-03"}
        with self.assertRaisesRegex(lists.ExchangeListError, "invalid exchange mapping"):
            lists._normalize_sse_row(row, "sse_main_board_a", "SSE", "snapshot")

        session = FakeSession([FakeResponse({"pageHelp": {"totalRecord": 2, "totalPage": 1}, "result": [
            {"A_STOCK_CODE": "600000", "SEC_NAME_CN": "A", "LIST_DATE": "2000-01-01"},
            {"A_STOCK_CODE": "600000", "SEC_NAME_CN": "A duplicate", "LIST_DATE": "2000-01-01"},
        ]})])
        with patch.object(lists, "_store_bytes", side_effect=fake_store), \
                self.assertRaisesRegex(lists.ExchangeListError, "duplicate"):
            lists.fetch_sse_list("sse_main_board_a", raw_dir=Path("raw"), snapshot_time="snapshot",
                                 session_factory=lambda: session, retries=1, sleeper=lambda _: None)

    def test_szse_leading_zero_normalization_and_duplicate_rejection(self) -> None:
        with patch.object(lists.pd, "read_excel", return_value=pd.DataFrame({"A股代码": [1], "A股简称": ["平安"]})):
            frame, fields = lists._normalize_szse_frame(pd.DataFrame({"A股代码": [1], "A股简称": ["平安"]}), "snapshot")
        self.assertEqual(frame.iloc[0]["symbol"], "000001.SZ")
        self.assertEqual(set(fields), {"A股代码", "A股简称"})
        current_source_row = pd.DataFrame({"板块": ["创业板"], "A股代码": ["302132"],
                                           "A股简称": ["中航成飞"], "A股上市日期": ["2010-08-27"],
                                           "所属行业": ["C 制造业"]})
        current, _ = lists._normalize_szse_frame(current_source_row, "snapshot")
        self.assertEqual(current.loc[0, "symbol"], "302132.SZ")
        self.assertEqual(current.loc[0, "security_type"], "A_share_growth_enterprise_market")
        self.assertEqual(current.loc[0, "industry"], "C 制造业")
        with self.assertRaisesRegex(lists.ExchangeListError, "unrecognized A-share board"):
            lists._normalize_szse_frame(pd.DataFrame({"板块": ["未知板块"], "A股代码": ["000001"],
                                                      "A股简称": ["测试"]}), "snapshot")
        with self.assertRaisesRegex(lists.ExchangeListError, "duplicate"):
            lists._normalize_szse_frame(pd.DataFrame({"A股代码": [1, "000001"], "A股简称": ["平安", "平安"]}), "snapshot")

    def test_denied_response_is_not_retried_and_error_is_sanitized(self) -> None:
        session = FakeSession([FakeResponse(status_code=403)])
        with self.assertRaisesRegex(lists.ExchangeListError, r"refused access \(HTTP 403\)"):
            lists._request(session, "https://example.invalid", params={}, headers={}, timeout=(1, 1),
                           retries=2, retry_delay=0, sleeper=lambda _: None)
        self.assertEqual(len(session.calls), 1)

    def test_retry_count_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            lists._request(FakeSession([]), "https://example.invalid", params={}, headers={}, timeout=(1, 1),
                           retries=3, retry_delay=0, sleeper=lambda _: None)


if __name__ == "__main__":
    unittest.main()
