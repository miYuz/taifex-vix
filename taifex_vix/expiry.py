# -*- coding: utf-8 -*-
"""到期日解析。

2025/12/08 起行情表才有『契約到期日』欄,更早的資料要自己補。分四層,
每個到期日都記錄用了哪一層(source),notebook 可以檢查。

  1. col       行情表的『契約到期日』欄
  2. fsp       最後結算價一覽表(cht/5/optIndxFSP)的最後結算日
  3. empirical 該到期代碼在已抓快取中最後出現的交易日(自動吃掉假日調整)
  4. rule      代碼推導:YYYYMM=第三個週三、WnW=第 n 個週三、Fn=第 n 個週五

代碼格式(實測):202608 月選 / 202608W1 週三週選 / 202608F1 週五週選
"""
import os
import re
from datetime import date, timedelta

import pandas as pd

from . import config, fetch

_CODE_RE = re.compile(r"^(\d{4})(\d{2})(?:([WF])(\d))?$")

_WEDNESDAY, _FRIDAY = 2, 4

_empirical_cache = None


def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def rule_expiry(code):
    """由到期代碼推導名目到期日(未考慮假日順延)。無法解析回 None。"""
    m = _CODE_RE.match(str(code).strip())
    if not m:
        return None
    y, mo, kind, n = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    if not 1 <= mo <= 12:
        return None
    if kind is None:                       # 月選 = 第三個週三
        return _nth_weekday(y, mo, _WEDNESDAY, 3)
    if kind == "W":
        return _nth_weekday(y, mo, _WEDNESDAY, int(n))
    return _nth_weekday(y, mo, _FRIDAY, int(n))


def build_empirical_map(market_code=None, force=False):
    """掃 raw 快取:到期代碼 → 最後出現的交易日。

    只收「已經不再出現」的代碼(最後出現日 < 快取最新日),否則近月序列
    會把『今天』誤判成到期日。
    """
    global _empirical_cache
    if _empirical_cache is not None and not force:
        return _empirical_cache

    mc = config.MARKET_CODE if market_code is None else market_code
    prefix = f"TXO_{fetch._SESSION_NAME.get(mc, mc)}_"
    last_seen = {}
    max_date = None
    if os.path.isdir(config.RAW_DIR):
        for fn in sorted(os.listdir(config.RAW_DIR)):
            if not (fn.startswith(prefix) and fn.endswith(".csv")):
                continue
            path = os.path.join(config.RAW_DIR, fn)
            try:
                d = pd.read_csv(path, usecols=["trade_date", "expiry_code"],
                                dtype={"expiry_code": str})
            except Exception:              # noqa: BLE001
                continue
            if d.empty:
                continue
            td = pd.to_datetime(d["trade_date"].iloc[0]).date()
            max_date = td if max_date is None else max(max_date, td)
            for code in d["expiry_code"].dropna().unique():
                prev = last_seen.get(code)
                last_seen[code] = td if prev is None else max(prev, td)

    _empirical_cache = {c: v for c, v in last_seen.items()
                        if max_date is None or v < max_date}
    return _empirical_cache


def resolve(df, use_fsp=True, use_empirical=True):
    """回傳 DataFrame(expiry_code, expiry_date, expiry_source)。

    df 為 fetch.load_day 的 tidy 表(單一交易日)。
    """
    codes = sorted(df["expiry_code"].dropna().unique())

    # layer 1: 行情表自帶
    from_col = {}
    if "expiry_date" in df.columns:
        g = df.dropna(subset=["expiry_date"]).groupby("expiry_code")["expiry_date"]
        from_col = {c: v.date() for c, v in g.min().items()}

    from_fsp = {}
    if use_fsp and len(from_col) < len(codes):
        try:
            fsp = fetch.fetch_fsp_table()
            from_fsp = {r.expiry_code: r.settle_date.date()
                        for r in fsp.itertuples()}
        except Exception:                  # noqa: BLE001
            from_fsp = {}

    from_emp = {}
    if use_empirical and len(from_col) < len(codes):
        from_emp = build_empirical_map()

    rows = []
    for c in codes:
        if c in from_col:
            rows.append((c, from_col[c], "col"))
        elif c in from_fsp:
            rows.append((c, from_fsp[c], "fsp"))
        elif c in from_emp:
            rows.append((c, from_emp[c], "empirical"))
        else:
            r = rule_expiry(c)
            rows.append((c, r, "rule" if r else "unknown"))

    out = pd.DataFrame(rows, columns=["expiry_code", "expiry_date",
                                      "expiry_source"])
    out["expiry_date"] = pd.to_datetime(out["expiry_date"])
    return out.dropna(subset=["expiry_date"]).reset_index(drop=True)


def attach(df):
    """把 expiry_date / expiry_source / dte 併回 tidy 行情表。"""
    exp = resolve(df)
    trade_date = pd.Timestamp(df["trade_date"].iloc[0])
    out = df.drop(columns=["expiry_date"], errors="ignore").merge(
        exp, on="expiry_code", how="inner")
    out["dte"] = (out["expiry_date"] - trade_date).dt.days
    return out
