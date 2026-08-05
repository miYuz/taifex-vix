# -*- coding: utf-8 -*-
"""單一到期日的 variance swap 計算(CBOE / 期交所 VIX 方法論)。

    sigma^2_T = (2/T) * sum( dK_i / K_i^2 * e^{rT} * Q(K_i) )
                - (1/T) * (F/K0 - 1)^2

數學核心移植自 claude_txf/txo_vix/vix.py(即時系統已驗證),改動:
  - 價格 Q(K):買賣中價優先,缺報價回退結算價(EOD 公開資料深價外常無買賣價)
  - T 用整日數 / 365(日頻收盤序列的 convention),不用秒級到期時點
  - 沒有 TXF 即時價可 fallback,parity 失敗該到期日直接出局
"""
import math

import numpy as np
import pandas as pd

from . import config

PRICE_COLS = ["bid", "ask", "settle"]


def _price(bid, ask, settle, price_source=None):
    """回傳 (price, source)。source ∈ {'ba', 'settle', None}。

    買賣中價要過兩道品質閘門才採用(見 config 的說明),否則退回結算價。
    """
    ps = config.PRICE_SOURCE if price_source is None else price_source
    has_settle = pd.notna(settle) and settle > 0

    if ps == "ba_first" and pd.notna(bid) and pd.notna(ask) and bid > 0 and ask > bid:
        mid = (bid + ask) / 2.0
        rel_spread = (ask - bid) / mid
        dev_ok = (not has_settle or
                  abs(mid / float(settle) - 1.0) <= config.MAX_DEV_FROM_SETTLE)
        if rel_spread <= config.MAX_REL_SPREAD and dev_ok:
            return mid, "ba"

    if has_settle:
        return float(settle), "settle"
    return None, None


def make_t_quote(df_exp, price_source=None):
    """單一到期日的 tidy 表 → T 字報價(一列一個履約價,左 Put 右 Call)。"""
    rows = []
    for right, prefix in (("P", "put"), ("C", "call")):
        d = df_exp[df_exp["right"] == right]
        for r in d.itertuples():
            px, src = _price(r.bid, r.ask, r.settle, price_source)
            rows.append({
                "strike": float(r.strike), "side": prefix,
                f"{prefix}_px": px, f"{prefix}_src": src,
                f"{prefix}_oi": getattr(r, "oi", np.nan),
                f"{prefix}_vol": getattr(r, "vol_day", np.nan),
            })
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    put = (df[df["side"] == "put"]
           .drop(columns=["side", "call_px", "call_src", "call_oi", "call_vol"],
                 errors="ignore"))
    call = (df[df["side"] == "call"]
            .drop(columns=["side", "put_px", "put_src", "put_oi", "put_vol"],
                  errors="ignore"))
    t = pd.merge(put, call, on="strike", how="outer")
    return t.sort_values("strike").reset_index(drop=True)


def estimate_forward(t_quote, r=None, T=None):
    """Put-Call parity 估 F:K* = argmin|C-P|,F = K* + e^{rT}(C-P)。

    先只用兩邊都是真實買賣中價的履約價;沒有才退而用含結算價的。
    回傳 dict(F, K_star, method) 或 None。
    """
    r = config.RISK_FREE if r is None else r
    ert = math.exp(r * T) if (T and r) else 1.0

    both = t_quote[t_quote["put_px"].notna() & t_quote["call_px"].notna()].copy()
    if both.empty:
        return None

    ba = both[(both["put_src"] == "ba") & (both["call_src"] == "ba")]
    use, method = (ba, "parity_ba") if len(ba) >= 3 else (both, "parity_mixed")

    use = use.assign(_gap=(use["call_px"] - use["put_px"]).abs())
    row = use.sort_values("_gap").iloc[0]
    K_star = float(row["strike"])
    F = K_star + ert * (float(row["call_px"]) - float(row["put_px"]))
    return {"F": F, "K_star": K_star, "method": method}


def find_k0(strikes, F):
    ks = [k for k in strikes if k <= F]
    return max(ks) if ks else None


