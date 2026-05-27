from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import add_indicators


@dataclass(frozen=True)
class ScanThresholds:
    range_90d_max: float = 0.60
    impulse_min: float = 0.15
    pullback_min: float = 0.10
    pullback_max: float = 0.25
    min_above_60d_low: float = 0.10
    ma60_slope_min: float = -0.03
    min_avg_volume_lots: float = 1000.0
    high_pullback_min: float = 0.10
    breakout_pullback_near_pct: float = 0.05


def _clip_score(value: float, low: float, high: float) -> float:
    if pd.isna(value) or high == low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0, 1) * 100)


def _inverse_clip_score(value: float, low: float, high: float) -> float:
    if pd.isna(value) or high == low:
        return 0.0
    return float(np.clip((high - value) / (high - low), 0, 1) * 100)


def _recent_impulse(window: pd.DataFrame) -> dict:
    low_pos = int(window["low"].idxmin())
    after_low = window.loc[low_pos:]
    high_pos = int(after_low["high"].idxmax()) if not after_low.empty else int(window["high"].idxmax())

    low_price = float(window.loc[low_pos, "low"])
    high_price = float(window.loc[high_pos, "high"])
    impulse_return = (high_price - low_price) / low_price if low_price else np.nan

    impulse_volume_avg = window.loc[low_pos:high_pos, "volume"].mean()
    pullback_volume_avg = window.loc[high_pos:, "volume"].mean()
    return {
        "recent_low_30d": low_price,
        "recent_high_30d": high_price,
        "recent_low_date": str(window.loc[low_pos, "date"].date()),
        "recent_high_date": str(window.loc[high_pos, "date"].date()),
        "impulse_return": impulse_return,
        "impulse_volume_avg": impulse_volume_avg,
        "pullback_volume_avg": pullback_volume_avg,
    }


def _risk_reward(entry: float, target: float, stop: float) -> float:
    risk = entry - stop
    reward = target - entry
    if risk <= 0 or reward <= 0:
        return np.nan
    return reward / risk


def _fib_zone(position: float) -> str:
    if pd.isna(position):
        return "N/A"
    if position < 0:
        return "<0 低於區間低點"
    if position < 0.236:
        return "0-0.236 弱勢低檔"
    if position < 0.382:
        return "0.236-0.382 初步反彈"
    if position < 0.5:
        return "0.382-0.500 中段偏弱"
    if position < 0.618:
        return "0.500-0.618 中段偏強"
    if position < 0.786:
        return "0.618-0.786 強勢修復"
    if position < 0.95:
        return "0.786-0.950 前高壓力區"
    if position <= 1.05:
        return "0.950-1.050 突破附近"
    return ">1.050 突破延伸"


