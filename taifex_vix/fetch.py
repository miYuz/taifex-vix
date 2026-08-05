# -*- coding: utf-8 -*-
"""抓取 + 解析期交所選擇權每日行情,並在 D:\\taifex_vix\\raw 建立離線快取。

端點(實測):
    GET https://www.taifex.com.tw/cht/3/optDailyMarketExcel
        ?commodity_id=TXO&marketCode=0&queryDate=YYYY/MM/DD
    回傳 UTF-8 HTML,資料在 pd.read_html 的 index 1 那張表。
    非交易日只有 1 張表 → 視為無資料。
"""
import io
import os
import re
import time
from datetime import date, datetime

import pandas as pd

# config 一 import 就會跑 bootstrap.ensure_ssl(),把 conda 的 OpenSSL DLL 目錄
# 掛上去。requests → urllib3 → pyOpenSSL 需要那些 DLL,所以順序不能顛倒。
from . import config
import requests  # noqa: E402  (必須在 config 之後)

_SESSION_NAME = {0: "day", 1: "night"}

# 正規化後的欄名 → tidy 欄名
_COLMAP = {
    "契約": "contract",
    "到期月份(週別)": "expiry_code",
    "契約到期日": "expiry_date_raw",
    "履約價": "strike",
    "買賣權": "right_raw",
    "開盤價": "open",
    "最高價": "high",
    "最低價": "low",
    "最後成交價": "last",
    "結算價": "settle",
    "*盤後交易時段成交量": "vol_night",
    "*一般交易時段成交量": "vol_day",
    "*合計成交量": "vol_total",
    "*未沖銷契約量": "oi",
    "最後最佳買價": "bid",
    "最後最佳賣價": "ask",
}

_NUM_COLS = ["strike", "open", "high", "low", "last", "settle",
             "vol_night", "vol_day", "vol_total", "oi", "bid", "ask"]


def _norm_col(c):
    """欄名去掉所有空白/換行:'到期月份 (週別)' → '到期月份(週別)'。"""
    return re.sub(r"\s+", "", str(c))


def _to_num(s):
    """'-' / '' → NaN;去千分位逗號與 ▼▲ 等符號。"""
    return pd.to_numeric(
        s.astype(str)
         .str.replace(",", "", regex=False)
         .str.replace("▼", "", regex=False)
         .str.replace("▲", "", regex=False)
         .str.strip()
         .replace({"-": None, "": None, "nan": None, "None": None}),
        errors="coerce")


def _clean_code(v):
    """月選代碼會被 read_html 讀成 float(202203.0),轉回字串。"""
    if isinstance(v, float) and not pd.isna(v) and float(v).is_integer():
        return str(int(v))
    return str(v).strip()


def raw_path(d, market_code=None):
    mc = config.MARKET_CODE if market_code is None else market_code
    return os.path.join(config.RAW_DIR,
                        f"TXO_{_SESSION_NAME.get(mc, mc)}_{d:%Y%m%d}.csv")


def none_path(d, market_code=None):
    """非交易日的標記檔,避免回補時反覆重打同一天。"""
    return raw_path(d, market_code) + ".none"


def _http_get(d, market_code):
    url = (f"{config.OPT_DAILY_URL}?commodity_id={config.COMMODITY_ID}"
           f"&marketCode={market_code}&queryDate={d:%Y/%m/%d}")
    last_err = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                             timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            r.encoding = "utf-8"
            time.sleep(config.REQUEST_SLEEP)
            return r.text
        except Exception as e:                      # noqa: BLE001
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{d:%Y-%m-%d} 抓取失敗: {last_err}")


