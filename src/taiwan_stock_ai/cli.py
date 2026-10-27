"""Command line interface. A waiting-data report is a successful, honest run."""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from .pipeline import TAIPEI, run
from .backfill import backfill
from .providers import collect
from .storage import Store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="台股 AI 智慧投資研究系統 V1.0（盤後）")
    sub = parser.add_subparsers(dest="command", required=True)
    daily = sub.add_parser("run", help="收集、分析並產生靜態網站")
    daily.add_argument("--phase", default="manual",
                       choices=["auto", "preopen", "intraday", "postclose", "final", "manual"])
    daily.add_argument("--as-of", type=date.fromisoformat)
    daily.add_argument("--output", type=Path, default=Path("site"))
    daily.add_argument("--offline", action="store_true", help="只使用已保存真實資料")
    probe = sub.add_parser("probe", help="測試官方 API，保存回應與品質資訊")
    probe.add_argument("--as-of", type=date.fromisoformat)
    check = sub.add_parser("check-db", help="檢查既有資料庫完整性")
    backup = sub.add_parser("backup", help="建立一致性的 SQLite 備份")
    backup.add_argument("destination", type=Path)
    history = sub.add_parser("backfill", help="從官方指定日期端點回補目前股票範圍的歷史行情")
    history.add_argument("--start", type=date.fromisoformat, required=True)
    history.add_argument("--end", type=date.fromisoformat, required=True)
    history.add_argument("--delay", type=float, default=1.0, help="請求間隔秒數，至少 1 秒")
    for command in (daily, probe, check, backup, history):
        command.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    if args.command == "backfill":
        if args.end > datetime.now(TAIPEI).date() or (args.end - args.start).days > 366:
            parser.error("回補須為已發生日期，每次最多 366 天")
        with Store(args.data_dir / "market.sqlite3") as store:
            result = backfill(args.start, args.end, args.data_dir / "raw", args.delay,
                              progress=lambda source: print(json.dumps({
                                  k: source.get(k) for k in ("id", "date", "data_date", "status", "accepted_rows", "error")
                              }, ensure_ascii=False), flush=True),
                              on_batch=lambda rows: store.ingest("quotes", rows, args.end.isoformat()))
            store.ingest("quotes", result["quotes"], args.end.isoformat())
            print(json.dumps({"observations": len(result["quotes"]),
                              "integrity": store.check(), "note": "僅回補指標歷史，不補造過去預測紀錄。"}, ensure_ascii=False))
        return 0 if result["quotes"] else 2
    if args.command == "run":
        report = run(args.data_dir, args.output, args.phase, args.as_of, args.offline)
        print(json.dumps({"status": report["status"], "as_of": report["as_of"],
                          "phase": report["phase"], "generated_at": report["generated_at"],
                          "data_date": report["data_date"],
                          "institutional_status": report.get("institutional_status"),
                          "sources": [{k: s.get(k) for k in ("id", "status", "data_date", "error")}
                                      for s in report["sources"]],
                          "candidates": len(report["analysis"]["candidates"]),
                          "site": str(args.output), "notices": report["notices"]}, ensure_ascii=False))
        return 0
    if args.command == "probe":
        result = collect(args.as_of or datetime.now(TAIPEI).date(), args.data_dir / "raw")
        print(json.dumps({"sources": result["sources"], "calendar": result["calendar"],
                          "quotes": len(result["quotes"])}, ensure_ascii=False, indent=2))
        return 0 if result["quotes"] else 2
    db = args.data_dir / "market.sqlite3"
    if not db.is_file():
        parser.error(f"資料庫不存在：{db}")
    with Store(db) as store:
        if args.command == "backup":
            store.backup(args.destination)
        ok = store.check()
        print("SQLite integrity: " + ("ok" if ok else "FAILED"))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
