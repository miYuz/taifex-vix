# -*- coding: utf-8 -*-
"""單日 orchestration 與落地。

run_day  : 抓 → 解析 → 到期日 → 逐到期日 variance → 固定天期插值 → (主列, 診斷表)
run_range: 逐日跑並 upsert 進主輸出
"""
import os
from datetime import date, datetime, timedelta

import pandas as pd

from . import config, expiry, fetch, term, vix_core

NEAR_DTE_LIMIT = 40          # 診斷/品質統計只看近月,不被季選拖累


def run_day(d, market_code=None, use_cache=True, allow_http=True):
    """回傳 (row: dict, detail: DataFrame)。非交易日回 (None, None)。"""
    if isinstance(d, datetime):
        d = d.date()
    df = fetch.load_day(d, market_code=market_code, use_cache=use_cache,
                        allow_http=allow_http)
    if df is None or df.empty:
        return None, None

    df = expiry.attach(df)
    if df.empty:
        return None, None

    metrics, strips = [], []
    for (code, dte, src), g in df.groupby(["expiry_code", "dte",
                                           "expiry_source"], sort=True):
        m, strip = vix_core.compute_expiry(g, dte, expiry_code=code)
        m["expiry_source"] = src
        metrics.append(m)
        if len(strip):
            strip = strip.assign(expiry_code=code, dte=dte)
            strips.append(strip)

    detail = pd.DataFrame(metrics).sort_values("dte").reset_index(drop=True)
    detail.insert(0, "trade_date", pd.Timestamp(d))

    row = {"trade_date": pd.Timestamp(d)}
    for N in config.TARGET_DAYS:
        res = term.interpolate(metrics, N)
        row[f"vix{N}"] = res["vix"]
        row[f"vix{N}_mode"] = res["mode"]
        row[f"vix{N}_d1"] = res["d1"]
        row[f"vix{N}_d2"] = res["d2"]
        row[f"vix{N}_code1"] = res["code1"]
        row[f"vix{N}_code2"] = res["code2"]
        # 插值跨度 = 兩個到期日相距幾天,是這個值可信度的直接指標。
        # 實測(scratchpad/method_test.py):跨度 ~7 天時還原誤差 MAE 約 0.3;
        # 跨度拉到 15 天(2022 年以前的常態)會惡化到 MAE 約 0.9。
        row[f"vix{N}_span"] = (None if res["d1"] is None or res["d2"] is None
                               else int(res["d2"] - res["d1"]))

    near = detail[detail["dte"] <= NEAR_DTE_LIMIT]
    ok_near = near[near["ok"]]
    row["n_expiry_used"] = int(detail["ok"].sum())
    row["n_expiry_near"] = int(len(near))
    row["n_expiry_near_ok"] = int(len(ok_near))
    if len(ok_near):
        first = ok_near.sort_values("dte").iloc[0]
        row["dte_near"] = int(first["dte"])
        row["F_near"] = float(first["F"])
        row["K0_near"] = float(first["K0"])
        row["ba_ratio"] = float(ok_near["ba_ratio"].mean())
        row["strip_min"] = int(ok_near["strip_count"].min())
        row["expiry_src"] = ",".join(sorted(set(ok_near["expiry_source"])))
    else:
        row.update({"dte_near": None, "F_near": None, "K0_near": None,
                    "ba_ratio": None, "strip_min": None, "expiry_src": None})

    bad = near[(~near["ok"]) & (near["reason"].notna())]
    row["warnings"] = "; ".join(
        f"{r.expiry_code}(d{r.dte}):{r.reason}" for r in bad.itertuples()) or None

    if strips:
        strip_all = pd.concat(strips, ignore_index=True)
        strip_all.insert(0, "trade_date", pd.Timestamp(d))
    else:
        strip_all = pd.DataFrame()
    return row, (detail, strip_all)


