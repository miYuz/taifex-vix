# -*- coding: utf-8 -*-
"""回歸自我檢查。改過 vix_core / term / expiry 之後跑這支。

    python -m taifex_vix.selftest

全部離線(吃 raw 快取),不發 HTTP,所以可以隨時跑。
"""
import sys

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from taifex_vix import config, expiry, fetch, pipeline, term, vix_core
else:
    from . import config, expiry, fetch, pipeline, term, vix_core

VIX_FLOOR, VIX_CAP = 5.0, 150.0


class _Check:
    def __init__(self):
        self.ok = True
        self.n = 0

    def __call__(self, name, cond, extra=""):
        self.ok &= bool(cond)
        self.n += 1
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def main():
    check = _Check()
    df = pipeline.load_output()
    if df.empty:
        print("主輸出是空的,請先跑 backfill")
        return 1
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    print(f"主輸出 {len(df)} 列  {df.trade_date.min():%Y-%m-%d} ~ "
          f"{df.trade_date.max():%Y-%m-%d}\n")

    # --- 結構性 ---
    check("無重複交易日", df.trade_date.duplicated().sum() == 0)
    check("交易日遞增", df.trade_date.is_monotonic_increasing)

    # --- NaN 只能出現在 no_bracket / no_data,不能無故消失 ---
    for N in config.TARGET_DAYS:
        v, mode = df[f"vix{N}"], df[f"vix{N}_mode"]
        bad_nan = df[v.isna() & ~mode.isin(["no_bracket", "no_data", "bad_variance"])]
        check(f"VIX{N}: NaN 都有對應的 mode 說明", len(bad_nan) == 0,
              f"例外 {len(bad_nan)} 天")
        bad_val = df[v.notna() & mode.isin(["no_bracket", "no_data"])]
        check(f"VIX{N}: no_bracket 不該有數值", len(bad_val) == 0,
              f"例外 {len(bad_val)} 天")
        vv = v.dropna()
        check(f"VIX{N}: 數值落在 {VIX_FLOOR}~{VIX_CAP}",
              vv.between(VIX_FLOOR, VIX_CAP).all(),
              f"min {vv.min():.1f}  max {vv.max():.1f}  NaN {v.isna().sum()} 天")

    # --- exact:必須等於該到期日單獨算出的值 ---
    worst = 0.0
    ex = df[df.vix7_mode == "exact"].tail(8)
    for r in ex.itertuples():
        raw = expiry.attach(fetch.load_day(r.trade_date.date(), allow_http=False))
        g = raw[raw.expiry_code == r.vix7_code1]
        m, _ = vix_core.compute_expiry(g, 7, r.vix7_code1)
        worst = max(worst, abs(m["vix"] - r.vix7))
    check(f"exact 等於單一到期日直接算({len(ex)} 天)", worst < 1e-9,
          f"最大差 {worst:.2e}")

    # --- interp:必須落在 d1/d2 兩個到期日 VIX 之間 ---
    viol = 0
    sub = df[df.vix7_mode == "interp"].tail(40)
    for r in sub.itertuples():
        raw = expiry.attach(fetch.load_day(r.trade_date.date(), allow_http=False))
        vs = []
        for code, dte in ((r.vix7_code1, r.vix7_d1), (r.vix7_code2, r.vix7_d2)):
            g = raw[raw.expiry_code == code]
            m, _ = vix_core.compute_expiry(g, int(dte), code)
            vs.append(m["vix"])
        if not (min(vs) - 1e-9 <= r.vix7 <= max(vs) + 1e-9):
            viol += 1
    check(f"interp 落在 d1/d2 之間({len(sub)} 天)", viol == 0, f"違反 {viol} 天")

    # --- 離線重算必須完全重現 ---
    tail = df.tail(20)
    rows = [pipeline.run_day(d, allow_http=False)[0]
            for d in pd.to_datetime(tail.trade_date).dt.date]
    re_df = pd.DataFrame([r for r in rows if r is not None])
    mg = tail.merge(re_df, on="trade_date", suffixes=("_a", "_b"))
    diffs = {c: float(np.nanmax(np.abs(mg[f"{c}_a"] - mg[f"{c}_b"])))
             for c in (f"vix{N}" for N in config.TARGET_DAYS)}
    check("離線重算可完全重現", all(v < 1e-9 for v in diffs.values()), str(diffs))

    # --- 插值公式:用合成資料獨立驗算一次 ---
    fake = [{"ok": True, "dte": 4, "variance": 0.46608 ** 2, "strip_count": 50,
             "expiry_code": "A"},
            {"ok": True, "dte": 9, "variance": 0.40856 ** 2, "strip_count": 50,
             "expiry_code": "B"}]
    got = term.interpolate(fake, 7)["vix"]
    T1, T2 = 4 / 365, 9 / 365
    want = 100 * np.sqrt((T1 * 0.46608 ** 2 * (9 - 7) / 5 +
                          T2 * 0.40856 ** 2 * (7 - 4) / 5) * 365 / 7)
    check("插值公式與手算一致", abs(got - want) < 1e-9,
          f"{got:.6f} vs {want:.6f}")

    print(f"\n{'=== 全部通過 ===' if check.ok else '=== 有項目未通過 ==='}"
          f"  ({check.n} 項)")
    return 0 if check.ok else 1


if __name__ == "__main__":
    sys.exit(main())