def parse_html(html, trade_date):
    """HTML → tidy DataFrame。非交易日/無資料回 None。"""
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return None
    if len(tables) < 2:
        return None

    t = tables[1]
    t.columns = [_norm_col(c) for c in t.columns]
    if "契約" not in t.columns:
        return None

    t = t[[c for c in t.columns if c in _COLMAP]].rename(columns=_COLMAP)
    t["contract"] = t["contract"].map(_clean_code)
    t = t[t["contract"] == config.COMMODITY_ID].copy()
    if t.empty:
        return None

    t["expiry_code"] = t["expiry_code"].map(_clean_code)
    for c in _NUM_COLS:
        if c in t.columns:
            t[c] = _to_num(t[c])

    t["right"] = t["right_raw"].astype(str).str.strip().map(
        {"Call": "C", "Put": "P"})
    t = t.drop(columns=["right_raw", "contract"])

    # 契約到期日:2025/12/08 起才有,值是 float 20260731.0
    if "expiry_date_raw" in t.columns:
        t["expiry_date"] = pd.to_datetime(
            _to_num(t["expiry_date_raw"]).astype("Int64").astype(str),
            format="%Y%m%d", errors="coerce")
        t = t.drop(columns=["expiry_date_raw"])
    else:
        t["expiry_date"] = pd.NaT

    t.insert(0, "trade_date", pd.Timestamp(trade_date))
    t = t.dropna(subset=["strike", "right"])
    return t.sort_values(["expiry_code", "strike", "right"]).reset_index(drop=True)


def settle_coverage(df):
    """結算價填充率。盤中查當天會是 0(結算價收盤後才公布)。"""
    if df is None or df.empty:
        return 0.0
    return float(df["settle"].notna().mean())


def load_day(d, market_code=None, use_cache=True, allow_http=True,
             require_settled=True, verbose=True):
    """取得某日 tidy 行情。非交易日或尚未結算回 None。"""
    df, _ = load_day_status(d, market_code=market_code, use_cache=use_cache,
                            allow_http=allow_http,
                            require_settled=require_settled, verbose=verbose)
    return df


def load_day_status(d, market_code=None, use_cache=True, allow_http=True,
                    require_settled=True, verbose=True):
    """同 load_day,但回傳 (DataFrame|None, status)。

    status:
      'ok'          有資料且已結算
      'non_trading' 非交易日(週末/假日),留下 .none 標記不再重打
      'unsettled'   是交易日但結算價還沒公布 —— **不寫快取**,晚點重跑就會拿到
      'no_http'     只吃快取模式下沒有快取
    排程要「等結算價出來」時,靠 'unsettled' 和 'non_trading' 區分該不該繼續等。
    """
    mc = config.MARKET_CODE if market_code is None else market_code
    if isinstance(d, datetime):
        d = d.date()
    config.ensure_dirs()

    if d.weekday() >= 5:            # 週末不開盤,連打都不用打
        return None, "non_trading"

    rp, np_ = raw_path(d, mc), none_path(d, mc)
    if use_cache:
        if os.path.exists(np_):
            return None, "non_trading"
        if os.path.exists(rp):
            df = pd.read_csv(rp, dtype={"expiry_code": str},
                             parse_dates=["trade_date", "expiry_date"])
            if not len(df):
                return None, "non_trading"
            if require_settled and settle_coverage(df) < config.MIN_SETTLE_COVERAGE:
                # 之前在盤中抓到的半成品,砍掉重抓
                if verbose:
                    print(f"[refetch] {d} 快取是盤中未結算的資料,重新抓取")
                os.remove(rp)
            else:
                return df, "ok"

    if not allow_http:
        return None, "no_http"

    df = parse_html(_http_get(d, mc), d)
    if df is None or df.empty:
        open(np_, "w").close()      # 標記非交易日
        return None, "non_trading"

    cov = settle_coverage(df)
    if require_settled and cov < config.MIN_SETTLE_COVERAGE:
        # 盤中查當天:契約清單有、結算價全空。不快取、不採計。
        if verbose:
            print(f"[skip] {d} 尚未結算(結算價填充率 {cov:.0%}),收盤後再跑")
        return None, "unsettled"

    df.to_csv(rp, index=False, encoding="utf-8-sig")
    return df, "ok"


def cached_dates(market_code=None):
    """已有快取(含非交易日標記)的日期集合。"""
    mc = config.MARKET_CODE if market_code is None else market_code
    prefix = f"TXO_{_SESSION_NAME.get(mc, mc)}_"
    out = set()
    if not os.path.isdir(config.RAW_DIR):
        return out
    for fn in os.listdir(config.RAW_DIR):
        if not fn.startswith(prefix):
            continue
        m = re.search(r"_(\d{8})\.csv", fn)
        if m:
            out.add(datetime.strptime(m.group(1), "%Y%m%d").date())
    return out


