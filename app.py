from __future__ import annotations

from html import escape
import json
import subprocess
import sys

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scanner.config import DB_PATH, DEFAULT_HISTORY_DAYS, PROJECT_ROOT
from scanner.db import connect, latest_scan


st.set_page_config(
    page_title="Early Bottoming Pullback Scanner",
    layout="wide",
)


@st.cache_data(ttl=300)
def load_results() -> pd.DataFrame:
    con = connect()
    return latest_scan(con)


@st.cache_data(ttl=300)
def load_coverage() -> dict:
    con = connect()
    row = con.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM symbols) AS symbol_count,
            (SELECT COUNT(*) FROM ohlcv) AS ohlcv_rows,
            (
                SELECT COUNT(*)
                FROM (
                    SELECT symbol, market, COUNT(*) AS row_count
                    FROM ohlcv
                    GROUP BY symbol, market
                    HAVING COUNT(*) >= 260
                )
            ) AS enough_history_count
        """
    ).fetchone()
    return {
        "symbol_count": row[0],
        "ohlcv_rows": row[1],
        "enough_history_count": row[2],
    }


@st.cache_data(ttl=300)
def load_symbol_ohlcv(symbol: str, market: str) -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    return con.execute(
        """
        SELECT date, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = ? AND market = ?
        ORDER BY date
        """,
        [symbol, market],
    ).df()


def pct_range(label: str, series: pd.Series, default: tuple[float, float]) -> tuple[float, float]:
    if series.empty:
        return default
    min_v = float(series.min())
    max_v = float(series.max())
    if min_v == max_v:
        max_v = min_v + 1
    default_min = min(max(default[0], min_v), max_v)
    default_max = min(max(default[1], min_v), max_v)
    if default_min > default_max:
        default_min, default_max = min_v, max_v
    return st.sidebar.slider(label, min_v, max_v, (default_min, default_max), step=0.5)


def metric_card(label: str, value: str) -> None:
    st.metric(label, value)


def format_table(df: pd.DataFrame) -> pd.DataFrame:
    display = df.rename(
        columns={
            "symbol": "股票代號",
            "name": "股票名稱",
            "market": "市場",
            "industry": "產業",
            "close": "收盤價",
            "distance_to_ma250_pct": "距離年線幅度 %",
            "impulse_return_pct": "30日初動漲幅 %",
            "pullback_pct": "從30日高點回檔 %",
            "range_90d_pct": "90日盤整振幅 %",
            "ma60_slope_pct": "MA60 斜率",
            "impulse_volume_multiple": "上漲段量能倍數",
            "pullback_volume_ratio": "回檔量縮比例",
            "stop_signal_count": "止跌訊號數量",
            "avg_volume_30_lots": "30日均量(張)",
            "today_volume_lots": "今日成交量(張)",
            "volume_surge_multiple": "量能倍數",
            "bottom_rise_pct": "距60日低點漲幅 %",
            "fib_position": "Fib位置(0-1)",
            "fib_zone": "Fib區間",
            "breakout_base_price": "突破基準價",
            "distance_to_breakout_base_pct": "距突破基準 %",
            "score": "總分",
            "strategy_tag": "策略標籤",
        }
    )
    cols = [
        "股票代號",
        "股票名稱",
        "市場",
        "產業",
        "收盤價",
        "距離年線幅度 %",
        "30日初動漲幅 %",
        "從30日高點回檔 %",
        "90日盤整振幅 %",
        "MA60 斜率",
        "上漲段量能倍數",
        "回檔量縮比例",
        "止跌訊號數量",
        "30日均量(張)",
        "今日成交量(張)",
        "量能倍數",
        "距60日低點漲幅 %",
        "Fib位置(0-1)",
        "Fib區間",
        "突破基準價",
        "距突破基準 %",
        "總分",
        "策略標籤",
    ]
    return display[cols]


def ticker_suffix(market: str) -> str:
    return ".TW" if market == "上市" else ".TWO"


def fmt_optional(value: float, fmt: str, empty: str = "-") -> str:
    return empty if pd.isna(value) else format(value, fmt)


def signal_set(value: object) -> set[str]:
    return {item for item in str(value or "").split(",") if item}


def has_signal(value: object, key: str) -> bool:
    return key in signal_set(value)


def classify_tier(row: pd.Series) -> str:
    signal_types = str(row.get("signal_types") or row.get("signal_type") or "")
    signals = signal_set(signal_types)
    if "bottom_impulse" in signals:
        return "可行動"
    if "breakout_pullback" in signals:
        return "突破回踩"
    if "intraday_breakout_pullback" in signals:
        return "日內突破回踩"
    if "high_pullback_watch" in signals:
        return "高檔回落"
    if "volume_surge" in signals:
        return "成交量暴增"
    if row["score"] >= 55 and row["avg_volume_30_lots"] >= 1000:
        return "觀察中"
    return "條件不足"


def condition_reasons(row: pd.Series) -> list[str]:
    try:
        details = json.loads(row["details"])
        conditions = details.get("conditions", {})
        stop_signals = details.get("stop_signals", {})
    except Exception:
        conditions = {}
        stop_signals = {}

    reasons = []
    if conditions.get("low_stage_near_or_below_ma250"):
        reasons.append("仍屬低位階，接近或低於年線區")
    if conditions.get("range_90d_tight"):
        reasons.append(f"90 日區間振幅 {row['range_90d_pct']:.1f}%，尚未完全噴出")
    if conditions.get("impulse_ge_30pct"):
        reasons.append(f"30 日內自底部起漲 {row['impulse_return_pct']:.1f}%")
    if conditions.get("avg_volume_30d_ge_1000_lots"):
        reasons.append(f"30 日均量 {row['avg_volume_30_lots']:,.0f} 張，流動性足夠")
    if conditions.get("volume_surge_ge_1_6x"):
        reasons.append(f"今日成交量為 30 日均量 {row['volume_surge_multiple']:.2f} 倍")
    if conditions.get("high_pullback_ge_10pct"):
        reasons.append(f"收盤價距 30 日高點回落 {row['pullback_pct']:.1f}%")
    if conditions.get("high_pullback_fib_0_5_to_0_786"):
        reasons.append("高檔回落 Fib 位於 0.5-0.786")
    if conditions.get("breakout_pullback_base_within_5pct"):
        reasons.append("收盤價回踩到突破基準正負 5% 內")
    if conditions.get("breakout_high_exceeded_base_by_5pct"):
        reasons.append("突破後高點曾超過基準價 5%")
    if "fib_zone" in row and pd.notna(row["fib_zone"]):
        reasons.append(f"Fib 位置 {row['fib_position']:.3f}，{row['fib_zone']}")
    if row["stop_signal_count"] >= 2:
        reasons.append(f"止跌/轉強訊號 {int(row['stop_signal_count'])} 個")
    if stop_signals.get("close_above_ma5"):
        reasons.append("收盤站上 MA5")
    return reasons[:5]


def render_report_cards(df: pd.DataFrame, as_of) -> None:
    st.markdown(
        """
        <style>
        .report-wrap { background:#0f1115; color:#e4e6eb; padding:22px; border-radius:8px; }
        .report-h { font-size:24px; font-weight:700; margin-bottom:3px; }
        .report-date { color:#9ca3af; font-size:13px; margin-bottom:18px; }
        .overview { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:8px; margin:12px 0 22px; }
        .stat { background:#1a1d24; border:1px solid #2a2f3a; border-radius:8px; padding:11px 13px; }
        .stat .label { color:#9ca3af; font-size:12px; }
        .stat .value { color:#e4e6eb; font-size:22px; font-weight:700; }
        .stat.green .value { color:#10b981; }
        .stat.blue .value { color:#3b82f6; }
        .stat.yellow .value { color:#f59e0b; }
        .top5-card, .stock-card { background:#1a1d24; border:1px solid #2a2f3a; border-radius:8px; padding:15px 17px; margin-bottom:12px; }
        .top5-card { border:2px solid #10b981; background:linear-gradient(135deg,rgba(16,185,129,.10),#1a1d24); }
        .top5-title { color:#10b981; font-weight:700; margin-bottom:8px; }
        .top5-table { width:100%; border-collapse:collapse; font-size:13px; }
        .top5-table th { color:#9ca3af; font-size:11px; text-align:left; padding:6px 8px; border-bottom:1px solid #2a2f3a; }
        .top5-table td { color:#d1d5db; padding:7px 8px; border-bottom:1px solid #2a2f3a; }
        .top5-table .ticker-cell { color:#e4e6eb; font-weight:700; font-family:Menlo,monospace; }
        .top5-table .score-cell { color:#10b981; font-weight:800; font-size:16px; }
        .tier-header { display:flex; align-items:center; gap:8px; color:#e4e6eb; margin:20px 0 10px; padding-bottom:6px; border-bottom:1px solid #2a2f3a; font-size:17px; font-weight:700; }
        .tier-header .count { color:#6b7280; font-size:13px; font-weight:400; }
        .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
        .card-head { display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:9px; }
        .head-left, .head-right { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
        .ticker { font-size:18px; font-weight:700; font-family:Menlo,monospace; }
        .name { font-size:15px; font-weight:600; }
        .tag { padding:2px 8px; border-radius:4px; font-size:11px; font-weight:700; }
        .tag.green { background:#064e3b; color:#10b981; }
        .tag.yellow { background:#78350f; color:#f59e0b; }
        .tag.blue { background:#1e3a5f; color:#93c5fd; }
        .tag.gray { background:#374151; color:#d1d5db; }
        .score-badge { color:#10b981; border:1px solid #10b981; border-radius:4px; padding:2px 8px; font-weight:800; font-family:Menlo,monospace; }
        .data-date { background:#1f2937; color:#9ca3af; padding:2px 6px; border-radius:3px; font-size:11px; font-family:Menlo,monospace; }
        .price { color:#e4e6eb; font-weight:700; }
        .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:6px 16px; font-size:12px; color:#9ca3af; margin-top:8px; padding-top:8px; border-top:1px dashed #2a2f3a; }
        .grid .label { color:#6b7280; margin-right:6px; }
        .grid .val { color:#e4e6eb; font-family:Menlo,monospace; }
        .pos { color:#10b981 !important; }
        .neg { color:#ef4444 !important; }
        .trade-plan { margin-top:10px; padding:8px 12px; background:rgba(16,185,129,.04); border-left:3px solid #10b981; border-radius:6px; font-size:12px; color:#9ca3af; }
        .reasons { margin-top:10px; font-size:12px; color:#9ca3af; }
        .reasons ul { list-style:none; padding-left:12px; margin-top:4px; }
        .reasons li:before { content:"·"; color:#6b7280; margin-right:6px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    signals = df["signal_types"].fillna(df["signal_type"])
    action_count = int(signals.apply(lambda value: has_signal(value, "bottom_impulse")).sum())
    pullback_count = int(signals.apply(lambda value: has_signal(value, "high_pullback_watch")).sum())
    breakout_count = int(signals.apply(lambda value: has_signal(value, "breakout_pullback")).sum())
    intraday_count = int(signals.apply(lambda value: has_signal(value, "intraday_breakout_pullback")).sum())
    surge_count = int(signals.apply(lambda value: has_signal(value, "volume_surge")).sum())
    watch_count = int((signals.eq("watch") & (df["score"] >= 55) & (df["avg_volume_30_lots"] >= 1000)).sum())
    liquid_count = int((df["avg_volume_30_lots"] >= 1000).sum())
    top = df.head(5)

    html = [
        "<div class='report-wrap'>",
        "<div class='report-h'>每日線型掃描報告</div>",
        f"<div class='report-date'>{escape(str(as_of))} ・ 觀察 {len(df):,} 檔</div>",
        "<div class='overview'>",
        f"<div class='stat blue'><div class='label'>觀察股票數</div><div class='value'>{len(df):,}</div></div>",
        f"<div class='stat green'><div class='label'>底部起漲型</div><div class='value'>{action_count:,}</div></div>",
        f"<div class='stat yellow'><div class='label'>高檔回落觀察型</div><div class='value'>{pullback_count:,}</div></div>",
        f"<div class='stat blue'><div class='label'>突破回踩型</div><div class='value'>{breakout_count:,}</div></div>",
        f"<div class='stat blue'><div class='label'>日內突破回踩</div><div class='value'>{intraday_count:,}</div></div>",
        f"<div class='stat yellow'><div class='label'>成交量暴增型</div><div class='value'>{surge_count:,}</div></div>",
        f"<div class='stat blue'><div class='label'>觀察中</div><div class='value'>{watch_count:,}</div></div>",
        f"<div class='stat yellow'><div class='label'>30日均量>=1000張</div><div class='value'>{liquid_count:,}</div></div>",
        "</div>",
        "<div class='top5-card'><div class='top5-title'>Top 5 setup_score（多訊號 + 流動性）</div>",
        "<table class='top5-table'><thead><tr><th>#</th><th>標的</th><th>分類</th><th>分數</th><th>30日起漲</th><th>30日均量</th><th>距年線</th></tr></thead><tbody>",
    ]
    for rank, row in enumerate(top.itertuples(), start=1):
        html.append(
            f"<tr><td>{rank}</td><td class='ticker-cell'>{escape(row.symbol)}{ticker_suffix(row.market)} {escape(row.name)}</td>"
            f"<td>{escape(row.strategy_tag)}</td><td class='score-cell'>{row.score:.0f}</td>"
            f"<td>{row.impulse_return_pct:+.1f}%</td><td>{row.avg_volume_30_lots:,.0f} 張</td>"
            f"<td>{row.distance_to_ma250_pct:+.1f}%</td></tr>"
        )
    html.append("</tbody></table></div>")

    for tier, color in [("可行動", "#10b981"), ("突破回踩", "#3b82f6"), ("日內突破回踩", "#3b82f6"), ("高檔回落", "#f59e0b"), ("成交量暴增", "#f59e0b"), ("觀察中", "#60a5fa"), ("條件不足", "#6b7280")]:
        tier_df = df[df.apply(classify_tier, axis=1) == tier].head(30)
        if tier_df.empty:
            continue
        html.append(
            f"<div class='tier-header'><span class='dot' style='background:{color}'></span>{tier}<span class='count'>{len(tier_df):,} 檔</span></div>"
        )
        for _, row in tier_df.iterrows():
            signal_types = str(row.get("signal_types") or row.get("signal_type") or "")
            signals_in_row = signal_set(signal_types)
            tag_class = "green" if "bottom_impulse" in signals_in_row else "blue" if ("breakout_pullback" in signals_in_row or "intraday_breakout_pullback" in signals_in_row) else "yellow" if ("high_pullback_watch" in signals_in_row or "volume_surge" in signals_in_row) else "blue" if row["score"] >= 55 else "gray"
            tags = "".join(
                f"<span class='tag {cls}'>{label}</span>"
                for key, label, cls in [
                    ("bottom_impulse", "底部起漲型", "green"),
                    ("breakout_pullback", "突破回踩型", "blue"),
                    ("intraday_breakout_pullback", "日內突破回踩", "blue"),
                    ("high_pullback_watch", "高檔回落觀察型", "yellow"),
                    ("volume_surge", "成交量暴增型", "yellow"),
                ]
                if key in signals_in_row
            ) or f"<span class='tag {tag_class}'>{escape(row['strategy_tag'])}</span>"
            base_class = "pos" if row["distance_to_breakout_base_pct"] >= 0 else "neg"
            base_price_text = fmt_optional(row["breakout_base_price"], ".2f")
            base_distance_text = (
                "-"
                if pd.isna(row["distance_to_breakout_base_pct"])
                else f"{row['distance_to_breakout_base_pct']:+.1f}%"
            )
            day_change = ""
            hist = load_symbol_ohlcv(row["symbol"], row["market"]).tail(2)
            if len(hist) == 2:
                change = hist.iloc[-1]["close"] / hist.iloc[-2]["close"] - 1
                change_class = "pos" if change >= 0 else "neg"
                day_change = f"<span class='{change_class}'>{change:+.2%}</span>"
            reasons = "".join(f"<li>{escape(reason)}</li>" for reason in condition_reasons(row))
            html.append(
                "<div class='stock-card'>"
                "<div class='card-head'>"
                "<div class='head-left'>"
                f"<span class='ticker'>{escape(row['symbol'])}{ticker_suffix(row['market'])}</span>"
                f"<span class='name'>{escape(row['name'])}</span>"
                f"{tags}"
                "</div>"
                "<div class='head-right'>"
                f"<span class='score-badge'>{row['score']:.0f}</span>"
                f"<span class='data-date'>資料日 {escape(str(as_of))}</span>"
                f"<span class='price'>{row['close']:.2f}</span>{day_change}"
                "</div></div>"
                "<div class='grid'>"
                f"<div><span class='label'>距 MA250</span><span class='val {'pos' if row['distance_to_ma250_pct'] >= 0 else 'neg'}'>{row['distance_to_ma250_pct']:+.1f}%</span></div>"
                f"<div><span class='label'>30日起漲</span><span class='val pos'>{row['impulse_return_pct']:.1f}%</span></div>"
                f"<div><span class='label'>距30日高回落</span><span class='val neg'>{row['pullback_pct']:.1f}%</span></div>"
                f"<div><span class='label'>突破基準</span><span class='val'>{base_price_text}</span></div>"
                f"<div><span class='label'>距突破基準</span><span class='val {base_class}'>{base_distance_text}</span></div>"
                f"<div><span class='label'>Fib位置</span><span class='val'>{row['fib_position']:.3f}</span></div>"
                f"<div><span class='label'>Fib區間</span><span class='val'>{escape(str(row['fib_zone']))}</span></div>"
                f"<div><span class='label'>距60日低點</span><span class='val pos'>{row['bottom_rise_pct']:.1f}%</span></div>"
                f"<div><span class='label'>90日振幅</span><span class='val'>{row['range_90d_pct']:.1f}%</span></div>"
                f"<div><span class='label'>MA60斜率</span><span class='val {'pos' if row['ma60_slope_pct'] >= 0 else 'neg'}'>{row['ma60_slope_pct']:+.2f}%</span></div>"
                f"<div><span class='label'>30日均量</span><span class='val'>{row['avg_volume_30_lots']:,.0f} 張</span></div>"
                f"<div><span class='label'>今日成交量</span><span class='val'>{row['today_volume_lots']:,.0f} 張</span></div>"
                f"<div><span class='label'>量能倍數</span><span class='val'>{row['volume_surge_multiple']:.2f}x</span></div>"
                f"<div><span class='label'>上漲量能</span><span class='val'>{row['impulse_volume_multiple']:.2f}x</span></div>"
                f"<div><span class='label'>止跌訊號</span><span class='val'>{int(row['stop_signal_count'])} 個</span></div>"
                "</div>"
                "<div class='trade-plan'>"
                f"<div><span class='pos'>觸發</span> 站穩底部起漲結構；觀察 30 日高與 MA20 支撐。</div>"
                f"<div><span class='neg'>失效</span> 跌破近 30 日起漲低點或放量長黑，訊號降級。</div>"
                "</div>"
                f"<div class='reasons'><span>觀察條件：</span><ul>{reasons}</ul></div>"
                "</div>"
            )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_chart(row: pd.Series) -> None:
    hist = load_symbol_ohlcv(row["symbol"], row["market"])
    if hist.empty:
        return
    hist["ma60"] = hist["close"].rolling(60).mean()
    hist["ma250"] = hist["close"].rolling(250).mean()
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=hist["date"],
            open=hist["open"],
            high=hist["high"],
            low=hist["low"],
            close=hist["close"],
            name="OHLC",
        )
    )
    fig.add_trace(go.Scatter(x=hist["date"], y=hist["ma60"], name="MA60", line=dict(width=1.5)))
    fig.add_trace(go.Scatter(x=hist["date"], y=hist["ma250"], name="MA250", line=dict(width=1.5)))
    fig.update_layout(
        height=520,
        margin=dict(l=10, r=10, t=25, b=10),
        xaxis_rangeslider_visible=False,
        legend_orientation="h",
    )
    st.plotly_chart(fig, use_container_width=True)

    vol = go.Figure()
    vol.add_trace(go.Bar(x=hist["date"], y=hist["volume"], name="Volume"))
    vol.update_layout(height=180, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(vol, use_container_width=True)


def run_online_update(backfill: bool) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "scanner.cli",
        "update",
        "--days",
        str(DEFAULT_HISTORY_DAYS),
    ]
    if backfill:
        cmd.append("--backfill")
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=7200,
        check=False,
    )


st.title("Bottom Impulse Scanner / 底部起漲策略")
st.caption("全市場 OHLCV 掃描：尋找低位階整理後出現初動起漲，且 30 日均量達 1000 張以上的股票。")

st.sidebar.header("資料更新")
refresh_minutes = st.sidebar.selectbox(
    "頁面自動刷新",
    [0, 5, 15, 30, 60],
    format_func=lambda m: "關閉" if m == 0 else f"每 {m} 分鐘",
)
if refresh_minutes:
    st.markdown(f"<meta http-equiv='refresh' content='{refresh_minutes * 60}'>", unsafe_allow_html=True)

with st.sidebar.expander("線上更新", expanded=False):
    st.caption("最新日更新通常較快；全市場 300 日回補會較久，但可補足歷史資料後重新掃描。")
    backfill_online = st.checkbox("包含 300 日歷史回補", value=False)
    if st.button("立即線上更新", type="primary"):
        with st.spinner("正在抓取資料並重新掃描..."):
            proc = run_online_update(backfill_online)
        st.cache_data.clear()
        output = "\n".join(part for part in [proc.stdout, proc.stderr] if part)
        if proc.returncode == 0:
            st.success("更新完成，正在重新整理資料。")
            if output:
                st.code(output[-4000:])
            st.rerun()
        else:
            st.error("更新失敗，請查看輸出。")
            st.code(output[-4000:] if output else "No output")

results = load_results()
coverage = load_coverage()
if results.empty:
    st.info(
        "尚無掃描結果。請先在終端機執行：`python3 -m scanner.cli update --backfill --days 300`，"
        "或用 `python3 -m scanner.cli import-csv path/to/ohlcv.csv` 匯入資料。"
    )
    st.stop()

if coverage["symbol_count"] and coverage["enough_history_count"] < coverage["symbol_count"] * 0.8:
    st.warning(
        f"目前只有 {coverage['enough_history_count']:,} / {coverage['symbol_count']:,} 檔股票有 260 日以上歷史資料。"
        "若符合條件清單為空，通常代表 300 日歷史回補尚未完成；請執行 "
        "`python3 -m scanner.cli update --backfill --days 300`。"
    )

for col, default in {"avg_volume_30_lots": 0.0, "today_volume_lots": 0.0, "volume_surge_multiple": 0.0, "bottom_rise_pct": 0.0, "fib_position": 0.0, "breakout_base_price": 0.0, "distance_to_breakout_base_pct": 0.0}.items():
    if col not in results.columns:
        results[col] = default
    results[col] = results[col].fillna(default)
if "fib_zone" not in results.columns:
    results["fib_zone"] = "N/A"
results["fib_zone"] = results["fib_zone"].fillna("N/A")
if "signal_type" not in results.columns:
    results["signal_type"] = results["strategy_tag"].map({"底部起漲型": "bottom_impulse", "高檔回落觀察型": "high_pullback_watch"}).fillna("watch")
results["signal_type"] = results["signal_type"].fillna("watch")
if "signal_types" not in results.columns:
    results["signal_types"] = results["signal_type"]
results["signal_types"] = results["signal_types"].fillna(results["signal_type"])

st.sidebar.header("篩選")
view_mode = st.sidebar.radio("呈現方式", ["報告卡片", "資料表格"], horizontal=True)
signal_options = ["底部起漲型", "突破回踩型", "日內突破回踩", "高檔回落觀察型", "成交量暴增型", "觀察中"]
selected_signals = st.sidebar.multiselect("訊號類型", signal_options, default=["底部起漲型", "突破回踩型", "日內突破回踩", "高檔回落觀察型", "成交量暴增型"])
signal_map = {"底部起漲型": "bottom_impulse", "突破回踩型": "breakout_pullback", "日內突破回踩": "intraday_breakout_pullback", "高檔回落觀察型": "high_pullback_watch", "成交量暴增型": "volume_surge", "觀察中": "watch"}
markets = st.sidebar.multiselect("上市 / 上櫃", sorted(results["market"].dropna().unique()), default=sorted(results["market"].dropna().unique()))
industries = st.sidebar.multiselect("產業", sorted(results["industry"].dropna().unique()))
distance_range = pct_range(
    "距離年線 %",
    results["distance_to_ma250_pct"],
    (float(results["distance_to_ma250_pct"].min()), float(results["distance_to_ma250_pct"].max())),
)
impulse_range = pct_range("初動漲幅 %", results["impulse_return_pct"], (0.0, float(results["impulse_return_pct"].max())))
limit_pullback = st.sidebar.checkbox("限制回檔幅度", value=False)
pullback_range = pct_range("回檔幅度 %", results["pullback_pct"], (0.0, max(0.0, float(results["pullback_pct"].max()))))
min_avg_volume = st.sidebar.number_input("30日均量至少(張)", min_value=0, value=1000, step=100)

filtered = results.copy()
if selected_signals:
    selected_keys = [signal_map[s] for s in selected_signals]
    filtered = filtered[
        filtered["signal_types"].apply(lambda value: any(key in str(value).split(",") for key in selected_keys))
    ]
if markets:
    filtered = filtered[filtered["market"].isin(markets)]
if industries:
    filtered = filtered[filtered["industry"].isin(industries)]
filtered = filtered[
    filtered["distance_to_ma250_pct"].between(*distance_range)
    & filtered["impulse_return_pct"].between(*impulse_range)
    & (filtered["avg_volume_30_lots"] >= min_avg_volume)
].sort_values("score", ascending=False)
if limit_pullback:
    filtered = filtered[filtered["pullback_pct"].between(*pullback_range)]

as_of = pd.to_datetime(results["as_of"].max()).date()
col1, col2, col3, col4 = st.columns(4)
with col1:
    metric_card("資料日期", str(as_of))
with col2:
    metric_card("符合條件", f"{int(results['passed'].sum()):,}")
with col3:
    metric_card("目前顯示", f"{len(filtered):,}")
with col4:
    metric_card("最高分", f"{filtered['score'].max():.1f}" if not filtered.empty else "-")

if view_mode == "報告卡片":
    render_report_cards(filtered, as_of)
else:
    st.subheader("排名清單")
    st.dataframe(
        format_table(filtered).style.format(
            {
                "收盤價": "{:.2f}",
                "距離年線幅度 %": "{:.1f}",
                "30日初動漲幅 %": "{:.1f}",
                "從30日高點回檔 %": "{:.1f}",
                "90日盤整振幅 %": "{:.1f}",
                "MA60 斜率": "{:.2f}",
                "上漲段量能倍數": "{:.2f}",
                "回檔量縮比例": "{:.2f}",
                "30日均量(張)": "{:,.0f}",
                "今日成交量(張)": "{:,.0f}",
                "量能倍數": "{:.2f}",
                "距60日低點漲幅 %": "{:.1f}",
                "Fib位置(0-1)": "{:.3f}",
                "突破基準價": "{:.2f}",
                "距突破基準 %": "{:.1f}",
                "總分": "{:.1f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

if not filtered.empty:
    labels = [f"{r.symbol} {r.name}" for r in filtered.itertuples()]
    selected_label = st.selectbox("查看個股走勢", labels)
    selected_idx = labels.index(selected_label)
    selected = filtered.iloc[selected_idx]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("收盤價", f"{selected['close']:.2f}")
    c2.metric("距離年線", f"{selected['distance_to_ma250_pct']:.1f}%")
    c3.metric("初動漲幅", f"{selected['impulse_return_pct']:.1f}%")
    c4.metric("回檔", f"{selected['pullback_pct']:.1f}%")
    c5.metric("總分", f"{selected['score']:.1f}")
    render_chart(selected)

    with st.expander("條件明細"):
        details = json.loads(selected["details"])
        st.json(details)
