"""Synthetic parser fixtures only; these prices are never application/demo data."""
import hashlib
import json
from datetime import date

import pytest
import requests

from taiwan_stock_ai.providers import (
    ENDPOINTS, collect, collect_calendar, number, parse_calendar, parse_companies,
    parse_date, parse_fundamentals, parse_institutions, parse_market_calendar, parse_quotes, fetch_response,
)


COMPANIES = {"1234": {"name": "測試公司", "industry": "測試產業"}}
FETCHED = "2026-01-02T10:00:00+00:00"


def test_retry_transient_truncated_response_only_once(monkeypatch):
    monkeypatch.setattr("taiwan_stock_ai.providers.time.sleep", lambda _: None)
    class TruncatedSession:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise requests.exceptions.ChunkedEncodingError("synthetic truncated response")
            return FakeResponse([])
    session = TruncatedSession()
    assert fetch_response(session, "https://example.invalid").status_code == 200
    assert session.calls == 2


def test_access_denial_is_not_retried():
    class DeniedSession:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            raise requests.exceptions.ProxyError("synthetic 403")
    session = DeniedSession()
    with pytest.raises(requests.exceptions.ProxyError):
        fetch_response(session, "https://example.invalid")
    assert session.calls == 1


def quote(**changes):
    return {"Date": "1150102", "Code": "1234", "Name": "測試公司",
            "OpeningPrice": "20", "HighestPrice": "22", "LowestPrice": "19",
            "ClosingPrice": "21", "TradeVolume": "1,000", "TradeValue": "21,000", **changes}


@pytest.mark.parametrize("raw,expected", [
    ("1150102", "2026-01-02"), ("20260102", "2026-01-02"),
    ("115/01/02", "2026-01-02"), ("2026-1-2", "2026-01-02"),
    ("115年1月2日", "2026-01-02"), ("1150229", None), (None, None), ("01/02", None),
])
def test_official_dates_require_complete_calendar_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "--", "", "NaN", "Inf", True])
def test_unknown_numbers_remain_missing(raw):
    assert number(raw) is None


def test_company_membership_excludes_non_common_equity():
    companies = parse_companies([
        {"公司代號": "1234", "公司簡稱": "測試公司", "產業別": "24"},
        {"公司代號": "006208", "公司簡稱": "ETF"},
        {"公司代號": "1234A", "公司簡稱": "特別股"},
    ])
    assert list(companies) == ["1234"]
    assert companies["1234"]["industry"] == "半導體"


def test_live_tpex_company_schema_preserves_industry_for_scoring():
    # Actual official field names, with clearly synthetic company values.
    companies = parse_companies([{
        "Date": "1150102", "SecuritiesCompanyCode": "1234", "CompanyName": "合成測試公司",
        "CompanyAbbreviation": "合成測試", "SecuritiesIndustryCode": "24",
    }])
    assert companies["1234"]["industry"] == "半導體"
    assert companies["1234"]["name"] == "合成測試"
    assert companies["1234"]["source_date"] == "2026-01-02"


def test_prices_preserve_official_date_units_and_provenance():
    result, rejected = parse_quotes([quote()], "TWSE", COMPANIES, "https://example.invalid", FETCHED, date(2026, 1, 5))
    assert rejected == 0
    assert result[0]["date"] == "2026-01-02"  # not requested date
    assert result[0]["volume"] == 1000  # shares, not lots
    assert result[0]["turnover"] == 21000
    assert result[0]["fetched_at"] == FETCHED
    assert result[0]["complete"] is True


@pytest.mark.parametrize("change", [
    {"Date": ""}, {"Date": "1150103"}, {"ClosingPrice": "--"},
    {"LowestPrice": "23"}, {"HighestPrice": "18"}, {"TradeVolume": "-1"},
    {"TradeVolume": "1.5"}, {"TradeValue": "NaN"},
])
def test_invalid_or_future_quotes_are_rejected(change):
    result, rejected = parse_quotes([quote(**change)], "TWSE", COMPANIES, "source", FETCHED, date(2026, 1, 2))
    assert result == []
    assert rejected == 1


def test_tpex_explicit_aliases_and_company_scope():
    row = {"Date": "1150102", "SecuritiesCompanyCode": "1234", "CompanyName": "測試公司",
           "Open": "20", "High": "22", "Low": "19", "Close": "21",
           "TradingShares": "1000", "TransactionAmount": "21000"}
    result, rejected = parse_quotes([row, {**row, "SecuritiesCompanyCode": "9999"}], "TPEX", COMPANIES, "source", FETCHED, date(2026, 1, 2))
    assert len(result) == 1 and rejected == 0
    assert result[0]["market"] == "TPEX"