def fmt_row(row):
    """單列的一行摘要,給 CLI 印。"""
    parts = []
    for N in config.TARGET_DAYS:
        v, mode = row.get(f"vix{N}"), row.get(f"vix{N}_mode")
        tag = {"exact": "=", "interp": "~"}.get(mode, "?")
        parts.append(f"VIX{N}{tag}{v:6.2f}" if v == v else f"VIX{N}{tag}   nan")
    ba = row.get("ba_ratio")
    parts.append(f"ba={ba:.0%}" if ba is not None else "ba=n/a")
    parts.append(f"strip>={row.get('strip_min')}")
    return "  ".join(parts)


def save_detail(d, detail_pack):
    if detail_pack is None:
        return
    detail, strip_all = detail_pack
    config.ensure_dirs()
    detail.to_csv(os.path.join(config.DETAIL_DIR, f"{d:%Y%m%d}.csv"),
                  index=False, encoding="utf-8-sig")
    if len(strip_all):
        strip_all.to_csv(os.path.join(config.DETAIL_DIR, f"{d:%Y%m%d}_strip.csv"),
                         index=False, encoding="utf-8-sig")


def load_output():
    if not os.path.exists(config.OUT_CSV):
        return pd.DataFrame()
    # 月選代碼是純數字(例如 202609),不強制 str 的話 read_csv 會讀成數值,
    # 之後 upsert 拼回去就變成 str/float 混型 → parquet 寫入失敗、比對也會對不上。
    code_cols = [f"vix{N}_code{i}" for N in config.TARGET_DAYS for i in (1, 2)]
    return pd.read_csv(config.OUT_CSV, parse_dates=["trade_date"],
                       dtype={c: str for c in code_cols})


def upsert(rows):
    """以 trade_date 為 key 覆蓋寫入,重跑同一天不會產生重複列。"""
    if not rows:
        return load_output()
    config.ensure_dirs()
    new = pd.DataFrame(rows)
    old = load_output()
    if len(old):
        old = old[~old["trade_date"].isin(new["trade_date"])]
        out = pd.concat([old, new], ignore_index=True)
    else:
        out = new
    out = out.sort_values("trade_date").reset_index(drop=True)
    out.to_csv(config.OUT_CSV, index=False, encoding="utf-8-sig")
    try:
        out.to_parquet(config.OUT_PARQUET, index=False)
    except Exception as e:                 # noqa: BLE001
        print(f"[warn] parquet 寫入略過 ({e})")
    return out


def run_range(start, end, market_code=None, use_cache=True, allow_http=True,
              skip_done=True, verbose=True):
    """逐日執行並落地。回傳 (主輸出 DataFrame, 統計 dict)。

    分兩趟:先把整段區間的原始行情抓齊,再回頭算 VIX。這樣 expiry.py 的
    「經驗法」(到期代碼最後出現的交易日 = 到期日)才有完整資料可用 ——
    2025/12/08 前的行情表沒有『契約到期日』欄,靠這一層補。
    """
    done = set()
    if skip_done:
        prev = load_output()
        if len(prev):
            done = set(pd.to_datetime(prev["trade_date"]).dt.date)

    stat = {"trading": 0, "skipped_done": 0, "non_trading": 0, "failed": 0,
            "fetched": 0}

    # ---- 第一趟:抓原始資料進快取 ----
    if allow_http:
        if verbose:
            print("[1/2] 抓取原始行情…")
        d, n = start, 0
        while d <= end:
            if d not in done and d.weekday() < 5:
                try:
                    if fetch.load_day(d, market_code=market_code,
                                      use_cache=use_cache) is not None:
                        stat["fetched"] += 1
                except Exception as e:              # noqa: BLE001
                    if verbose:
                        print(f"  {d} 抓取失敗: {e}")
                n += 1
                if verbose and n % 50 == 0:
                    print(f"  …{d} (已取得 {stat['fetched']} 個交易日)")
            d += timedelta(days=1)
        expiry.build_empirical_map(market_code=market_code, force=True)

    # ---- 第二趟:算 VIX(純吃快取) ----
    if verbose:
        print("[2/2] 計算 VIX…")
    rows = []
    d = start
    while d <= end:
        if d in done:
            stat["skipped_done"] += 1
            d += timedelta(days=1)
            continue
        try:
            row, pack = run_day(d, market_code=market_code,
                                use_cache=use_cache, allow_http=False)
        except Exception as e:             # noqa: BLE001
            stat["failed"] += 1
            if verbose:
                print(f"  {d} 失敗: {e}")
            d += timedelta(days=1)
            continue

        if row is None:
            stat["non_trading"] += 1
        else:
            rows.append(row)
            save_detail(d, pack)
            stat["trading"] += 1
            if verbose:
                print(f"  {d}  {fmt_row(row)}")
        d += timedelta(days=1)

    out = upsert(rows)
    return out, stat