def build_strip(t_quote, K0, max_consecutive_invalid=None):
    """OTM strip:K<K0 用 Put、K>K0 用 Call、K=K0 取兩者平均。

    仿 CBOE:自 K0 向兩翼延伸,連續 N 檔無有效價即截斷該翼。
    """
    if max_consecutive_invalid is None:
        max_consecutive_invalid = config.MAX_CONSECUTIVE_INVALID

    tq = t_quote.sort_values("strike").reset_index(drop=True)
    idx = tq.index[tq["strike"] == K0]
    if len(idx) == 0:
        return pd.DataFrame()
    k0_i = int(idx[0])

    def ok(row, side):
        px = row[f"{side}_px"]
        return pd.notna(px) and px > 0

    rows = []
    r0 = tq.loc[k0_i]
    if ok(r0, "put") and ok(r0, "call"):
        rows.append({"strike": float(K0),
                     "Q": (float(r0["put_px"]) + float(r0["call_px"])) / 2.0,
                     "right_used": "CP_AVG",
                     "src": f"{r0['put_src']}/{r0['call_src']}"})
    elif ok(r0, "put"):
        rows.append({"strike": float(K0), "Q": float(r0["put_px"]),
                     "right_used": "P", "src": r0["put_src"]})
    elif ok(r0, "call"):
        rows.append({"strike": float(K0), "Q": float(r0["call_px"]),
                     "right_used": "C", "src": r0["call_src"]})

    for rng, side, tag in ((range(k0_i - 1, -1, -1), "put", "P"),
                           (range(k0_i + 1, len(tq)), "call", "C")):
        miss = 0
        for i in rng:
            row = tq.loc[i]
            if ok(row, side):
                rows.append({"strike": float(row["strike"]),
                             "Q": float(row[f"{side}_px"]),
                             "right_used": tag, "src": row[f"{side}_src"]})
                miss = 0
            else:
                miss += 1
                if miss >= max_consecutive_invalid:
                    break

    return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)


def compute_variance(strip_df, F, K0, T, r=None):
    """中點 dK + variance swap 公式。回傳 (variance, strip_df 加欄)。"""
    r = config.RISK_FREE if r is None else r
    s = strip_df.copy()
    ks = s["strike"].to_numpy(dtype=float)
    if len(ks) < 2:
        return float("nan"), s

    dk = np.empty(len(ks))
    dk[0] = ks[1] - ks[0]
    dk[-1] = ks[-1] - ks[-2]
    if len(ks) > 2:
        dk[1:-1] = (ks[2:] - ks[:-2]) / 2.0
    s["delta_k"] = dk

    ert = math.exp(r * T)
    s["contribution"] = s["delta_k"] / (s["strike"] ** 2) * ert * s["Q"]
    variance = (2.0 / T) * s["contribution"].sum() - (1.0 / T) * ((F / K0 - 1.0) ** 2)
    return variance, s


def compute_expiry(df_exp, dte, expiry_code=None, price_source=None):
    """單一到期日:T → F → K0 → strip → variance。

    回傳 metrics dict(失敗時帶 ok=False 與 reason),永遠不 raise。
    """
    base = {"expiry_code": expiry_code, "dte": int(dte), "ok": False,
            "reason": None, "T": None, "F": None, "F_method": None,
            "K_star": None, "K0": None, "variance": None, "vix": None,
            "strip_count": 0, "n_ba": 0, "n_settle": 0, "ba_ratio": None,
            "strike_min": None, "strike_max": None}

    if dte < config.MIN_DTE:
        base["reason"] = f"dte={dte} < {config.MIN_DTE}"
        return base, pd.DataFrame()

    T = dte / config.DAYS_PER_YEAR
    base["T"] = T

    t_quote = make_t_quote(df_exp, price_source=price_source)
    if t_quote.empty:
        base["reason"] = "無報價"
        return base, pd.DataFrame()

    fwd = estimate_forward(t_quote, T=T)
    if fwd is None:
        base["reason"] = "parity 候選不足,無法估 forward"
        return base, pd.DataFrame()
    base.update({"F": fwd["F"], "F_method": fwd["method"],
                 "K_star": fwd["K_star"]})

    ks = t_quote["strike"].dropna().unique()
    K0 = find_k0(ks, fwd["F"])
    if K0 is None:
        # 崩盤日會出現:履約價階梯來不及往下加掛,F 掉到最低履約價之下,
        # 整個 put 翼不存在 → strip 必然嚴重低估,寧可不出數字。
        base["reason"] = (f"F={fwd['F']:.0f} 低於最低履約價 {min(ks):.0f} "
                          f"(階梯 {min(ks):.0f}~{max(ks):.0f}),put 翼缺失")
        return base, pd.DataFrame()
    base["K0"] = K0

    strip = build_strip(t_quote, K0)
    base["strip_count"] = len(strip)
    if len(strip) < config.MIN_STRIP_COUNT:
        base["reason"] = f"strip 只有 {len(strip)} 檔 (<{config.MIN_STRIP_COUNT})"
        return base, strip

    n_ba = int(strip["src"].astype(str).str.contains("ba").sum())
    base.update({"n_ba": n_ba, "n_settle": len(strip) - n_ba,
                 "ba_ratio": n_ba / len(strip),
                 "strike_min": float(strip["strike"].min()),
                 "strike_max": float(strip["strike"].max())})

    variance, strip = compute_variance(strip, fwd["F"], K0, T)
    if not np.isfinite(variance) or variance <= 0:
        base["reason"] = f"variance={variance} 非正"
        base["variance"] = variance
        return base, strip

    base.update({"variance": float(variance), "ok": True,
                 "vix": 100.0 * math.sqrt(variance)})
    return base, strip
