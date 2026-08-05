# taifex-vix

用**台灣期交所公開資料**計算台指選擇權（TXO）的 **7 天 / 14 天固定天期 VIX**。
免帳號、免券商 API，每日收盤資料，可回補歷史、可排程增量更新。

期交所官方只發布 **30 天** 波動率指數；這個專案用同一套 CBOE 公式做出**更短天期**的版本。

📊 **互動儀表板**：`https://<你的GitHub帳號>.github.io/taifex-vix/`
（push 並啟用 Pages 後生效，記得把這行換成實際網址）

---

## 資料現況

| | |
|---|---|
| 期間 | 2020-01-02 ~ 2026-08-04 |
| 交易日 | 1,599 |
| 輸出 | [`data/vix_txo_daily.csv`](data/vix_txo_daily.csv) / `.parquet` |
| 交易時段 | 一般交易時段（日盤）收盤 |

**驗證**：自算的 30 天 VIX 對照期交所官方收盤指數 —— **MAE 0.296、相關 0.9857**（64 個重疊交易日）。
30 天只是拿來校準引擎用的，7/14 天才是產出重點。

---

## 快速開始

```bash
pip install -r requirements.txt
```

```bash
python -m taifex_vix.cli daily
```

其他常用指令：

```bash
python -m taifex_vix.cli backfill --start 2020-01-01
```

```bash
python -m taifex_vix.cli day 2026-07-31 --show
```

```bash
python -m taifex_vix.selftest
```

```bash
python -m taifex_vix.build_dashboard docs/index.html
```

資料預設寫到 `D:\taifex_vix`，用環境變數改：

```bash
set TAIFEX_VIX_DATA=C:\your\path
```

---

## 方法

1. **逐到期日** variance swap（CBOE / 期交所公式）

   `σ²_T = (2/T) Σ (ΔK_i/K_i²) e^{rT} Q(K_i) − (1/T)(F/K0 − 1)²`

2. **固定天期插值**到 N 天

   `σ²_N = [T₁σ₁²(d₂−N)/(d₂−d₁) + T₂σ₂²(N−d₁)/(d₂−d₁)] × 365/N`

3. 候選到期日 = 週三週選 + 週五週選 + 月選，條件 `dte ≥ 1`。剛好有 `dte == N` 就直接用
   （`mode=exact`）；夾不住時允許最多 2 天的外插（`extrap`），再遠就留空（`no_bracket`）。

4. `T` = 整日數 / 365；`r` = 0（7~14 天 `e^{rT}` 影響 < 0.05%）。

5. `F` 由 put-call parity 取 `K* = argmin|C−P|`；`K0` = 最大的 `K ≤ F`；
   strip 從 `K0` 往兩翼延伸，連續 2 檔無有效價即截斷（CBOE 規則）。

### Q(K) 的價格來源與品質閘門 ← 最關鍵的一段

預設 `PRICE_SOURCE = "ba_first"`：買賣中價優先，**但要過兩道閘門**（相對價差 ≤ 30%、
與結算價偏離 ≤ 30%），不過關就回退結算價。

期交所 EOD 的「最後最佳買價/賣價」對冷門履約價常是隔很久的**殘留報價**。實測 2026/07/31
出現 `bid 0.5 / ask 363`（中價是結算價的 13 倍）。用 43 個交易日對照官方 30 天 VIX：

| Q(K) 來源 | MAE | RMSE | 相關係數 |
|---|---|---|---|
| 買賣中價，不過濾 | 1.023 | 1.303 | 0.902 |
| **買賣中價 + 品質閘門**（預設） | **0.293** | **0.422** | **0.991** |
| 只用結算價 | 0.303 | 0.427 | 0.990 |

閘門讓誤差降到 1/3.5。副作用是 `ba_ratio`（真正採用中價的比例）通常只有 10~30%，
這是正常的 —— 濾掉才對得上官方。

---

## 這個值可不可信：看插值跨度

`vix{N}_span` = 用來插值的兩個到期日相距幾天，是可信度的直接指標。
2022 年以前期交所掛的週選少，近月階梯稀疏，跨度就寬：

| 年 | 7 天跨度中位數 | p90 | VIX7 有值 |
|---|---|---|---|
| 2020–2022 | **14 天** | 28 | 98.0–99.6% |
| 2023–2024 | 7 天 | 7 | 97.9–98.3% |
| 2025 | 5 天 | 7 | 99.6% |
| 2026 | **2 天** | 5 | 99.3% |

跨度對誤差的影響是量出來的（拿 2023 年後密階梯的日子當真值，把階梯人工稀疏化成
2020 年的樣子再還原）：**跨度 7 天時 MAE 約 0.3，拉到 15 天約 0.9。**

**2022 年以前的值可以用，但要放寬容忍度**，別跟 2023 年後的值當成同一個精度。

### 為什麼用 CBOE 線性法

也測過 loglinear、σ 對 √t 線性、單調三次樣條（PCHIP）。從稀疏階梯還原真值：

| 方法 | 7 天 MAE (n=225) | 14 天 MAE (n=49) |
|---|---|---|
| **linear（CBOE，採用）** | **0.919** | 0.821 |
| loglinear | 0.989 | 0.647 |
| σ~√t | 1.104 | **0.620** |
| PCHIP | 0.938 | 0.876 |

差距小且方向不一致，證據不足以推翻標準法，且換掉會破壞跟官方的 0.296 對照。