def test_undated_valuation_is_not_timestamped_using_quotes_or_today():
    result, rejected = parse_fundamentals([{"Code": "1234", "PEratio": "10"}], "TWSE", COMPANIES, "source", FETCHED, date(2026, 1, 2))
    assert result == [] and rejected == 1


def test_explicit_envelope_date_is_accepted_without_filling_missing_values():
    payload = {"date": "20260102", "data": [{"Code": "1234", "PEratio": "10", "PBratio": "--"}]}
    result, _ = parse_fundamentals(payload, "TWSE", COMPANIES, "source", FETCHED, date(2026, 1, 2))
    assert result[0]["date"] == "2026-01-02"
    assert result[0]["pb"] is None and result[0]["complete"] is False


@pytest.mark.parametrize("shape", ["tables", "data9"])
def test_twse_dated_quote_envelope_and_tables(shape):
    fields = ["證券代號", "證券名稱", "開盤價", "最高價", "最低價", "收盤價", "成交股數", "成交金額"]
    data = [["1234", "測試公司", "20", "22", "19", "21", "1000", "21000"]]
    if shape == "tables":
        payload = {"date": "20260102", "tables": [{"fields": ["指數"], "data": [["測試指數"]]}, {"fields": fields, "data": data}]}
    else:
        payload = {"date": "20260102", "fields9": fields, "data9": data}
    result, rejected = parse_quotes(payload, "TWSE", COMPANIES, "source", FETCHED, date(2026, 1, 5))
    assert result[0]["date"] == "2026-01-02" and rejected == 0


def test_tpex_yield_ratio_alias():
    payload = [{"Date": "1150102", "SecuritiesCompanyCode": "1234", "PriceEarningRatio": "10", "PriceBookRatio": "1.2", "YieldRatio": "3.5"}]
    result, _ = parse_fundamentals(payload, "TPEX", COMPANIES, "source", FETCHED, date(2026, 1, 2))
    assert result[0]["dividend_yield"] == 3.5


def test_dated_twse_fallback_is_used_when_openapi_omits_date(tmp_path):
    class DatedFallbackSession(FakeSession):
        def get(self, url, **kwargs):
            if url == ENDPOINTS["twse_quotes"]:
                row = quote()
                del row["Date"]
                return FakeResponse([row])
            if url == ENDPOINTS["twse_dated_quotes"]:
                row = quote()
                del row["Date"]
                return FakeResponse({"date": "20260102", "data": [row]})
            return super().get(url, **kwargs)
    result = collect(date(2026, 1, 2), tmp_path, session=DatedFallbackSession())
    assert len(result["quotes"]) == 1
    assert result["quotes"][0]["source"] == ENDPOINTS["twse_dated_quotes"]
    source = next(s for s in result["sources"] if s["id"] == "twse_quotes")
    assert source["status"] == "waiting" and source["rejected_rows"] == 1


@pytest.mark.parametrize("kind,field", [("quotes", "quotes"), ("valuations", "fundamentals")])
@pytest.mark.parametrize("new_date,expected", [("20260105", "dated"), ("20260102", "primary"),
                                             (None, "primary"), ("20260106", "primary")])
def test_freshest_verified_twse_source_wins_without_duplicate_rows(tmp_path, kind, field, new_date, expected):
    class FreshnessSession(FakeSession):
        def get(self, url, **kwargs):
            if url == ENDPOINTS[f"twse_{kind}"]:
                return FakeResponse([quote() if kind == "quotes" else {
                    "Code": "1234", "Date": "1150102", "PEratio": "10", "PBratio": "1", "DividendYield": "3"}])
            if url == ENDPOINTS[f"twse_dated_{kind}"]:
                row = quote() if kind == "quotes" else {
                    "Code": "1234", "PEratio": "10", "PBratio": "1", "DividendYield": "3"}
                row.pop("Date", None)
                return FakeResponse({"date": new_date, "data": [row]})
            return super().get(url, **kwargs)

    result = collect(date(2026, 1, 5), tmp_path, session=FreshnessSession())
    assert len(result[field]) == 1
    source_id = f"twse_dated_{kind}" if expected == "dated" else f"twse_{kind}"
    assert result[field][0]["source"] == ENDPOINTS[source_id]
    assert result[field][0]["date"] == ("2026-01-05" if expected == "dated" else "2026-01-02")
    primary = next(s for s in result["sources"] if s["id"] == f"twse_{kind}")
    assert primary["accepted_rows"] == 1