def fetch_official_vix(year_month):
    """官方每日收盤 30 天 VIX(驗證基準)。year_month 例:'202607'。

    月檔格式(tab 分隔):交易日期 / 時間 / 波動率指數 / 收盤前1分鐘平均。
    只保留最近 3 個月的月檔,更早的期交所不提供。
    """
    config.ensure_dirs()
    ym = str(year_month)
    cache = os.path.join(config.META_DIR, f"official_vix_{ym}.csv")
    if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 86400:
        return pd.read_csv(cache, parse_dates=["trade_date"])

    r = requests.get(config.VIX_DAILY_URL.format(ym=ym),
                     headers={"User-Agent": config.USER_AGENT},
                     timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    rows = []
    for line in r.content.decode("utf-8", "replace").splitlines():
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) < 3 or not re.fullmatch(r"\d{8}", parts[0]):
            continue
        nums = [p for p in parts[2:] if re.fullmatch(r"-?\d+(\.\d+)?", p)]
        if not nums:
            continue
        rows.append({
            "trade_date": pd.to_datetime(parts[0], format="%Y%m%d"),
            "official_vix": float(nums[0]),
            "official_vix_1min": float(nums[1]) if len(nums) > 1 else None,
        })
    out = pd.DataFrame(rows)
    if len(out):
        out.to_csv(cache, index=False, encoding="utf-8-sig")
    return out


def fetch_official_vix_range(months):
    """多個月份併起來。months: ['202606','202607',...]"""
    frames = []
    for ym in months:
        try:
            d = fetch_official_vix(ym)
            if len(d):
                frames.append(d)
        except Exception as e:                  # noqa: BLE001
            print(f"[warn] 官方 VIX {ym} 取得失敗: {e}")
    if not frames:
        return pd.DataFrame(columns=["trade_date", "official_vix",
                                     "official_vix_1min"])
    return (pd.concat(frames, ignore_index=True)
              .drop_duplicates("trade_date")
              .sort_values("trade_date")
              .reset_index(drop=True))


def load_official_vix_all(refresh_months=4):
    """官方 VIX:先刷新最近幾個月,再把 meta/ 底下所有月檔快取併起來。

    期交所只提供最近 3 個月,所以定期執行才會累積出長期對照序列。
    """
    config.ensure_dirs()
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(refresh_months):
        months.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    fetch_official_vix_range(months)

    frames = []
    for fn in sorted(os.listdir(config.META_DIR)):
        if fn.startswith("official_vix_") and fn.endswith(".csv"):
            try:
                frames.append(pd.read_csv(os.path.join(config.META_DIR, fn),
                                          parse_dates=["trade_date"]))
            except Exception:                       # noqa: BLE001
                pass
    if not frames:
        return pd.DataFrame(columns=["trade_date", "official_vix",
                                     "official_vix_1min"])
    return (pd.concat(frames, ignore_index=True)
              .drop_duplicates("trade_date")
              .sort_values("trade_date")
              .reset_index(drop=True))


def fetch_fsp_table():
    """最後結算價一覽表 → DataFrame(expiry_code, settle_date)。

    給 2025/12/08 前沒有『契約到期日』欄的舊資料補到期日用。
    """
    config.ensure_dirs()
    cache = os.path.join(config.META_DIR, "fsp.csv")
    if os.path.exists(cache):
        age = time.time() - os.path.getmtime(cache)
        if age < 7 * 86400:
            return pd.read_csv(cache, dtype={"expiry_code": str},
                               parse_dates=["settle_date"])

    r = requests.get(config.FSP_URL, headers={"User-Agent": config.USER_AGENT},
                     timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    r.encoding = "utf-8"
    rows = []
    for t in pd.read_html(io.StringIO(r.text)):
        cols = [_norm_col(c) for c in t.columns]
        if len(cols) < 2 or "最後" not in cols[0]:
            continue
        t.columns = cols
        for _, row in t.iterrows():
            sd = pd.to_datetime(str(row[cols[0]]).strip(), errors="coerce")
            code = _clean_code(row[cols[1]])
            if pd.notna(sd) and code and code != "nan":
                rows.append({"expiry_code": code, "settle_date": sd})
    out = pd.DataFrame(rows).drop_duplicates("expiry_code")
    if len(out):
        out.to_csv(cache, index=False, encoding="utf-8-sig")
    return out
