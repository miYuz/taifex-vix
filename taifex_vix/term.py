# -*- coding: utf-8 -*-
"""固定天期插值:由各到期日的 sigma^2 內插出 N 天 VIX。

CBOE 標準式的日數版本(權重是對 variance*T 做線性內插,再年化回 N 天):

    sigma^2_N = [ T1*sigma1^2*(d2-N)/(d2-d1) + T2*sigma2^2*(N-d1)/(d2-d1) ]
                * (365/N)
    VIX_N     = 100 * sqrt(sigma^2_N)

規則:
  - 候選 = 所有算得出 sigma^2 的到期日(週三週選 + 週五週選 + 月選)
  - 剛好有 d == N → 直接用,mode='exact'
  - 否則取最緊的一組 d1 <= N <= d2,mode='interp'
  - 夾不住 → NaN,mode='no_bracket'(不做外插)
"""
import math

from . import config


def _nan_result(N, mode, note=None):
    return {"target_days": N, "vix": float("nan"), "mode": mode,
            "d1": None, "d2": None, "code1": None, "code2": None,
            "note": note}


def interpolate(metrics, N, days_per_year=None):
    """metrics: compute_expiry 產出的 dict 串列。回傳固定天期結果 dict。"""
    dpy = config.DAYS_PER_YEAR if days_per_year is None else days_per_year
    usable = [m for m in metrics if m.get("ok")]
    if not usable:
        return _nan_result(N, "no_data", "當日沒有任何到期日算得出 variance")

    by_dte = {}
    for m in usable:                      # 同 dte 取 strip 較完整的
        prev = by_dte.get(m["dte"])
        if prev is None or m["strip_count"] > prev["strip_count"]:
            by_dte[m["dte"]] = m

    if N in by_dte:
        m = by_dte[N]
        return {"target_days": N, "vix": m["vix"], "mode": "exact",
                "d1": N, "d2": N, "code1": m["expiry_code"],
                "code2": m["expiry_code"], "note": None}

    below = [d for d in by_dte if d < N]
    above = [d for d in by_dte if d > N]
    mode = "interp"

    if below and above:
        d1, d2 = max(below), min(above)
    else:
        # 夾不住:通常是「週三 + 假期」把下一個序列推遠。是否短距離外插看 config。
        avail = sorted(by_dte)
        if config.MAX_EXTRAPOLATE_DAYS <= 0 or len(avail) < 2:
            return _nan_result(N, "no_bracket", f"可用到期日 dte={avail},夾不住 {N}")
        if not below:                       # N 比最近的到期日還短
            d1, d2, gap = avail[0], avail[1], avail[0] - N
        else:                               # N 比最遠的到期日還長
            d1, d2, gap = avail[-2], avail[-1], N - avail[-1]
        if gap > config.MAX_EXTRAPOLATE_DAYS:
            return _nan_result(N, "no_bracket",
                               f"可用到期日 dte={avail},外插距離 {gap} 天超過上限")
        mode = "extrap"

    m1, m2 = by_dte[d1], by_dte[d2]
    T1, T2 = d1 / dpy, d2 / dpy
    w1 = (d2 - N) / (d2 - d1)
    w2 = (N - d1) / (d2 - d1)

    var_n = (T1 * m1["variance"] * w1 + T2 * m2["variance"] * w2) * (dpy / N)
    if not (var_n > 0):
        return _nan_result(N, "bad_variance", f"外插/內插後 variance={var_n}")
    return {"target_days": N, "vix": 100.0 * math.sqrt(var_n), "mode": mode,
            "d1": d1, "d2": d2, "code1": m1["expiry_code"],
            "code2": m2["expiry_code"], "note": None}
