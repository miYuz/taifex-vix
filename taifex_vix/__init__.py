# -*- coding: utf-8 -*-
"""期交所公開資料 → TXO 7 天 / 14 天固定天期 VIX。

免帳號、免 Shioaji 連線,每日收盤資料,可回補歷史 + 每日增量。
與 claude_txf/txo_vix(即時系統)完全獨立。
"""
__all__ = ["config", "fetch", "expiry", "vix_core", "term", "pipeline"]
