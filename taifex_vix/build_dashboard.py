# -*- coding: utf-8 -*-
"""由主輸出 CSV 重建互動儀表板 HTML。

    python -m taifex_vix.build_dashboard [輸出路徑]

樣板放在 taifex_vix/dashboard/ 底下:
    template_head.html  版面 + CSS + 靜態結構
    template_tail.js    繪圖與互動邏輯
資料每次從 vix_txo_daily.csv 重新產生後內嵌進去,所以圖永遠跟 CSV 同步。
"""
import json
import os
import sys
from datetime import datetime

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from taifex_vix import config, pipeline
else:
    from . import config, pipeline

TPL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "vix_dashboard.html")


def build_payload(df):
    """主輸出 DataFrame → 給前端的精簡 payload。

    刻意不含官方 30 天 VIX:圖上沒有畫它(只作引擎校準,結果寫在 README),
    而且期交所只留最近 3 個月 —— 本機有快取、CI 沒有,會讓同樣的資料
    產生不同的 HTML,每天冒出無意義的 commit。
    """
    df = df.sort_values("trade_date").reset_index(drop=True)

    def col(c, nd=2):
        if c not in df.columns:
            return [None] * len(df)
        return [None if pd.isna(v) else round(float(v), nd) for v in df[c]]

    def icol(c):
        if c not in df.columns:
            return [None] * len(df)
        return [None if pd.isna(v) else int(v) for v in df[c]]


    return {
        "d": [d.strftime("%Y-%m-%d") for d in df.trade_date],
        "v7": col("vix7"), "v14": col("vix14"), "v30": col("vix30"),
        "m7": [str(x) for x in df.get("vix7_mode", pd.Series([""] * len(df)))],
        "m14": [str(x) for x in df.get("vix14_mode", pd.Series([""] * len(df)))],
        "s7": icol("vix7_span"), "s14": icol("vix14_span"),
        "ba": col("ba_ratio", 3), "px": col("close_0050"),
    }


def build(out_path=None, verbose=True):
    out_path = out_path or DEFAULT_OUT
    df = pipeline.load_output()
    if df.empty:
        raise RuntimeError("主輸出是空的,請先跑 backfill / daily")
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    payload = build_payload(df)
    payload["built"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    head = open(os.path.join(TPL_DIR, "template_head.html"),
                encoding="utf-8").read()
    tail = open(os.path.join(TPL_DIR, "template_tail.js"),
                encoding="utf-8").read()

    blob = json.dumps(payload, separators=(",", ":"))

    # 資料同時寫成獨立檔:頁面執行時會用 no-store 抓它。
    # GitHub Pages 對 HTML 送 Cache-Control: max-age=600,只靠內嵌的話
    # 使用者會被快取的舊頁面卡住看不到當天資料。
    data_path = os.path.join(os.path.dirname(os.path.abspath(out_path)),
                             "data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        f.write(blob)

    # 內嵌一份當備援:離線或用 file:// 直接開檔時 fetch 會失敗,照樣能看
    html = (head + "\n<script>\nlet DATA = " + blob + ";\n" + tail +
            "\n</" + "script>\n")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    if verbose:
        print(f"儀表板已重建: {out_path}  ({len(html):,} bytes, "
              f"{len(df)} 個交易日, 最後 {df.trade_date.max():%Y-%m-%d})")
        print(f"資料檔: {data_path}  ({len(blob):,} bytes)")
    return out_path



def main(argv=None):
    argv = argv or sys.argv[1:]
    if not config.SSL_OK:
        print(f"[warn] {config.SSL_NOTE}")
    build(argv[0] if argv else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