def _breakout_events(data: pd.DataFrame) -> pd.DataFrame:
    records = []
    lookbacks = [60, 90, 120, 180, 300]
    for lookback in lookbacks:
        col = f"prior_{lookback}_high"
        data[col] = (
            data["high"]
            .shift(1)
            .rolling(lookback, min_periods=max(20, lookback // 2))
            .max()
        )
        frame = data[(data["high"] > data[col]) & data[col].notna()][
            ["date", "high", col]
        ].copy()
        if frame.empty:
            continue
        frame["breakout_base_price"] = frame[col]
        frame["breakout_lookback_days"] = lookback
        records.append(
            frame[["date", "high", "breakout_base_price", "breakout_lookback_days"]]
        )
    if not records:
        return pd.DataFrame(
            columns=["date", "high", "breakout_base_price", "breakout_lookback_days"]
        )
    return (
        pd.concat(records, ignore_index=True)
        .sort_values(["date", "breakout_lookback_days"])
        .groupby("date", as_index=False)
        .tail(1)
        .tail(90)
    )


def scan_one(df: pd.DataFrame, thresholds: ScanThresholds = ScanThresholds()) -> dict | None:
    if len(df) < 260:
        return None

    data = add_indicators(df).reset_index(drop=True)
    last = data.iloc[-1]
    prev = data.iloc[-2]
    if pd.isna(last["ma250"]) or pd.isna(last["ma60"]):
        return None

    w90 = data.tail(90)
    w180 = data.tail(180)
    w60 = data.tail(60)
    w30 = data.tail(30)
    w20_prev = w60.head(20)
    w20_mid = w60.iloc[20:40]
    w20_last = w60.tail(20)

    range_90d = (w90["high"].max() - w90["low"].min()) / w90["low"].min()
    ma60_slope = (last["ma60"] / data.iloc[-20]["ma60"] - 1) if not pd.isna(data.iloc[-20]["ma60"]) else np.nan
    low_no_break = (
        w20_last["low"].min() >= w20_mid["low"].min() * 0.97
        and w20_mid["low"].min() >= w20_prev["low"].min() * 0.97
    )

    impulse = _recent_impulse(w30)
    impulse_return = impulse["impulse_return"]
    recent_high = impulse["recent_high_30d"]
    recent_low = impulse["recent_low_30d"]
    pullback = (recent_high - last["close"]) / recent_high if recent_high else np.nan
    wave = _recent_impulse(w180)
    fib_low_price = wave["recent_low_30d"]
    fib_high_price = wave["recent_high_30d"]
    fib_position = (
        (last["close"] - fib_low_price) / (fib_high_price - fib_low_price)
        if fib_high_price and fib_low_price and fib_high_price != fib_low_price
        else np.nan
    )
    fib_zone = _fib_zone(fib_position)
    breakout_events = _breakout_events(data)
    prior_breakout_events = breakout_events[breakout_events["date"] < last["date"]]
    intraday_breakout_events = breakout_events[breakout_events["date"] == last["date"]]
    if prior_breakout_events.empty:
        breakout_base_price = np.nan
        breakout_high_after_base = np.nan
        distance_to_breakout_base = np.nan
        breakout_lookback_days = np.nan
    else:
        breakout_event = prior_breakout_events.iloc[-1]
        breakout_base_price = float(breakout_event["breakout_base_price"])
        breakout_lookback_days = float(breakout_event["breakout_lookback_days"])
        breakout_date = breakout_event["date"]
        post_breakout = data[data["date"] >= breakout_date]
        breakout_high_after_base = float(post_breakout["high"].max())
        distance_to_breakout_base = (
            last["close"] / breakout_base_price - 1 if breakout_base_price else np.nan
        )
    if intraday_breakout_events.empty:
        intraday_breakout_base_price = np.nan
        intraday_breakout_high = np.nan
        intraday_distance_to_breakout_base = np.nan
        intraday_breakout_lookback_days = np.nan
    else:
        intraday_breakout_event = intraday_breakout_events.iloc[-1]
        intraday_breakout_base_price = float(intraday_breakout_event["breakout_base_price"])
        intraday_breakout_lookback_days = float(
            intraday_breakout_event["breakout_lookback_days"]
        )
        intraday_breakout_high = float(last["high"])
        intraday_distance_to_breakout_base = (
            last["close"] / intraday_breakout_base_price - 1
            if intraday_breakout_base_price
            else np.nan
        )
    low_60d = float(w60["low"].min())
    bottom_rise = (last["close"] / low_60d - 1) if low_60d else np.nan
    avg_volume_30_lots = last["vol_ma30"] / 1000 if not pd.isna(last["vol_ma30"]) else np.nan
    today_volume_lots = last["volume"] / 1000 if not pd.isna(last["volume"]) else np.nan
    volume_surge_multiple = last["volume"] / last["vol_ma30"] if not pd.isna(last["vol_ma30"]) and last["vol_ma30"] else np.nan
    prev_volume_surge_multiple = (
        prev["volume"] / prev["vol_ma30"]
        if not pd.isna(prev["vol_ma30"]) and prev["vol_ma30"]
        else np.nan
    )
    prev_range = prev["high"] - prev["low"]
    prev_close_position = (prev["close"] - prev["low"]) / prev_range if prev_range else np.nan

    impulse_volume_avg = impulse["impulse_volume_avg"]
    pullback_volume_avg = impulse["pullback_volume_avg"]
    volume_ratio = pullback_volume_avg / impulse_volume_avg if impulse_volume_avg else np.nan
    impulse_volume_multiple = impulse_volume_avg / data["volume"].tail(90).mean()

    stop_signals = {
        "5d_low_stopped_falling": bool(data.tail(5)["low"].iloc[-1] > data.tail(5)["low"].iloc[:-1].min()),
        "close_above_ma5": bool(last["close"] > last["ma5"]),
        "rsi14_rebounding": bool(last["rsi14"] > data["rsi14"].iloc[-4:-1].min() and last["rsi14"] > 35),
        "volume_above_5d_avg": bool(last["volume"] > last["vol_ma5"]),
        "red_candle": bool(last["close"] > last["open"]),
    }
    stop_signal_count = sum(stop_signals.values())

    conditions = {
        "low_stage_near_or_below_ma250": bool(
            last["close"] <= last["ma250"] * 1.08 and last["ma60"] <= last["ma250"] * 1.03
        ),
        "range_90d_tight": bool(range_90d < thresholds.range_90d_max),
        "60d_low_not_breaking": bool(low_no_break),
        "ma60_flat_or_turning": bool(ma60_slope >= thresholds.ma60_slope_min),
        "impulse_ge_30pct": bool(impulse_return >= thresholds.impulse_min),
        "close_above_recent_low_30d": bool(last["close"] > recent_low),
        "close_10pct_above_60d_low": bool(last["close"] > low_60d * (1 + thresholds.min_above_60d_low)),
        "stop_signals_ge_2": bool(stop_signal_count >= 2),
        "avg_volume_30d_ge_1000_lots": bool(avg_volume_30_lots >= thresholds.min_avg_volume_lots),
    }
    bottom_impulse_passed = all(conditions.values())
    high_pullback_passed = bool(
        avg_volume_30_lots >= thresholds.min_avg_volume_lots
        and pullback >= thresholds.high_pullback_min
        and 0.5 <= fib_position <= 0.786
    )
    breakout_pullback_passed = bool(
        avg_volume_30_lots >= thresholds.min_avg_volume_lots
        and not pd.isna(distance_to_breakout_base)
        and -thresholds.breakout_pullback_near_pct
        <= distance_to_breakout_base
        <= thresholds.breakout_pullback_near_pct
        and breakout_high_after_base > breakout_base_price * (1 + thresholds.breakout_pullback_near_pct)
    )
    intraday_breakout_pullback_passed = bool(
        avg_volume_30_lots >= thresholds.min_avg_volume_lots
        and not pd.isna(intraday_distance_to_breakout_base)
        and -thresholds.breakout_pullback_near_pct
        <= intraday_distance_to_breakout_base
        <= thresholds.breakout_pullback_near_pct
        and intraday_breakout_high
        > intraday_breakout_base_price * (1 + thresholds.breakout_pullback_near_pct)
    )
    volume_surge_passed = bool(
        avg_volume_30_lots >= thresholds.min_avg_volume_lots
        and volume_surge_multiple >= 1.6
    )
    volume_stop_reversal_passed = bool(
        avg_volume_30_lots >= thresholds.min_avg_volume_lots
        and prev_volume_surge_multiple >= 1.6
        and prev["close"] > prev["open"]
        and prev_close_position >= 0.65
        and prev["low"] <= low_60d * 1.10
        and last["close"] > prev["high"]
        and last["close"] > last["open"]
        and last["low"] >= prev["low"] * 0.98
        and last["volume"] >= last["vol_ma30"]
    )
    volume_stop_watch_passed = bool(
        avg_volume_30_lots >= thresholds.min_avg_volume_lots
        and prev_volume_surge_multiple >= 1.6
        and prev["close"] > prev["open"]
        and prev_close_position >= 0.65
        and prev["low"] <= low_60d * 1.10
        and last["close"] <= prev["high"]
        and last["low"] >= prev["low"] * 0.98
        and last["close"] >= prev["close"] * 0.98
    )
    passed = (
        bottom_impulse_passed
        or high_pullback_passed
        or breakout_pullback_passed
        or intraday_breakout_pullback_passed
        or volume_surge_passed
        or volume_stop_reversal_passed
        or volume_stop_watch_passed
    )

    impulse_return_score = _clip_score(impulse_return, 0.30, 0.80)
    base_tightness_score = _inverse_clip_score(range_90d, 0.20, thresholds.range_90d_max)
    low_stage_score = _inverse_clip_score(last["close"] / last["ma250"] - 1, -0.35, 0.08)
    bottoming_signal_score = min(stop_signal_count / 5 * 100, 100)
    volume_signal_score = (
        _clip_score(impulse_volume_multiple, 1.0, 2.5) * 0.6
        + _clip_score(avg_volume_30_lots, 1000, 3000) * 0.4
    )
    score = (
        impulse_return_score * 0.30
        + base_tightness_score * 0.20
        + low_stage_score * 0.20
        + bottoming_signal_score * 0.20
        + volume_signal_score * 0.10
    )
    high_pullback_score = (
        _clip_score(pullback, 0.10, 0.35) * 0.45
        + _clip_score(avg_volume_30_lots, 1000, 5000) * 0.25
        + _clip_score(impulse_return, 0.20, 1.00) * 0.20
        + _clip_score(impulse_volume_multiple, 1.0, 2.5) * 0.10
    )
    breakout_score = (
        _inverse_clip_score(
            abs(distance_to_breakout_base), 0.0, thresholds.breakout_pullback_near_pct
        )
        * 0.35
        + _clip_score(breakout_lookback_days, 60, 300) * 0.20
        + _clip_score(avg_volume_30_lots, 1000, 5000) * 0.15
        + _clip_score(impulse_return, 0.10, 0.80) * 0.15
        + bottoming_signal_score * 0.15
    )
    intraday_breakout_score = (
        _inverse_clip_score(
            abs(intraday_distance_to_breakout_base), 0.0, thresholds.breakout_pullback_near_pct
        )
        * 0.30
        + _clip_score(intraday_breakout_lookback_days, 60, 300) * 0.20
        + _clip_score(
            intraday_breakout_high / intraday_breakout_base_price - 1, 0.05, 0.18
        )
        * 0.20
        + _clip_score(avg_volume_30_lots, 1000, 5000) * 0.15
        + bottoming_signal_score * 0.15
    )
    volume_surge_score = (
        _clip_score(volume_surge_multiple, 1.6, 4.0) * 0.45
        + _clip_score(today_volume_lots, 1000, 20000) * 0.20
        + _clip_score(avg_volume_30_lots, 1000, 10000) * 0.15
        + bottoming_signal_score * 0.20
    )
    volume_stop_reversal_score = (
        _clip_score(prev_volume_surge_multiple, 1.6, 4.0) * 0.25
        + _clip_score(volume_surge_multiple, 1.0, 3.0) * 0.20
        + _clip_score(last["close"] / prev["high"] - 1, 0.0, 0.08) * 0.25
        + _clip_score(prev_close_position, 0.65, 1.0) * 0.15
        + bottoming_signal_score * 0.15
    )
    volume_stop_watch_score = (
        _clip_score(prev_volume_surge_multiple, 1.6, 4.0) * 0.30
        + _clip_score(prev_close_position, 0.65, 1.0) * 0.25
        + _inverse_clip_score(abs(last["close"] / prev["high"] - 1), 0.0, 0.08) * 0.25
        + bottoming_signal_score * 0.20
    )

    signal_pairs = []
    score_candidates = [score]
    if bottom_impulse_passed:
        signal_pairs.append(("bottom_impulse", "底部起漲型"))
        score_candidates.append(score)
    if high_pullback_passed:
        signal_pairs.append(("high_pullback_watch", "高檔回落觀察型"))
        score_candidates.append(high_pullback_score)
    if breakout_pullback_passed:
        signal_pairs.append(("breakout_pullback", "突破回踩型"))
        score_candidates.append(breakout_score)
    if intraday_breakout_pullback_passed:
        signal_pairs.append(("intraday_breakout_pullback", "日內突破回踩"))
        score_candidates.append(intraday_breakout_score)
    if volume_stop_reversal_passed:
        signal_pairs.append(("volume_stop_reversal", "量增止跌型"))
        score_candidates.append(volume_stop_reversal_score)
    if volume_stop_watch_passed:
        signal_pairs.append(("volume_stop_watch", "量增止跌觀察"))
        score_candidates.append(volume_stop_watch_score)
    if volume_surge_passed:
        signal_pairs.append(("volume_surge", "成交量暴增型"))
        score_candidates.append(volume_surge_score)

    if signal_pairs:
        signal_type = signal_pairs[0][0]
        signal_types = ",".join(pair[0] for pair in signal_pairs)
        tag = " / ".join(pair[1] for pair in signal_pairs)
        final_score = max(score_candidates)
    else:
        tag = "觀察中"
        signal_type = "watch"
        signal_types = "watch"
        final_score = score
    signal_keys = set(signal_types.split(","))
    if "breakout_pullback" in signal_keys:
        active_breakout_base_price = breakout_base_price
        active_breakout_distance = distance_to_breakout_base
        active_breakout_lookback_days = breakout_lookback_days
    elif "intraday_breakout_pullback" in signal_keys:
        active_breakout_base_price = intraday_breakout_base_price
        active_breakout_distance = intraday_distance_to_breakout_base
        active_breakout_lookback_days = intraday_breakout_lookback_days
    elif not pd.isna(breakout_base_price):
        active_breakout_base_price = breakout_base_price
        active_breakout_distance = distance_to_breakout_base
        active_breakout_lookback_days = breakout_lookback_days
    else:
        active_breakout_base_price = intraday_breakout_base_price
        active_breakout_distance = intraday_distance_to_breakout_base
        active_breakout_lookback_days = intraday_breakout_lookback_days

    five_day_stop = float(data.tail(5)["low"].min() * 0.97)
    fib_382_price = (
        fib_low_price + (fib_high_price - fib_low_price) * 0.382
        if fib_high_price and fib_low_price and fib_high_price != fib_low_price
        else np.nan
    )
    fib_236_price = (
        fib_low_price + (fib_high_price - fib_low_price) * 0.236
        if fib_high_price and fib_low_price and fib_high_price != fib_low_price
        else np.nan
    )
    fib_500_price = (
        fib_low_price + (fib_high_price - fib_low_price) * 0.500
        if fib_high_price and fib_low_price and fib_high_price != fib_low_price
        else np.nan
    )
    fib_618_price = (
        fib_low_price + (fib_high_price - fib_low_price) * 0.618
        if fib_high_price and fib_low_price and fib_high_price != fib_low_price
        else np.nan
    )
    fib_786_price = (
        fib_low_price + (fib_high_price - fib_low_price) * 0.786
        if fib_high_price and fib_low_price and fib_high_price != fib_low_price
        else np.nan
    )
    fib_1272_price = (
        fib_low_price + (fib_high_price - fib_low_price) * 1.272
        if fib_high_price and fib_low_price and fib_high_price != fib_low_price
        else np.nan
    )
    if "breakout_pullback" in signal_keys and not pd.isna(breakout_base_price):
        entry_price = float(breakout_base_price)
        stop_loss_price = float(breakout_base_price * 0.95)
        raw_target = (
            breakout_high_after_base
            if not pd.isna(breakout_high_after_base)
            else max(recent_high, breakout_base_price * 1.12)
        )
        target_price = float(max(raw_target, entry_price + (entry_price - stop_loss_price) * 2))
        trade_plan = "突破回踩：突破前波高點後，隔日以後回測突破基準附近承接；進場價用突破基準，停損放基準價下方 5%，目標看突破後高點或 2R。"
    elif "intraday_breakout_pullback" in signal_keys and not pd.isna(intraday_breakout_base_price):
        entry_price = float(intraday_breakout_base_price)
        stop_loss_price = float(intraday_breakout_base_price * 0.95)
        target_price = float(
            max(intraday_breakout_high, entry_price + (entry_price - stop_loss_price) * 1.5)
        )
        trade_plan = "日內突破回踩：今天盤中突破前波高點後當日壓回基準附近，時間尺度較短；建議隔日重新站回日內突破高點或回測基準不破才確認，停損放基準價下方 5%。"
    elif "volume_stop_reversal" in signal_keys:
        entry_price = float(prev["high"])
        stop_loss_price = float(prev["low"] * 0.98)
        target_price = float(max(fib_high_price, recent_high, entry_price + (entry_price - stop_loss_price) * 2))
        trade_plan = "量增止跌：昨天大量紅 K 先止跌，今天站上昨日高點才確認；建議進場價用昨日高點或回測不破昨日高點，停損放昨日低點下方 2%，目標看波段高點或 2R。"
    elif "volume_stop_watch" in signal_keys:
        entry_price = float(prev["high"])
        stop_loss_price = float(prev["low"] * 0.98)
        target_price = float(max(fib_high_price, recent_high, entry_price + (entry_price - stop_loss_price) * 2))
        trade_plan = "量增止跌觀察：昨天已出現大量紅 K 止跌，但尚未站上昨日高點；先列觀察，建議等突破昨日高點才視為確認，停損放昨日低點下方 2%。"
    elif "high_pullback_watch" in signal_keys:
        entry_price = float(fib_618_price if not pd.isna(fib_618_price) else last["ma20"])
        stop_loss_price = float(fib_382_price if not pd.isna(fib_382_price) and fib_382_price < entry_price else entry_price * 0.92)
        target_price = float(max(fib_high_price, recent_high, entry_price + (entry_price - stop_loss_price) * 1.8))
        trade_plan = "高檔回落：建議等待 Fib 0.618 附近或回測支撐止跌再切入，停損看 Fib 0.382 附近，目標回測波段高點。"
    elif "bottom_impulse" in signal_keys:
        if pd.isna(fib_position):
            entry_price = float(last["ma20"])
            stop_loss_price = float(recent_low * 0.97)
            target_price = float(max(recent_high, last["ma250"], entry_price + (entry_price - stop_loss_price) * 2))
            trade_plan = "底部起漲：波段 Fib 不完整，先用 MA20 回測不破作切入，停損放 30 日起漲低點下方。"
        elif fib_position < 0.382:
            entry_price = float(fib_382_price)
            stop_loss_price = float(fib_236_price if not pd.isna(fib_236_price) else recent_low * 0.97)
            target_price = float(max(fib_618_price, entry_price + (entry_price - stop_loss_price) * 2))
            trade_plan = "底部起漲：目前仍在 Fib 0.382 以下，偏早期反彈；建議等站回 0.382 才確認，停損看 0.236，目標先看 0.618。"
        elif fib_position < 0.5:
            entry_price = float(fib_382_price)
            stop_loss_price = float(fib_236_price if not pd.isna(fib_236_price) else recent_low * 0.97)
            target_price = float(max(fib_786_price, entry_price + (entry_price - stop_loss_price) * 2))
            trade_plan = "底部起漲：目前在 Fib 0.382-0.5，建議回測 0.382 不破或重新站穩後切入，停損看 0.236，目標看 0.786。"
        elif fib_position < 0.618:
            entry_price = float(fib_500_price)
            stop_loss_price = float(fib_382_price)
            target_price = float(max(fib_high_price, entry_price + (entry_price - stop_loss_price) * 2))
            trade_plan = "底部起漲：目前在 Fib 0.5-0.618，建議等回測 0.5 附近承接，停損看 0.382，目標回看波段高點。"
        elif fib_position < 0.786:
            entry_price = float(fib_618_price)
            stop_loss_price = float(fib_500_price)
            target_price = float(max(fib_high_price, entry_price + (entry_price - stop_loss_price) * 2))
            trade_plan = "底部起漲：目前在 Fib 0.618-0.786，追價風險較高；建議等回測 0.618 附近承接，停損看 0.5，目標回看波段高點。"
        else:
            entry_price = float(fib_786_price)
            stop_loss_price = float(fib_618_price)
            target_price = float(max(fib_1272_price, entry_price + (entry_price - stop_loss_price) * 2))
            trade_plan = "底部起漲：目前已接近或突破波段高位，避免追高；建議等回測 0.786 附近不破，停損看 0.618，目標看 1.272 延伸。"
    elif "volume_surge" in signal_keys:
        entry_price = float(prev["high"])
        stop_loss_price = five_day_stop
        target_price = float(max(recent_high, entry_price + (entry_price - stop_loss_price) * 1.8))
        trade_plan = "成交量暴增：先視為量能警訊，建議用隔日站上放量 K 高點作確認價；停損放近 5 日低點下方，目標看短波高點或 1.8R。"
    else:
        entry_price = float(max(data.tail(5)["high"].max(), last["ma20"]))
        stop_loss_price = five_day_stop
        target_price = float(max(recent_high, entry_price + (entry_price - stop_loss_price) * 1.5))
        trade_plan = "觀察中：尚未形成完整交易型態，僅列入追蹤；等價格站穩短均或回測不破再評估。"
    risk_reward_ratio = _risk_reward(entry_price, target_price, stop_loss_price)

    extended_conditions = {
        **conditions,
        "high_pullback_ge_10pct": bool(pullback >= thresholds.high_pullback_min),
        "high_pullback_fib_0_5_to_0_786": bool(0.5 <= fib_position <= 0.786),
        "high_pullback_watch_passed": bool(high_pullback_passed),
        "bottom_impulse_passed": bool(bottom_impulse_passed),
        "breakout_pullback_base_within_5pct": bool(
            not pd.isna(distance_to_breakout_base)
            and -thresholds.breakout_pullback_near_pct
            <= distance_to_breakout_base
            <= thresholds.breakout_pullback_near_pct
        ),
        "breakout_high_exceeded_base_by_5pct": bool(
            not pd.isna(breakout_high_after_base)
            and not pd.isna(breakout_base_price)
            and breakout_high_after_base > breakout_base_price * (1 + thresholds.breakout_pullback_near_pct)
        ),
        "breakout_pullback_passed": bool(breakout_pullback_passed),
        "intraday_breakout_pullback_base_within_5pct": bool(
            not pd.isna(intraday_distance_to_breakout_base)
            and -thresholds.breakout_pullback_near_pct
            <= intraday_distance_to_breakout_base
            <= thresholds.breakout_pullback_near_pct
        ),
        "intraday_breakout_high_exceeded_base_by_5pct": bool(
            not pd.isna(intraday_breakout_high)
            and not pd.isna(intraday_breakout_base_price)
            and intraday_breakout_high
            > intraday_breakout_base_price * (1 + thresholds.breakout_pullback_near_pct)
        ),
        "intraday_breakout_pullback_passed": bool(intraday_breakout_pullback_passed),
        "volume_surge_ge_1_6x": bool(volume_surge_multiple >= 1.6),
        "volume_surge_passed": bool(volume_surge_passed),
        "prev_volume_surge_ge_1_6x": bool(prev_volume_surge_multiple >= 1.6),
        "prev_red_close_near_high": bool(prev["close"] > prev["open"] and prev_close_position >= 0.65),
        "close_above_prev_high": bool(last["close"] > prev["high"]),
        "volume_stop_reversal_passed": bool(volume_stop_reversal_passed),
        "volume_stop_watch_passed": bool(volume_stop_watch_passed),
    }
    return {
        "as_of": last["date"],
        "symbol": last["symbol"],
        "name": last["name"],
        "market": last["market"],
        "industry": last.get("industry") or "未分類",
        "close": float(last["close"]),
        "day_change_pct": float((last["close"] / prev["close"] - 1) * 100)
        if prev["close"]
        else np.nan,
        "distance_to_ma250_pct": float((last["close"] / last["ma250"] - 1) * 100),
        "impulse_return_pct": float(impulse_return * 100),
        "pullback_pct": float(pullback * 100),
        "range_90d_pct": float(range_90d * 100),
        "ma60_slope_pct": float(ma60_slope * 100),
        "impulse_volume_multiple": float(impulse_volume_multiple),
        "pullback_volume_ratio": float(volume_ratio),
        "stop_signal_count": int(stop_signal_count),
        "avg_volume_30_lots": float(avg_volume_30_lots),
        "today_volume_lots": float(today_volume_lots),
        "volume_surge_multiple": float(volume_surge_multiple),
        "bottom_rise_pct": float(bottom_rise * 100),
        "fib_position": float(fib_position),
        "fib_zone": fib_zone,
        "fib_low_price": float(fib_low_price),
        "fib_high_price": float(fib_high_price),
        "breakout_base_price": float(active_breakout_base_price)
        if not pd.isna(active_breakout_base_price)
        else np.nan,
        "breakout_lookback_days": int(active_breakout_lookback_days)
        if not pd.isna(active_breakout_lookback_days)
        else None,
        "distance_to_breakout_base_pct": float(active_breakout_distance * 100)
        if not pd.isna(active_breakout_distance)
        else np.nan,
        "entry_price": entry_price,
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
        "risk_reward_ratio": float(risk_reward_ratio) if not pd.isna(risk_reward_ratio) else np.nan,
        "score": float(final_score),
        "strategy_tag": tag,
        "signal_type": signal_type,
        "signal_types": signal_types,
        "passed": bool(passed),
        "details": json.dumps(
            {
                "conditions": extended_conditions,
                "stop_signals": stop_signals,
                "fib_wave_low": fib_low_price,
                "fib_wave_high": fib_high_price,
                "fib_wave_low_date": wave["recent_low_date"],
                "fib_wave_high_date": wave["recent_high_date"],
                "breakout_base_price": breakout_base_price,
                "breakout_high_after_base": breakout_high_after_base,
                "breakout_lookback_days": breakout_lookback_days,
                "distance_to_breakout_base": distance_to_breakout_base,
                "intraday_breakout_base_price": intraday_breakout_base_price,
                "intraday_breakout_high": intraday_breakout_high,
                "intraday_breakout_lookback_days": intraday_breakout_lookback_days,
                "intraday_distance_to_breakout_base": intraday_distance_to_breakout_base,
                "prev_high": float(prev["high"]),
                "prev_low": float(prev["low"]),
                "prev_close_position": float(prev_close_position),
                "prev_volume_surge_multiple": float(prev_volume_surge_multiple),
                "trade_plan": trade_plan,
                **impulse,
            },
            ensure_ascii=False,
        ),
    }


def scan_market(ohlcv: pd.DataFrame) -> pd.DataFrame:
    results = []
    for (_, _), group in ohlcv.groupby(["market", "symbol"], sort=False):
        result = scan_one(group)
        if result is not None:
            results.append(result)
    if not results:
        return pd.DataFrame()
    out = pd.DataFrame(results)
    out["updated_at"] = pd.Timestamp.now()
    return out.sort_values("score", ascending=False).reset_index(drop=True)