def test_calendar_does_not_infer_weekday_session_from_partial_holiday_list():
    payload = [{"Date": "20260101", "Name": "開國紀念日"}]
    assert parse_calendar(payload, date(2026, 1, 1), "source")["trading_day"] is False
    assert parse_calendar(payload, date(2026, 1, 2), "source")["trading_day"] is None


def test_calendar_distinguishes_festival_trading_dates_and_settlement_only():
    payload = [
        {"Name": "農曆春節前最後交易日", "Date": "1150211", "Description": "農曆春節前最後交易。"},
        {"Name": "市場無交易，僅辦理結算交割作業", "Date": "1150212", "Description": ""},
        {"Name": "農曆春節後開始交易日", "Date": "1150223", "Description": "農曆春節後開始交易。"},
        {"Name": "國慶日", "Date": "1151009", "Description": "於10月9日補假。"},
    ]
    assert parse_calendar(payload, date(2026, 2, 11), "source")["trading_day"] is True
    assert parse_calendar(payload, date(2026, 2, 12), "source")["trading_day"] is False
    assert parse_calendar(payload, date(2026, 2, 23), "source")["trading_day"] is True
    assert parse_calendar(payload, date(2026, 10, 9), "source")["trading_day"] is False


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload, ensure_ascii=False).encode()

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def get(self, url, **kwargs):
        assert kwargs["timeout"] == (5, 20)
        assert "verify" not in kwargs  # requests TLS verification remains enabled
        if url == ENDPOINTS["twse_companies"]:
            return FakeResponse([{"公司代號": "1234", "公司簡稱": "測試公司", "產業別": "24"}])
        if url == ENDPOINTS["twse_quotes"]:
            return FakeResponse([quote()])
        raise requests.exceptions.ProxyError("proxy https://private:credential@example.invalid HTTP 403")


def test_source_failures_preserve_other_data_and_raw_evidence(tmp_path):
    result = collect(date(2026, 1, 2), tmp_path, session=FakeSession())
    assert len(result["quotes"]) == 1
    assert result["calendar"]["trading_day"] is True
    assert result["institutions"] == []
    assert len(result["sources"]) == len(ENDPOINTS)
    assert "credential" not in json.dumps(result)
    source = next(s for s in result["sources"] if s["id"] == "twse_quotes")
    from pathlib import Path
    assert hashlib.sha256(Path(source["raw_path"]).read_bytes()).hexdigest() == source["sha256"]
    assert list(tmp_path.glob("*/manifest.json"))


def test_weekend_remains_nontrading_on_total_source_failure(tmp_path):
    class FailedSession:
        def get(self, *args, **kwargs):
            raise requests.Timeout("connection timeout")
    result = collect(date(2026, 1, 3), tmp_path, session=FailedSession())
    assert result["quotes"] == []
    assert result["calendar"]["trading_day"] is False
    assert all(s["status"] == "error" for s in result["sources"])


def institution(**changes):
    return {"SecuritiesCompanyCode": "1234", "Date": "1150102",
            "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "-1,200",
            "SecuritiesInvestmentTrustCompanies-Difference": "200",
            "Dealers-Difference": "0", "TotalDifference": "-1,000", **changes}


def test_official_institutional_shares_keep_date_sign_and_zero():
    records, rejected = parse_institutions([institution()], "TPEX", COMPANIES,
                                           ENDPOINTS["tpex_institutions"], FETCHED, date(2026, 1, 5))
    assert rejected == 0
    row = records[0]
    assert row["date"] == "2026-01-02"
    assert row["net_buy"] == -1000 and row["foreign_net"] == -1200
    assert row["investment_trust_net"] == 200 and row["dealer_net"] == 0
    assert row["unit"] == "shares" and row["complete"] is True


@pytest.mark.parametrize("changes", [{"Date": ""}, {"Date": "1150106"},
                                    {"TotalDifference": "--"}, {"Dealers-Difference": ""},
                                    {"TotalDifference": "0.5"}])
def test_missing_future_or_noninteger_institution_data_is_not_fabricated(changes):
    records, rejected = parse_institutions([institution(**changes)], "TPEX", COMPANIES,
                                           "source", FETCHED, date(2026, 1, 5))
    assert records == [] and rejected == 1


