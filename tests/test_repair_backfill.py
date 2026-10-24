"""Synthetic repair fixtures verify targeted requests and preservation."""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from taiwan_stock_ai.storage import Store


SPEC = importlib.util.spec_from_file_location(
    "repair_backfill", Path(__file__).parents[1] / "scripts" / "repair_backfill.py")
repair_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair_module)


def setup_history(tmp_path):
    data = tmp_path / "data"
    with Store(data / "market.sqlite3") as store:
        store.ingest("quotes", [{"market": "TWSE", "symbol": "1234", "date": "2025-12-31",
                                 "fetched_at": "2026-01-02T08:00:00+00:00", "close": 99}], "2026-01-02")
    source_dir = data / "raw" / "original"
    source_dir.mkdir(parents=True)
    raw = json.dumps([{"公司代號": "1234", "公司簡稱": "合成測試", "產業別": "24",
                       "出表日期": "1150102"}]).encode()
    (source_dir / "twse_companies.json.gz").write_bytes(gzip.compress(raw))
    manifest = source_dir / "manifest.json"
    manifest.write_text(json.dumps({"sources": [
        {"id": "twse_companies", "status": "ok", "sha256": hashlib.sha256(raw).hexdigest()},
        {"id": "twse_history_2026-01-02", "status": "error", "url": "https://untrusted.invalid"},
        {"id": "twse_history_2026-01-05", "status": "ok", "accepted_rows": 1},
        {"id": "twse_history_2026-01-01", "status": "waiting", "accepted_rows": 0},
    ]}))
    return data, manifest


class FakeResponse:
    status_code = 200

    def __init__(self, day="20260102"):
        self.payload = {"date": day, "fields": ["證券代號", "證券名稱", "開盤價", "最高價", "最低價",
                                                "收盤價", "成交股數", "成交金額"],
                        "data": [["1234", "合成測試", "100", "102", "99", "101", "1000", "101000"]]}
        self.content = json.dumps(self.payload).encode()

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, day="20260102"):
        self.urls = []
        self.day = day

    def get(self, url, **kwargs):
        assert "verify" not in kwargs
        self.urls.append(url)
        return FakeResponse(self.day)


def test_only_failed_source_day_requested_and_prior_history_preserved(tmp_path):
    data, manifest = setup_history(tmp_path)
    session = FakeSession()
    result = repair_module.repair(manifest, data, session=session)
    assert len(session.urls) == 1
    assert session.urls[0].startswith("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?")
    assert "date=20260102" in session.urls[0]
    assert result["inserted"] == 1 and result["integrity"]
    source = result["sources"][0]
    assert source["accepted_rows"] == 1
    assert hashlib.sha256(gzip.decompress(Path(source["raw_path"]).read_bytes())).hexdigest() == source["sha256"]
    with Store(data / "market.sqlite3") as store:
        records = store.records("quotes", "2026-01-02")
    assert len(records) == 2 and records[0]["close"] == 99
    assert records[1]["universe_date"] == "2026-01-02"


def test_wrong_response_date_never_ingested(tmp_path):
    data, manifest = setup_history(tmp_path)
    result = repair_module.repair(manifest, data, session=FakeSession("20260105"))
    assert result["inserted"] == 0
    assert result["sources"][0]["status"] == "error"
    assert result["sources"][0]["accepted_rows"] == 0
    assert Path(result["manifest"]).is_file()


def test_company_snapshot_integrity_required_before_any_request(tmp_path):
    data, manifest = setup_history(tmp_path)
    (manifest.parent / "twse_companies.json.gz").write_bytes(gzip.compress(b"[]"))
    session = FakeSession()
    with pytest.raises(ValueError, match="checksum"):
        repair_module.repair(manifest, data, session=session)
    assert session.urls == []


def test_future_source_date_is_rejected(tmp_path):
    data, manifest = setup_history(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["sources"][1]["id"] = "twse_history_2099-01-02"
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="future"):
        repair_module.repair(manifest, data, session=FakeSession())


def test_existing_database_required(tmp_path):
    data, manifest = setup_history(tmp_path)
    with pytest.raises(ValueError, match="Existing database"):
        repair_module.repair(manifest, tmp_path / "nonexistent", session=FakeSession())
