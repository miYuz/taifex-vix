# -*- coding: utf-8 -*-
"""證交所個股/ETF 日收盤價(拿來跟 VIX 對照看)。

端點:
    GET https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY
        ?date=YYYYMM01&stockNo=0050&response=json
    一次回傳「整個月」,所以回補只要每月一次請求。
    日期是民國年(115/08/03),要轉西元。

注意:證交所給的是**未還原權值**的收盤價,遇到分割/除權息會有跳空。
本模組同時提供 `split_adjusted()` 做比例還原,畫長期走勢要用還原後的。
"""
import os
import time
from datetime import date

import pandas as pd

from . import config
import requests  # noqa: E402  (必須在 config 之後,見 fetch.py 說明)

STOCK_DAY_URL = ("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
                 "?date={ym}01&stockNo={stock}&response=json")


def _cache_path(stock, y, m):
    return os.path.join(config.META_DIR, f"twse_{stock}_{y}{m:02d}.csv")


def fetch_month(y, m, stock="0050", use_cache=True):
    """單月日收盤價 → DataFrame(trade_date, close)。查無資料回空表。"""
    config.ensure_dirs()
    cp = _cache_path(stock, y, m)
    # 當月資料還會變動,不用永久快取
    fresh = (y, m) == (date.today().year, date.today().month)
    if use_cache and os.path.exists(cp) and not fresh:
        return pd.read_csv(cp, parse_dates=["trade_date"])

    url = STOCK_DAY_URL.format(ym=f"{y}{m:02d}", stock=stock)
    r = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                     timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    j = r.json()
    time.sleep(config.REQUEST_SLEEP)
    if j.get("stat") != "OK" or not j.get("data"):
        return pd.DataFrame(columns=["trade_date", "close"])

    rows = []
    for rec in j["data"]:
        try:
            roc_y, mm, dd = rec[0].split("/")
            d = date(int(roc_y) + 1911, int(mm), int(dd))
            close = float(str(rec[6]).replace(",", ""))
        except (ValueError, IndexError):
            continue
        rows.append({"trade_date": pd.Timestamp(d), "close": close})
    out = pd.DataFrame(rows)
    if len(out) and not fresh:
        out.to_csv(cp, index=False, encoding="utf-8-sig")
    return out


def load_close_series(start, end, stock="0050", verbose=True):
    """區間日收盤價。逐月抓(有快取),回傳 DataFrame(trade_date, close)。"""
    frames = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        try:
            d = fetch_month(y, m, stock=stock)
            if len(d):
                frames.append(d)
        except Exception as e:                      # noqa: BLE001
            if verbose:
                print(f"[warn] {stock} {y}-{m:02d} 取得失敗: {e}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    if not frames:
        return pd.DataFrame(columns=["trade_date", "close"])
    out = (pd.concat(frames, ignore_index=True)
             .drop_duplicates("trade_date")
             .sort_values("trade_date")
             .reset_index(drop=True))
    return out[(out.trade_date >= pd.Timestamp(start)) &
               (out.trade_date <= pd.Timestamp(end))].reset_index(drop=True)


def detect_splits(df, ratio_threshold=0.35):
    """找出疑似分割/減資造成的跳空(單日變動超過門檻)。

    回傳 [(日期, 前收, 今收, 比例)]。0050 在 2025 年做過分割,
    不處理的話長期走勢圖會出現一個假的斷崖。
    """
    out = []
    c = df["close"].to_numpy()
    for i in range(1, len(c)):
        if c[i - 1] > 0 and abs(c[i] / c[i - 1] - 1) > ratio_threshold:
            out.append((df["trade_date"].iloc[i], float(c[i - 1]),
                        float(c[i]), float(c[i] / c[i - 1])))
    return out


def split_adjusted(df, ratio_threshold=0.35):
    """把分割前的價格按比例縮放,接成連續序列(以最新價格為基準)。

    只處理明顯的分割跳空;一般除權息的小幅缺口不動,因為那是真實報酬的一部分。
    """
    d = df.copy().reset_index(drop=True)
    splits = detect_splits(d, ratio_threshold)
    if not splits:
        d["close_adj"] = d["close"]
        return d, []
    factor = pd.Series(1.0, index=d.index)
    for ts, prev, cur, ratio in splits:
        idx = d.index[d["trade_date"] == ts][0]
        factor.iloc[:idx] *= ratio          # 分割日之前整段按比例縮放
    d["close_adj"] = d["close"] * factor
    return d, splits
