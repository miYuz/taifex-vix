# -*- coding: utf-8 -*-
"""taifex_vix 設定。

路徑分兩類,可各自用環境變數覆蓋:
    TAIFEX_VIX_DATA  快取與診斷檔(原始行情約 1500 檔,體積大,不進版控)
    TAIFEX_VIX_OUT   主輸出 CSV/parquet(小,要進版控);預設跟 DATA_ROOT 同位置
"""
import os

from . import bootstrap

# import 時就把 anaconda 的 ssl 修好,後面 requests 才不會炸
SSL_OK, SSL_NOTE = bootstrap.ensure_ssl()

DATA_ROOT = os.environ.get("TAIFEX_VIX_DATA", r"D:\taifex_vix")
RAW_DIR = os.path.join(DATA_ROOT, "raw")
DETAIL_DIR = os.path.join(DATA_ROOT, "detail")
META_DIR = os.path.join(DATA_ROOT, "meta")

# 設 TAIFEX_VIX_OUT 指到 repo 的 data/,每日更新就直接寫進版控目錄,
# commit 前不用再搬檔案。
OUT_DIR = os.environ.get("TAIFEX_VIX_OUT", DATA_ROOT)
OUT_CSV = os.path.join(OUT_DIR, "vix_txo_daily.csv")
OUT_PARQUET = os.path.join(OUT_DIR, "vix_txo_daily.parquet")

# ---- 期交所端點 ----
OPT_DAILY_URL = "https://www.taifex.com.tw/cht/3/optDailyMarketExcel"
FSP_URL = "https://www.taifex.com.tw/cht/5/optIndxFSP"      # 最後結算價一覽表
# 官方每日收盤 VIX(驗證基準),月檔:.../vix/log2data/YYYYMMnew.txt
VIX_DAILY_URL = ("https://www.taifex.com.tw/file/taifex/Dailydownload"
                 "/vix/log2data/{ym}new.txt")

COMMODITY_ID = "TXO"
MARKET_CODE = 0          # 0=一般交易時段(日盤), 1=盤後交易時段(夜盤)

USER_AGENT = "Mozilla/5.0"
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3
REQUEST_SLEEP = 1.2      # 每次真的發出 HTTP 後的禮貌間隔(秒)

# ---- 選擇權價格來源 ----
# 'ba_first' = 買賣中價優先、缺(或品質不過關)回退結算價
# 'settle'   = 只用結算價
PRICE_SOURCE = "ba_first"
# EOD 的「最後最佳買賣價」對冷門履約價常是隔很久的殘留報價,寬得離譜
# (實測 2026/07/31 出現 bid 0.5 / ask 363,中價是結算價的 13 倍)。
# 兩道閘門沒過就當這個 mid 不可信,改用結算價。
MAX_REL_SPREAD = 0.30        # (ask-bid)/mid
MAX_DEV_FROM_SETTLE = 0.30   # |mid/settle - 1|

# 盤中查當天,期交所會回整份契約清單但『結算價全部空白』(結算價收盤後才出)。
# 這時只剩品質差的殘留買賣價,算出來的 VIX 會失真且無從察覺,所以直接視為
# 「當日尚未結算」不予採計。收盤後(約 15:00)再跑就正常。
MIN_SETTLE_COVERAGE = 0.5

# ---- VIX 計算參數 ----
TARGET_DAYS = (7, 14, 30)    # 30 天只是拿來跟官方 VIX 對照驗證引擎
RISK_FREE = 0.0              # 7~14 天 e^{rT} 影響 <0.05%,取 0
DAYS_PER_YEAR = 365.0
MIN_DTE = 1                  # 排除當日結算(dte=0)的序列

# 目標天期夾不住時,允許往外延伸幾天(mode 標 'extrap');超過就維持 NaN。
#
# 夾不住的成因有兩種:
#   1. 週三 + 前後有假期 —— 當天到期的序列 dte=0 被排除,下一個被假期推遠
#   2. 2022 年以前期交所掛的週選少,近月階梯本來就稀疏
#
# 上限 2 是量出來的,不是猜的。作法:拿 2023 年後階梯密的日子當真值,
# 把短端逐步砍掉製造各種外插距離,再看誤差(scratchpad/extrap_test.py):
#
#   外插距離   7天 MAE   7天 p90    14天 MAE
#      1天       0.61      1.40       0.22
#      2天       1.23      2.84       0.46
#      3天       1.55      3.07        —
#      4天       2.36      4.89        —
#      7天       5.96     12.17        —
#
# 3 天以後急遽惡化,7 天已經 MAE 6.0 vol point —— 那種數字比 NaN 更糟,
# 因為它看起來像正常值。2 天以內誤差還在可用範圍。
MAX_EXTRAPOLATE_DAYS = 2
MIN_STRIP_COUNT = 5          # strip 檔數低於此值該到期日不可用
MAX_CONSECUTIVE_INVALID = 2  # CBOE:連續 2 檔無有效價就截斷該翼

DEFAULT_START = "2023-01-01"


def ensure_dirs():
    for d in (DATA_ROOT, RAW_DIR, DETAIL_DIR, META_DIR, OUT_DIR):
        os.makedirs(d, exist_ok=True)
    return DATA_ROOT
