from __future__ import annotations

import numpy as np
import pandas as pd


def _session_vwap(
    df: pd.DataFrame,
) -> pd.Series:
    """
    Calculate VWAP independently for each trading session.

    VWAP is reset at the start of every trading day.

    The dataframe is expected to contain an IST-based DatetimeIndex
    or another index where each calendar date represents a trading
    session.
    """

    typical_price = (
        df["high"]
        + df["low"]
        + df["close"]
    ) / 3.0

    session_date = pd.Series(
        df.index.date,
        index=df.index,
    )

    pv = (
        typical_price
        * df["volume"]
    )

    cumulative_pv = (
        pv.groupby(session_date)
        .cumsum()
    )

    cumulative_volume = (
        df["volume"]
        .groupby(session_date)
        .cumsum()
    )

    return (
        cumulative_pv
        / cumulative_volume.replace(
            0,
            np.nan,
        )
    )


def add_indicators(
    df: pd.DataFrame,
    rvol_lookback=20,
) -> pd.DataFrame:

    x = df.copy()

    if x.empty:
        return x

    # ---------------------------------------------------------
    # BASIC PRICE INDICATORS
    # ---------------------------------------------------------

    x["ema9"] = (
        x["close"]
        .ewm(
            span=9,
            adjust=False,
        )
        .mean()
    )

    x["ema20"] = (
        x["close"]
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    # ---------------------------------------------------------
    # SESSION VWAP
    # ---------------------------------------------------------

    x["vwap"] = _session_vwap(x)

    # ---------------------------------------------------------
    # RSI(14)
    # ---------------------------------------------------------

    delta = x["close"].diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = (
        gain
        .ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        )
        .mean()
    )

    avg_loss = (
        loss
        .ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        )
        .mean()
    )

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            np.nan,
        )
    )

    x["rsi14"] = (
        100
        - 100 / (1 + rs)
    )

    x["rsi_ema9"] = (
        x["rsi14"]
        .ewm(
            span=9,
            adjust=False,
        )
        .mean()
    )

    # ---------------------------------------------------------
    # ATR(14)
    # ---------------------------------------------------------

    prev_close = x["close"].shift(1)

    true_range = pd.concat(
        [
            x["high"] - x["low"],
            (x["high"] - prev_close).abs(),
            (x["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    x["atr14"] = (
        true_range
        .ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14,
        )
        .mean()
    )

    # ---------------------------------------------------------
    # RVOL
    # ---------------------------------------------------------

    x["avg_volume"] = (
        x["volume"]
        .shift(1)
        .rolling(
            rvol_lookback,
            min_periods=5,
        )
        .mean()
    )

    x["rvol"] = (
        x["volume"]
        / x["avg_volume"].replace(
            0,
            np.nan,
        )
    )

    # ---------------------------------------------------------
    # EMA20 SLOPE
    # ---------------------------------------------------------

    x["ema20_slope"] = (
        x["ema20"].diff(3)
    )

    # ---------------------------------------------------------
    # REMOVE ROWS WITHOUT REQUIRED INDICATORS
    # ---------------------------------------------------------

    return x.dropna(
        subset=[
            "ema9",
            "ema20",
            "vwap",
            "rsi14",
            "rsi_ema9",
            "atr14",
        ]
    )
