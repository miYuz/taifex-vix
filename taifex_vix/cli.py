# -*- coding: utf-8 -*-
"""taifex_vix 進入點。

    python -m taifex_vix.cli backfill --start 2023-01-01
    python -m taifex_vix.cli daily
    python -m taifex_vix.cli day 2026-07-31 --show
"""
import argparse
import sys
import time
from datetime import date, datetime

import pandas as pd

if __package__ in (None, ""):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from taifex_vix import config, fetch, pipeline
else:
    from . import config, fetch, pipeline


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def cmd_backfill(args):
    start = _d(args.start) if args.start else _d(config.DEFAULT_START)
    end = _d(args.end) if args.end else date.today()
    print(f"回補 {start} ~ {end}  (session={config.MARKET_CODE}, "
          f"skip_done={not args.force})")
    out, stat = pipeline.run_range(start, end, market_code=args.market_code,
                                   allow_http=not args.offline,
                                   skip_done=not args.force)
    print(f"\n交易日 {stat['trading']} / 非交易日 {stat['non_trading']} / "
          f"已存在跳過 {stat['skipped_done']} / 失敗 {stat['failed']}")
    if not args.no_price:
        out = pipeline.update_price_column()
    print(f"主輸出共 {len(out)} 列 → {config.OUT_CSV}")
    return 0


POLL_SEC = 180


def _wait_for_settlement(args):
    """排程用:當天是交易日但結算價還沒出時,原地等到它出來(或等到逾時)。

    非交易日(週末/假日)不會空等,直接放行讓後面退回前一個交易日。
    """
    if args.wait <= 0:
        return
    target = date.today()
    deadline = time.monotonic() + args.wait * 60
    while True:
        _, status = fetch.load_day_status(target, market_code=args.market_code,
                                          verbose=False)
        if status == "ok":
            print(f"{target} 結算價已公布 @ {datetime.now():%H:%M:%S}")
            return
        if status == "non_trading":
            print(f"{target} 非交易日,不等待")
            return
        if time.monotonic() >= deadline:
            print(f"等待逾時({args.wait} 分),改用前一個交易日")
            return
        print(f"[wait] {datetime.now():%H:%M:%S} {target} 尚未結算,"
              f"{POLL_SEC // 60} 分後重試")
        time.sleep(POLL_SEC)


def cmd_daily(args):
    _wait_for_settlement(args)
    d = pipeline.latest_trading_day(market_code=args.market_code)
    if d is None:
        print("往回 10 天都找不到交易日資料")
        return 1
    row, pack = pipeline.run_day(d, market_code=args.market_code)
    if row is None:
        print(f"{d} 無資料")
        return 1
    pipeline.save_detail(d, pack)
    out = pipeline.upsert([row])
    if not args.no_price:
        # 只刷新近兩個月:CI 上沒有月檔快取,不限制會重抓幾十個月份
        out = pipeline.update_price_column(verbose=False, months_back=2)
    print(f"{d}  {pipeline.fmt_row(row)}")
    if row.get("warnings"):
        print(f"  warnings: {row['warnings']}")
    print(f"主輸出共 {len(out)} 列 → {config.OUT_CSV}")
    return 0


def cmd_day(args):
    d = _d(args.date)
    row, pack = pipeline.run_day(d, market_code=args.market_code,
                                 allow_http=not args.offline)
    if row is None:
        print(f"{d} 非交易日 / 無資料")
        return 1
    print(f"{d}  {pipeline.fmt_row(row)}")
    for N in config.TARGET_DAYS:
        print(f"  VIX{N}: {row[f'vix{N}']!r}  mode={row[f'vix{N}_mode']}  "
              f"d1={row[f'vix{N}_d1']}({row[f'vix{N}_code1']})  "
              f"d2={row[f'vix{N}_d2']}({row[f'vix{N}_code2']})")
    if row.get("warnings"):
        print(f"  warnings: {row['warnings']}")
    if args.show:
        detail, _ = pack
        pd.set_option("display.width", 200)
        cols = ["expiry_code", "dte", "expiry_source", "ok", "T", "F", "K0",
                "strip_count", "ba_ratio", "vix", "reason"]
        print("\n各到期日:")
        print(detail[cols].to_string(index=False))
    if args.save:
        pipeline.save_detail(d, pack)
        pipeline.upsert([row])
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="taifex_vix",
                                description="期交所 TXO → 7/14 天 VIX")
    sub = p.add_subparsers(dest="cmd", required=True)

    # 共用選項:放在 parent 讓它們寫在子命令『後面』(符合直覺)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--market-code", type=int, default=None,
                        help="0=日盤(預設) 1=盤後")
    common.add_argument("--offline", action="store_true",
                        help="只用 raw 快取,不發 HTTP")
    common.add_argument("--no-price", action="store_true",
                        help="不更新 0050 收盤價欄位")

    b = sub.add_parser("backfill", parents=[common], help="回補歷史區間")
    b.add_argument("--start", default=None)
    b.add_argument("--end", default=None)
    b.add_argument("--force", action="store_true", help="已有的日期也重算")
    b.set_defaults(func=cmd_backfill)

    dl = sub.add_parser("daily", parents=[common], help="抓最近一個交易日並 append")
    dl.add_argument("--wait", type=int, default=0, metavar="分鐘",
                    help="當天結算價還沒出時,最多等幾分鐘(排程用,預設 0=不等)")
    dl.set_defaults(func=cmd_daily)

    dy = sub.add_parser("day", parents=[common], help="單日試算(預設不寫檔)")
    dy.add_argument("date")
    dy.add_argument("--show", action="store_true", help="印出各到期日診斷")
    dy.add_argument("--save", action="store_true", help="寫入主輸出")
    dy.set_defaults(func=cmd_day)

    args = p.parse_args(argv)
    if not config.SSL_OK:
        print(f"[fatal] {config.SSL_NOTE}")
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