def update_price_column(stock="0050", verbose=True, months_back=None):
    """把證交所的日收盤價(分割還原後)併進主輸出的 close_{stock} 欄。

    months_back:只刷新最近幾個月,其餘沿用既有值。本機有快取所以無所謂,
    但在 CI 上快取是空的,不限制的話每次都要重抓 80 個月份。
    只抓近期不影響分割還原 —— 分割還原是把「分割日之前」的價格縮放到現行基準,
    近期視窗內沒有分割,factor 本來就是 1。
    """
    from . import twse
    out = load_output()
    if out.empty:
        return out
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    col = f"close_{stock}"

    start = out.trade_date.min().date()
    partial = (months_back and col in out.columns and out[col].notna().any())
    if partial:
        y, m = out.trade_date.max().year, out.trade_date.max().month
        for _ in range(max(0, months_back - 1)):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        start = date(y, m, 1)
        if verbose:
            print(f"[{stock}] 增量更新:只抓 {start:%Y-%m} 起的月檔")

    px = twse.load_close_series(start, out.trade_date.max().date(), stock=stock,
                                verbose=verbose)
    if px.empty:
        if verbose:
            print(f"[warn] {stock} 沒有取得任何價格")
        return out
    adj, splits = twse.split_adjusted(px)

    if partial:
        # 只覆蓋抓到的那段日期,舊資料原封不動
        newmap = dict(zip(adj.trade_date, adj.close_adj))
        out[col] = [newmap.get(d, v) for d, v in zip(out.trade_date, out[col])]
        out = out.sort_values("trade_date").reset_index(drop=True)
        out.to_csv(config.OUT_CSV, index=False, encoding="utf-8-sig")
        try:
            out.to_parquet(config.OUT_PARQUET, index=False)
        except Exception as e:                 # noqa: BLE001
            print(f"[warn] parquet 寫入略過 ({e})")
        if verbose:
            print(f"{col}: {out[col].notna().sum()} / {len(out)} 天有價格")
        return out
    if verbose and splits:
        for ts, prev, cur, ratio in splits:
            print(f"[{stock}] {ts.date()} 偵測到分割 {prev:.2f}->{cur:.2f} "
                  f"(1:{1 / ratio:.0f}),已按比例還原")
    col = f"close_{stock}"
    out = out.drop(columns=[col], errors="ignore").merge(
        adj[["trade_date", "close_adj"]].rename(columns={"close_adj": col}),
        on="trade_date", how="left")
    out = out.sort_values("trade_date").reset_index(drop=True)
    out.to_csv(config.OUT_CSV, index=False, encoding="utf-8-sig")
    try:
        out.to_parquet(config.OUT_PARQUET, index=False)
    except Exception as e:                 # noqa: BLE001
        print(f"[warn] parquet 寫入略過 ({e})")
    if verbose:
        print(f"{col}: {out[col].notna().sum()} / {len(out)} 天有價格")
    return out


def latest_trading_day(ref=None, lookback=10, market_code=None):
    """往回找最近一個有資料的交易日。"""
    ref = ref or date.today()
    for i in range(lookback):
        d = ref - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        if fetch.load_day(d, market_code=market_code) is not None:
            return d
    return None