### 外插上限為什麼是 2 天

同樣是量出來的（把短端逐步砍掉製造各種外插距離）：

| 外插距離 | 7 天 MAE | 7 天 p90 | 14 天 MAE |
|---|---|---|---|
| 1 天 | 0.61 | 1.40 | 0.22 |
| **2 天（上限）** | **1.23** | **2.84** | **0.46** |
| 3 天 | 1.55 | 3.07 | — |
| 7 天 | 5.96 | 12.17 | — |

3 天以後急遽惡化，7 天已 MAE 6.0 —— 那種數字比 NaN 更糟，因為它看起來像正常值。

---

## 資料來源

| 用途 | 端點 |
|---|---|
| 選擇權每日行情 | 期交所 `/cht/3/optDailyMarketExcel?commodity_id=TXO&marketCode=0&queryDate=YYYY/MM/DD` |
| 官方 30 天 VIX（驗證基準） | 期交所 `/file/taifex/Dailydownload/vix/log2data/YYYYMMnew.txt`（只留最近 3 個月） |
| 最後結算日對照表 | 期交所 `/cht/5/optIndxFSP` |
| 0050 日收盤價 | 證交所 `/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMM01&stockNo=0050` |

**到期日解析**：2025/12/08 起行情表才有「契約到期日」欄，更早的分四層補
（`col` → `fsp` → `empirical` → `rule`），每筆記錄在 `expiry_source`。
`backfill` 刻意分兩趟跑（先抓齊原始資料 → 再算 VIX），就是為了讓 `empirical` 有完整資料可用。

**0050**：證交所給的是未還原權值價格，0050 在 **2025-06-18 做過 1:4 分割**，
`twse.split_adjusted()` 會偵測並按比例還原。2025-06-11~06-17 無價格是分割前的停止交易期間。

---

## 主輸出欄位

| 欄位 | 說明 |
|---|---|
| `trade_date` | 交易日 |
| `vix7` / `vix14` / `vix30` | 固定天期 VIX |
| `vix{N}_mode` | `exact` / `interp` / `extrap` / `no_bracket` |
| `vix{N}_span` | 插值跨度（天）—— **可信度指標** |
| `vix{N}_d1` / `_d2` / `_code1` / `_code2` | 插值用的兩個到期日與代碼 |
| `close_0050` | 0050 收盤價（已還原分割） |
| `ba_ratio` | strip 中採用買賣中價的比例 |
| `strip_min` | 當天用到的最少 strip 檔數 |
| `expiry_src` | 到期日解析來源 |
| `warnings` | 近月被剔除的到期日與原因 |

---

## 每日自動更新（Windows）

[`taifex_vix/run_daily.bat`](taifex_vix/run_daily.bat) 做兩件事：更新資料 → 重建
`docs/index.html`。用工作排程器設每週一~五 15:15：

```bat
schtasks /Create /TN "TaifexVIX_Daily" /TR "<repo>\taifex_vix\run_daily.bat" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:15 /F
```

期交所結算價實測落在 **14:32 ~ 15:12** 之間公布，所以 15:15 排程 + `--wait 60` 會穩穩抓到。
盤中執行會偵測到「結算價全空」而拒收，自動退回前一個交易日。

### ⚠ run_daily.bat 只能放 ASCII

cmd.exe 用系統 OEM 編碼（zh-TW 是 Big5）讀 .bat。檔案存成 UTF-8 的話中文註解會被誤解碼並
**破壞指令解析** —— 實測連 `cd` 那行都被吃掉，整個腳本什麼都沒做，但 `exit /b` 仍回傳 0，
排程顯示「執行成功」。**也不要加 `chcp`**：中途切換編碼會讓 cmd 用錯位的偏移重讀剩餘位元組。
要讓 Python 的中文訊息在 log 裡正常，`set PYTHONIOENCODING=utf-8` 就夠。

### anaconda 的 ssl 地雷

從一般 shell（非 Anaconda Prompt）直接叫 `anaconda3\python.exe` 會
`ImportError: DLL load failed while importing _ssl`，因為 conda 的 OpenSSL DLL 在
`<prefix>\Library\bin`，只有 `conda activate` 才會進 PATH。
[`bootstrap.py`](taifex_vix/bootstrap.py) 會在 import 失敗時用 `os.add_dll_directory()` 補上。

因此 `fetch.py` 裡 `from . import config` 必須排在 `import requests` **之前**。

---

## 專案結構

```
taifex_vix/
    bootstrap.py       anaconda ssl DLL 修補
    config.py          端點、品質閘門、目標天期
    fetch.py           抓取 / 解析 / 快取、官方 VIX、結算保護
    twse.py            0050 收盤價 + 分割還原
    expiry.py          四層到期日解析
    vix_core.py        forward / K0 / strip / variance
    term.py            固定天期插值
    pipeline.py        單日 orchestration、upsert、落地
    cli.py             backfill / daily / day
    build_dashboard.py 由 CSV 重建儀表板
    selftest.py        15 項離線回歸檢查
    dashboard/         儀表板樣板(HTML + JS)
data/                  主輸出 CSV / parquet
docs/index.html        儀表板(GitHub Pages 來源)
notebooks/             驗證報告
```

---

## 免責

本專案僅為公開資料的整理與計算，**不構成任何投資建議**。
資料來自台灣期貨交易所與證券交易所公開資訊，正確性以官方公告為準。
