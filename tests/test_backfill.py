"""Synthetic official-shaped fixtures; never seed published research data."""

import gzip
import hashlib
import json
from datetime import date

import pytest
import requests

from taiwan_stock_ai.backfill import backfill, historical_url, normalize_history
from taiwan_stock_ai.providers import ENDPOINTS


DAY = date(2026, 1, 2)
COMPANIES = [{"公司代號": "1234", "公司簡稱": "合成測試", "產業別": "24", "出表日期": "1150105"}]


def twse(day="20260102"):
    return {"date": day, "tables": [{"fields": ["證券代號", "證券名稱", "開盤價", "最高價", "最低價",
                                                   "收盤價", "成交股數", "成交金額"],
                                     "data": [["1234", "合成測試", "20", "22", "19", "21", "1000", "21000"]]}]}


def tpex(day="20260102"):
    return {"date": day, "tables": [{"date": "115/01/02", "fields": ["代號", "名稱", "開盤", "最高", "最低",
                                                   "收盤", "成交股數", "成交金額(元)"],
                                     "data": [["1234", "合成測試", "30", "32", "29", "31", "2000", "62000"]]}]}


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload, ensure_ascii=False).encode()

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    def __init__(self, overrides=None):
        self.urls = []
        self.overrides = overrides or {}

    def get(self, url, **_):
        self.urls.append(url)
        if url in self.overrides:
            value = self.overrides[url]
            if isinstance(value, Exception):
                raise value
            return Response(value)
        if url in (ENDPOINTS["twse_companies"], ENDPOINTS["tpex_companies"]):
            return Response(COMPANIES)
        return Response(twse() if "twse.com.tw/rwd" in url else tpex())


@pytest.fixture(autouse=True)
def no_real_wait(monkeypatch):
    monkeypatch.setattr("taiwan_stock_ai.backfill.time.sleep", lambda _: None)


def test_true_dates_units_and_compressed_originals(tmp_path):
    session = Session()
    progress = []
    batches = []
    result = backfill(DAY, DAY, tmp_path, session=session, progress=progress.append, on_batch=batches.append)
    assert len(result["quotes"]) == 2
    assert [q["market"] for q in result["quotes"]] == ["TWSE", "TPEX"]
    assert result["quotes"][1]["turnover"] == 62000
    assert result["quotes"][1]["volume"] == 2000
    assert all(q["date"] == "2026-01-02" for q in result["quotes"])
    assert all(q["universe_date"] == "2026-01-05" for q in result["quotes"])
    assert len(progress) == 2
    assert len(batches) == 2
    assert sum(len(batch) for batch in batches) == len(result["quotes"])
    for source in result["sources"]:
        original = gzip.decompress(__import__("pathlib").Path(source["raw_path"]).read_bytes())
        assert hashlib.sha256(original).hexdigest() == source["sha256"]
    manifest = next(tmp_path.glob("*/manifest.json"))
    assert json.loads(manifest.read_text())["purpose"] == "current_universe_indicator_warmup"


def test_wrong_date_rejected_not_relabelled(tmp_path):
    session = Session({historical_url("TWSE", DAY): twse("20260105"),
                       historical_url("TPEX", DAY): tpex("20260105")})
    result = backfill(DAY, DAY, tmp_path, session=session)
    assert result["quotes"] == []
    assert all(s["status"] == "waiting" for s in result["sources"][2:])


def test_conflicting_table_date_fails():
    payload = tpex()
    payload["tables"][0]["date"] = "115/01/03"
    with pytest.raises(ValueError, match="table date"):
        normalize_history(payload, "TPEX", DAY)


def test_missing_company_list_does_not_invent_universe(tmp_path):
    session = Session({ENDPOINTS["tpex_companies"]: []})
    result = backfill(DAY, DAY, tmp_path, session=session)
    assert len(result["quotes"]) == 1
    assert result["quotes"][0]["market"] == "TWSE"
    assert historical_url("TPEX", DAY) not in session.urls


def test_weekends_do_not_request_daily_quotes(tmp_path):
    session = Session()
    result = backfill(date(2026, 1, 3), date(2026, 1, 4), tmp_path, session=session)
    assert result["quotes"] == []
    assert len(session.urls) == 2


def test_holiday_empty_response_stays_waiting(tmp_path):
    session = Session({historical_url("TWSE", DAY): {"stat": "很抱歉，沒有符合條件的資料!"},
                       historical_url("TPEX", DAY): {"date": "", "tables": []}})
    result = backfill(DAY, DAY, tmp_path, session=session)
    assert not result["quotes"]
    assert not result["paused_markets"]
    assert all(s["accepted_rows"] == 0 for s in result["sources"][2:])


def test_circuit_pauses_market_after_three_transport_failures(tmp_path):
    overrides = {historical_url("TWSE", d): requests.exceptions.ProxyError("synthetic denied")
                 for d in [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]}
    session = Session(overrides)
    result = backfill(date(2026, 1, 5), date(2026, 1, 8), tmp_path, session=session)
    assert "TWSE" in result["paused_markets"]
    assert historical_url("TWSE", date(2026, 1, 8)) not in session.urls


@pytest.mark.parametrize("start,end,delay", [
    (date(2026, 1, 5), DAY, 1), (DAY, DAY, 0), (DAY, date(2028, 1, 1), 1),
])
def test_invalid_ranges_and_rate_rejected(tmp_path, start, end, delay):
    with pytest.raises(ValueError):
        backfill(start, end, tmp_path, delay, session=Session())