def test_twse_institutional_envelope_and_company_scope():
    fields = ["證券代號", "外陸資買賣超股數(不含外資自營商)", "投信買賣超股數", "自營商買賣超股數", "三大法人買賣超股數"]
    payload = {"date": "20260102", "fields": fields,
               "data": [["1234", "1500", "200", "-300", "1400"],
                        ["006208", "1500", "200", "-300", "1400"]]}
    records, rejected = parse_institutions(payload, "TWSE", COMPANIES, "source", FETCHED, date(2026, 1, 5))
    assert len(records) == 1 and rejected == 0
    assert records[0]["net_buy"] == 1400 and records[0]["date"] == "2026-01-02"


def closure_responses():
    # Actual official announcement titles and publication date, not price fixtures.
    return {"twse_market_notices": {"data": [[2, "臺灣證券交易所集中交易市場115年10月23日休市一天",
                                               "115年10月22日", "notice-id"]]},
            "tpex_market_notices": {"release": [{"id": 23117,
                "title": "115/10/22 因颱風過境，台北市政府宣布10月23日全日停止上班，櫃檯買賣有價證券市場及衍生性金融商品市場休市一日"}]}}


def test_two_exchange_emergency_closures_are_verified_for_exact_date_only():
    calendar = parse_market_calendar(closure_responses(), date(2026, 10, 23))
    assert calendar["trading_day"] is False
    assert set(calendar["market_closures"]) == {"TWSE", "TPEX"}
    assert parse_market_calendar(closure_responses(), date(2026, 10, 27))["trading_day"] is None


@pytest.mark.parametrize("change", ["single_exchange", "future_publication", "partial_day", "cancelled", "company", "ambiguous_year"])
def test_emergency_calendar_requires_full_market_scope_and_known_dates(change):
    responses = closure_responses()
    tpex = responses["tpex_market_notices"]["release"][0]
    if change == "single_exchange":
        del responses["tpex_market_notices"]
    elif change == "future_publication":
        tpex["title"] = tpex["title"].replace("115/10/22", "115/10/24")
    elif change == "partial_day":
        tpex["title"] += "，僅下午休市"
    elif change == "cancelled":
        tpex["title"] += "，取消休市並照常交易"
    elif change == "company":
        tpex["title"] = "115/10/22 個別公司股票10月23日停止交易一日"
    else:
        tpex["title"] = tpex["title"].replace("115/10/22", "114/10/22")
    assert parse_market_calendar(responses, date(2026, 10, 23))["trading_day"] is None


def test_calendar_preflight_preserves_evidence_without_requesting_quotes_and_is_reused(tmp_path):
    class CalendarSession(FakeSession):
        calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == ENDPOINTS["twse_calendar"]:
                return FakeResponse([])
            for source_id, payload in closure_responses().items():
                if url == ENDPOINTS[source_id]:
                    return FakeResponse(payload)
            return super().get(url, **kwargs)

    session = CalendarSession()
    preflight = collect_calendar(date(2026, 10, 23), tmp_path, session=session)
    assert len(session.calls) == 3
    assert preflight["calendar"]["trading_day"] is False
    assert all(source.get("sha256") for source in preflight["sources"])
    result = collect(date(2026, 10, 23), tmp_path, session=session, calendar_result=preflight)
    assert len(session.calls) == len(ENDPOINTS)
    assert result["calendar"] == preflight["calendar"]
    assert len(result["sources"]) == len(ENDPOINTS)


def test_collection_saves_real_institution_source_rows(tmp_path):
    class InstitutionSession(FakeSession):
        def get(self, url, **kwargs):
            if url == ENDPOINTS["twse_institutions"]:
                return FakeResponse({"date": "20260102", "data": [{"證券代號": "1234",
                    "外陸資買賣超股數(不含外資自營商)": "1000", "投信買賣超股數": "0",
                    "自營商買賣超股數": "-500", "三大法人買賣超股數": "500"}]})
            return super().get(url, **kwargs)

    result = collect(date(2026, 1, 5), tmp_path, session=InstitutionSession())
    assert len(result["institutions"]) == 1
    assert result["institutions"][0]["date"] == "2026-01-02"
    source = next(s for s in result["sources"] if s["id"] == "twse_institutions")
    assert source["status"] == "ok" and source["data_date"] == "2026-01-02"
    assert source["accepted_rows"] == 1 and source["rejected_rows"] == 0
