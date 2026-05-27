from __future__ import annotations

from html import escape
import json
from pathlib import Path

import pandas as pd

from .config import PROJECT_ROOT
from .db import connect, latest_scan


REPORT_FILENAME = "getmoneytommrrow.html"


def _suffix(market: str) -> str:
    return ".TW" if market == "上市" else ".TWO"


def _tv_exchange(market: str) -> str:
    return "TWSE" if market == "上市" else "TPEX"


def _tradingview_url(symbol: str, market: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol={_tv_exchange(market)}%3A{symbol}"


def _card_id(symbol: str, market: str) -> str:
    return f"stock-{market}-{symbol}"


def _fmt_pct(value: float) -> str:
    return f"{value:+.1f}%"


def _fmt_optional(value: float, fmt: str, empty: str = "-") -> str:
    return empty if pd.isna(value) else format(value, fmt)


def _trade_plan(row: pd.Series) -> str:
    try:
        details = json.loads(row.get("details") or "{}")
        return details.get("trade_plan") or "依策略價位控管風險，未觸發續強前以觀察為主。"
    except Exception:
        return "依策略價位控管風險，未觸發續強前以觀察為主。"


def _change_class(value: float) -> str:
    if pd.isna(value) or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def _readiness_label(score: float) -> str:
    if pd.isna(score):
        return "型態契合度 -"
    if score >= 85:
        tier = "高"
    elif score >= 70:
        tier = "中高"
    elif score >= 55:
        tier = "觀察"
    else:
        tier = "初步"
    return f"型態契合度 {tier} {score:.0f}%"


def _is_actionable_setup(row: pd.Series) -> bool:
    signals = _signal_set(row.get("signal_types") or row.get("signal_type") or "")
    actionable = {
        "bottom_impulse",
        "breakout_pullback",
        "high_pullback_watch",
        "volume_stop_reversal",
    }
    if not signals.intersection(actionable):
        return False
    if signals == {"volume_surge"} or signals == {"intraday_breakout_pullback"}:
        return False
    rr = row.get("risk_reward_ratio")
    return bool(pd.isna(rr) or rr >= 1.5)


def _signal_set(value: object) -> set[str]:
    return {item for item in str(value or "").split(",") if item}


def _signal_mask(results: pd.DataFrame, key: str) -> pd.Series:
    signal_series = results["signal_types"].fillna(results["signal_type"])
    return signal_series.apply(lambda value: key in _signal_set(value))


SIGNAL_DEFINITIONS = {
    "bottom_impulse": "低位階整理後，30 日內自波段低點起漲達門檻，且短線止跌訊號足夠。",
    "breakout_pullback": "原本盤整或下跌後，突破前一個波段高點，隔日以後回踩到突破基準正負 5% 內；突破尺度越長分數越高。",
    "intraday_breakout_pullback": "今天盤中突破前波高點，但同日收盤壓回突破基準附近，屬較短時間尺度的突破回踩。",
    "high_pullback_watch": "股價自 30 日高點回落 10% 以上，且 Fib 位於 0.5-0.786 的健康回測區。",
    "volume_stop_reversal": "昨日大量紅 K 先止跌，今日收盤站上昨日高點，屬確認型。",
    "volume_stop_watch": "昨日已出現大量紅 K 止跌，但尚未站上昨日高點，先列觀察。",
    "volume_surge": "今日成交量大於 30 日均量 1.6 倍，代表資金突然放大。",
}


STOP_SIGNAL_LABELS = {
    "5d_low_stopped_falling": "最近 5 日低點不再創低",
    "close_above_ma5": "收盤站上 MA5",
    "rsi14_rebounding": "RSI14 從低檔回升",
    "volume_above_5d_avg": "今日量大於 5 日均量",
    "red_candle": "今日收紅 K",
}


def _details(row: pd.Series) -> dict:
    try:
        return json.loads(row.get("details") or "{}")
    except Exception:
        return {}


def _stop_signal_text(row: pd.Series) -> str:
    stop_signals = _details(row).get("stop_signals", {})
    active = [label for key, label in STOP_SIGNAL_LABELS.items() if stop_signals.get(key)]
    return "、".join(active) if active else "無明確止跌訊號"


def _signal_tag(key: str, label: str, cls: str) -> str:
    definition = escape(SIGNAL_DEFINITIONS.get(key, "策略條件說明"))
    return (
        f"<details class='tag-info'><summary class='tag {cls}'>{label}</summary>"
        f"<div class='tip'>{definition}</div></details>"
    )


def _reasons(row: pd.Series) -> list[str]:
    reasons = []
    signal_types = _signal_set(row.get("signal_types") or row.get("signal_type") or "")
    if "volume_stop_reversal" in signal_types or "volume_stop_watch" in signal_types:
        try:
            details = _details(row)
            prev_high = details.get("prev_high")
            prev_low = details.get("prev_low")
            prev_vol = details.get("prev_volume_surge_multiple")
            reasons.append(f"昨日大量紅 K，量能為 30 日均量 {prev_vol:.2f} 倍")
            if "volume_stop_reversal" in signal_types:
                reasons.append(f"今日收盤站上昨日高點 {prev_high:.2f}")
            else:
                reasons.append(f"尚未站上昨日高點 {prev_high:.2f}，先列觀察")
            reasons.append(f"停損參考昨日低點 {prev_low:.2f} 下方")
        except Exception:
            reasons.append("昨日大量紅 K 後，今日站上昨日高點確認")
    if "volume_surge" in signal_types:
        reasons.append(
            f"今日成交量 {row['today_volume_lots']:,.0f} 張，為 30 日均量 {row['volume_surge_multiple']:.2f} 倍"
        )
        reasons.append(f"30 日均量 {row['avg_volume_30_lots']:,.0f} 張，符合流動性門檻")
    if "breakout_pullback" in signal_types:
        lookback = row.get("breakout_lookback_days")
        lookback_text = f"{int(lookback)} 日" if pd.notna(lookback) else "前波"
        reasons.append(
            f"突破 {lookback_text}高點後，隔日以後回踩到基準價 {row['breakout_base_price']:.2f} 正負 5% 內"
        )
        reasons.append(f"目前距突破基準 {row['distance_to_breakout_base_pct']:+.1f}%")
    if "intraday_breakout_pullback" in signal_types:
        lookback = row.get("breakout_lookback_days")
        lookback_text = f"{int(lookback)} 日" if pd.notna(lookback) else "前波"
        reasons.append(
            f"今日盤中突破 {lookback_text}高點後，壓回基準價 {row['breakout_base_price']:.2f} 正負 5% 內"
        )
        reasons.append("時間尺度偏短，隔日站回日內高點會更有確認性")
    if "high_pullback_watch" in signal_types:
        reasons.append(f"30 日均量 {row['avg_volume_30_lots']:,.0f} 張，符合流動性門檻")
        reasons.append(f"Fib 位置 {row['fib_position']:.3f}，{row['fib_zone']}")
        reasons.append("高檔回落 Fib 位於 0.5-0.786")
        if "bottom_impulse" not in signal_types:
            reasons.append("可觀察是否在 MA20 / 前波整理區止跌")
            return reasons

    if not reasons or "bottom_impulse" in signal_types:
        reasons.append(f"止跌/轉強訊號 {int(row['stop_signal_count'])} 個")
        if row["avg_volume_30_lots"] >= 1000:
            reasons.append(f"30 日均量 {row['avg_volume_30_lots']:,.0f} 張，符合流動性門檻")
        else:
            reasons.append(f"30 日均量 {row['avg_volume_30_lots']:,.0f} 張，未達 1000 張門檻")
        if pd.notna(row.get("fib_position")):
            reasons.append(f"Fib 位置 {row['fib_position']:.3f}，{row['fib_zone']}")
    return list(dict.fromkeys(reasons))


def build_report_html(results: pd.DataFrame) -> str:
    if results.empty:
        return "<!doctype html><meta charset='utf-8'><body>尚無掃描結果</body>"

    as_of = pd.to_datetime(results["as_of"].max()).date()
    if "signal_type" not in results.columns:
        results["signal_type"] = "watch"
    if "signal_types" not in results.columns:
        results["signal_types"] = results["signal_type"]
    for col in ["entry_price", "target_price", "stop_loss_price", "risk_reward_ratio", "fib_low_price", "fib_high_price", "day_change_pct", "breakout_lookback_days"]:
        if col not in results.columns:
            results[col] = pd.NA
    signal_series = results["signal_types"].fillna(results["signal_type"])
    passed = results[_signal_mask(results, "bottom_impulse")].sort_values("score", ascending=False)
    pullbacks = results[_signal_mask(results, "high_pullback_watch")].sort_values("score", ascending=False)
    breakouts = results[_signal_mask(results, "breakout_pullback")].sort_values("score", ascending=False)
    intraday_breakouts = results[_signal_mask(results, "intraday_breakout_pullback")].sort_values("score", ascending=False)
    reversals = results[_signal_mask(results, "volume_stop_reversal")].sort_values("score", ascending=False)
    reversal_watch = results[_signal_mask(results, "volume_stop_watch")].sort_values("score", ascending=False)
    surges = results[_signal_mask(results, "volume_surge")].sort_values("score", ascending=False)
    watch = results[
        (signal_series == "watch") & (results["score"] >= 55) & (results["avg_volume_30_lots"] >= 1000)
    ].sort_values("score", ascending=False)
    top = (
        results[results.apply(_is_actionable_setup, axis=1)]
        .sort_values("score", ascending=False)
        .head(5)
    )

    css = """
    :root{--bg:#f5f5f7;--card:rgba(255,255,255,.86);--line:rgba(0,0,0,.08);--text:#1d1d1f;--muted:#6e6e73;--soft:#86868b;--green:#0a7f52;--blue:#0066cc;--yellow:#b36b00;--red:#c7362f}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{background:radial-gradient(circle at top left,#fff 0,#f5f5f7 38%,#ececf0 100%);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang TC","Helvetica Neue",sans-serif;line-height:1.55;padding:34px 22px;max-width:1180px;margin:0 auto;letter-spacing:0}
    .hero{display:flex;justify-content:space-between;gap:18px;align-items:flex-end;margin-bottom:18px}.hero-title{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.hero h1{font-size:34px;line-height:1.1;margin:0;font-weight:760}.open-external{border:1px solid var(--line);background:rgba(255,255,255,.88);color:var(--blue);border-radius:999px;padding:8px 12px;font-size:13px;font-weight:700;text-decoration:none;box-shadow:0 10px 28px rgba(0,0,0,.06)}.date{color:var(--muted);font-size:14px;margin-top:8px}.searchbar{position:sticky;top:10px;z-index:10;display:flex;gap:10px;align-items:center;background:rgba(255,255,255,.72);border:1px solid rgba(0,0,0,.08);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-radius:18px;padding:10px 12px;margin:18px 0 18px;box-shadow:0 12px 36px rgba(0,0,0,.08)}
    .searchbar input{width:100%;border:0;outline:0;background:transparent;font-size:16px;color:var(--text);padding:8px}.searchbar button{border:0;border-radius:999px;background:#1d1d1f;color:white;padding:9px 15px;font-weight:650;cursor:pointer}.searchbar .hint{font-size:12px;color:var(--muted);white-space:nowrap}.suggestions{display:none;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:-8px 0 18px}.suggestions a{text-decoration:none;color:var(--text);background:rgba(255,255,255,.82);border:1px solid var(--line);border-radius:14px;padding:8px 10px;font-size:13px;box-shadow:0 10px 28px rgba(0,0,0,.05)}
    .overview{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin-bottom:24px}.stat,.card,.top5{background:var(--card);border:1px solid var(--line);box-shadow:0 18px 45px rgba(0,0,0,.06);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}.stat{border-radius:18px;padding:14px 15px}.stat .label{font-size:12px;color:var(--muted)}.stat .value{font-size:25px;font-weight:760}.green .value{color:var(--green)}.blue .value{color:var(--blue)}.yellow .value{color:var(--yellow)}
    a.stat{display:block;text-decoration:none;color:inherit;transition:transform .16s,box-shadow .16s}a.stat:hover{transform:translateY(-2px);box-shadow:0 22px 55px rgba(0,0,0,.10)}
    .top5{border-radius:22px;padding:18px 20px;margin-bottom:26px}.top5-title{font-weight:760;margin-bottom:8px}.top5-note{font-size:12px;color:var(--muted);margin-bottom:12px}table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:var(--muted);font-size:11px;font-weight:650}th,td{padding:8px 10px;border-bottom:1px solid var(--line)}.ticker-cell{font-weight:760;font-family:Menlo,monospace}.fit-cell{color:var(--green);font-weight:760}
    h2{font-size:20px;margin:30px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center}.dot{width:10px;height:10px;border-radius:50%;display:inline-block}.count{color:var(--soft);font-size:13px;font-weight:400}
    .card{border-radius:22px;padding:18px 20px;margin-bottom:13px;scroll-margin-top:92px;transition:border-color .16s,box-shadow .16s,transform .16s}.card.match{border-color:#0071e3;box-shadow:0 0 0 4px rgba(0,113,227,.15),0 24px 60px rgba(0,0,0,.13);transform:translateY(-1px)}
    .card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:10px}.head-left,.head-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.head-right{justify-content:flex-end}.ticker{font-size:19px;font-weight:760;font-family:Menlo,monospace;color:var(--text);text-decoration:none}.name{font-size:16px;font-weight:650;color:var(--text);text-decoration:none}.ticker:hover,.name:hover{text-decoration:underline}.tag{padding:3px 8px;border-radius:999px;font-size:11px;font-weight:720;list-style:none;cursor:pointer}.tag.green{background:#e7f6ef;color:var(--green)}.tag.yellow{background:#fff4df;color:var(--yellow)}.tag.blue{background:#e8f2ff;color:var(--blue)}.tag.red{background:#fde8e6;color:var(--red)}.tag-info{position:relative}.tag-info summary{display:inline-block}.tag-info .tip{position:absolute;z-index:20;top:26px;left:auto;right:0;width:min(280px,72vw);background:#fff;border:1px solid var(--line);box-shadow:0 18px 42px rgba(0,0,0,.14);border-radius:14px;padding:10px;color:var(--muted);font-size:12px}.fit{background:#eef8f3;color:var(--green);padding:5px 10px;border-radius:12px;font-size:12px;font-weight:760}.quote{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);background:#f7f7fa;border-radius:14px;padding:5px 9px;box-shadow:inset 0 1px 0 rgba(255,255,255,.8)}.price{font-weight:800;font-family:Menlo,monospace}.chg{padding:2px 7px;border-radius:999px;font-size:12px;font-family:Menlo,monospace;font-weight:800}.chg.up{background:#fde8e6;color:var(--red)}.chg.down{background:#e7f6ef;color:var(--green)}.chg.flat{background:#f1f1f3;color:var(--muted)}
    .grid,.trade-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:7px 16px;font-size:12px;color:var(--muted);margin-top:9px;padding-top:10px;border-top:1px solid var(--line)}.trade-grid{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));background:#f7f7fa;border:1px solid var(--line);border-radius:16px;padding:12px;margin-top:12px}.label{color:var(--soft);margin-right:6px}.val{color:var(--text);font-family:Menlo,monospace}.pos{color:var(--green)}.neg{color:var(--red)}.up{color:var(--red)}.down{color:var(--green)}
    .metric-info summary{cursor:pointer;list-style:none}.metric-info summary::-webkit-details-marker{display:none}.metric-info .tip{margin-top:6px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:8px;color:var(--muted);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang TC",sans-serif;line-height:1.45}.plan{margin-top:10px;padding:10px 12px;background:#fbfbfd;border:1px solid var(--line);border-radius:16px;font-size:12px;color:var(--muted)}.reasons{margin-top:10px;font-size:12px;color:var(--muted)}.reasons ul{list-style:none;padding-left:0;margin:6px 0 0}.reasons li{display:inline-block;margin:0 6px 6px 0;padding:4px 8px;background:#f6f6f8;border-radius:999px}.empty{display:none;color:var(--red);font-size:13px;padding:0 4px 12px}
    """
    js = """
    <script>
    function normalizeText(value){return (value||'').toString().trim().toLowerCase();}
    function searchStock(){
      const input=document.getElementById('stockSearch');
      const q=normalizeText(input.value);
      const empty=document.getElementById('searchEmpty');
      const suggestions=document.getElementById('suggestions');
      document.querySelectorAll('.card.match').forEach(el=>el.classList.remove('match'));
      if(!q){empty.style.display='none';return;}
      const cards=Array.from(document.querySelectorAll('.card[data-search]'));
      const hit=cards.find(card=>normalizeText(card.dataset.search).includes(q));
      if(hit){
        empty.style.display='none';
        hit.classList.add('match');
        hit.scrollIntoView({behavior:'smooth',block:'center'});
        if(suggestions) suggestions.style.display='none';
      }else{
        empty.textContent='找不到符合「'+input.value+'」的個股卡片';
        empty.style.display='block';
      }
    }
    function updateSuggestions(){
      const input=document.getElementById('stockSearch');
      const q=normalizeText(input.value);
      const box=document.getElementById('suggestions');
      if(!box) return;
      box.innerHTML='';
      if(!q){box.style.display='none';return;}
      const hits=Array.from(document.querySelectorAll('.card[data-search]')).filter(card=>normalizeText(card.dataset.search).includes(q)).slice(0,8);
      hits.forEach(card=>{
        const a=document.createElement('a');
        a.href='#'+card.id;
        a.textContent=card.dataset.label || card.dataset.search;
        a.addEventListener('click',()=>{document.querySelectorAll('.card.match').forEach(el=>el.classList.remove('match'));card.classList.add('match');box.style.display='none';});
        box.appendChild(a);
      });
      box.style.display=hits.length?'grid':'none';
    }
    function openExternalBrowser(event){
      event.preventDefault();
      const url=window.location.href;
      const opened=window.open(url,'_blank','noopener,noreferrer');
      if(!opened){
        window.location.href=url;
      }
    }
    window.addEventListener('DOMContentLoaded',()=>{
      const input=document.getElementById('stockSearch');
      const form=document.getElementById('searchForm');
      if(form){form.addEventListener('submit',event=>{event.preventDefault();searchStock();});}
      input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();searchStock();}});
      input.addEventListener('input',()=>{document.getElementById('searchEmpty').style.display='none';updateSuggestions();});
    });
    </script>
    """
    html = [
        "<!DOCTYPE html><html lang='zh-TW'><head><meta charset='UTF-8'>",
        f"<title>每日線型掃描報告 — {as_of}</title><style>{css}</style></head><body>",
        "<div class='hero'><div><div class='hero-title'><h1>每日線型掃描報告</h1><a class='open-external' href='#' onclick='openExternalBrowser(event)'>用外部瀏覽器開啟</a></div>",
        f"<div class='date'>{as_of} ・ 觀察 {len(results):,} 檔 ・ 20:30 自動發送</div></div></div>",
        "<form id='searchForm' class='searchbar'><input id='stockSearch' type='search' inputmode='search' autocomplete='off' placeholder='搜尋股票代號或名稱，例如 2330、台積電'><button type='submit'>搜尋</button><span class='hint'>Enter 跳轉</span></form><div id='suggestions' class='suggestions'></div><div id='searchEmpty' class='empty'></div>",
        "<div class='overview'>",
        f"<a class='stat blue' href='#top5'><div class='label'>觀察股票數</div><div class='value'>{len(results):,}</div></a>",
        f"<a class='stat green' href='#bottom-impulse'><div class='label'>底部起漲型</div><div class='value'>{len(passed):,}</div></a>",
        f"<a class='stat blue' href='#breakout-pullback'><div class='label'>突破回踩型</div><div class='value'>{len(breakouts):,}</div></a>",
        f"<a class='stat blue' href='#intraday-breakout-pullback'><div class='label'>日內突破回踩</div><div class='value'>{len(intraday_breakouts):,}</div></a>",
        f"<a class='stat yellow' href='#high-pullback'><div class='label'>高檔回落觀察型</div><div class='value'>{len(pullbacks):,}</div></a>",
        f"<a class='stat green' href='#volume-stop-reversal'><div class='label'>量增止跌型</div><div class='value'>{len(reversals):,}</div></a>",
        f"<a class='stat green' href='#volume-stop-watch'><div class='label'>量增止跌觀察</div><div class='value'>{len(reversal_watch):,}</div></a>",
        f"<a class='stat yellow' href='#volume-surge'><div class='label'>成交量暴增型</div><div class='value'>{len(surges):,}</div></a>",
        f"<a class='stat blue' href='#watch'><div class='label'>觀察中</div><div class='value'>{len(watch):,}</div></a>",
        f"<a class='stat yellow' href='#top5'><div class='label'>30日均量>=1000張</div><div class='value'>{int((results['avg_volume_30_lots'] >= 1000).sum()):,}</div></a>",
        "</div><div class='top5' id='top5'><div class='top5-title'>Top 5 進場觀察</div>",
        "<div class='top5-note'>只列入具有明確進場/回測邏輯的型態；純成交量暴增或純日內突破回踩不放入這裡。</div>",
        "<table><thead><tr><th>#</th><th>標的</th><th>分類</th><th>型態解讀</th><th>Fib</th><th>距突破基準</th><th>30日均量</th></tr></thead><tbody>",
    ]
    for i, row in enumerate(top.itertuples(), 1):
        breakout_distance = (
            "-"
            if pd.isna(row.distance_to_breakout_base_pct)
            else f"{row.distance_to_breakout_base_pct:+.1f}%"
        )
        card_id = escape(_card_id(str(row.symbol), str(row.market)))
        html.append(
            f"<tr><td>{i}</td><td class='ticker-cell'><a href='#{card_id}'>{escape(row.symbol)}{_suffix(row.market)} {escape(row.name)}</a></td>"
            f"<td>{escape(row.strategy_tag)}</td><td class='fit-cell'>{escape(_readiness_label(row.score))}</td>"
            f"<td>{row.fib_position:.3f}</td><td>{breakout_distance}</td><td>{row.avg_volume_30_lots:,.0f} 張</td></tr>"
        )
    html.append("</tbody></table></div>")

    emitted: set[tuple[str, str]] = set()
    for section_id, title, frame, color in [
        ("bottom-impulse", "底部起漲", passed, "var(--green)"),
        ("breakout-pullback", "突破回踩", breakouts, "var(--blue)"),
        ("intraday-breakout-pullback", "日內突破回踩", intraday_breakouts, "var(--blue)"),
        ("high-pullback", "高檔回落", pullbacks, "var(--yellow)"),
        ("volume-stop-reversal", "量增止跌", reversals, "var(--green)"),
        ("volume-stop-watch", "量增止跌觀察", reversal_watch, "var(--green)"),
        ("volume-surge", "成交量暴增", surges, "var(--yellow)"),
        ("watch", "觀察中", watch, "var(--blue)"),
    ]:
        frame = frame[
            ~frame.apply(lambda item: (item["symbol"], item["market"]) in emitted, axis=1)
        ]
        html.append(f"<h2 id='{section_id}'><span class='dot' style='background:{color}'></span>{title}<span class='count'>{len(frame):,} 檔</span></h2>")
        for _, row in frame.iterrows():
            emitted.add((row["symbol"], row["market"]))
            reasons = "".join(f"<li>{escape(reason)}</li>" for reason in _reasons(row))
            plan = escape(_trade_plan(row))
            signal_types = str(row.get("signal_types") or row.get("signal_type") or "")
            tags = "".join(
                _signal_tag(key, label, cls)
                for key, label, cls in [
                    ("bottom_impulse", "底部起漲型", "green"),
                    ("breakout_pullback", "突破回踩型", "blue"),
                    ("intraday_breakout_pullback", "日內突破回踩", "blue"),
                    ("high_pullback_watch", "高檔回落觀察型", "yellow"),
                    ("volume_stop_reversal", "量增止跌型", "green"),
                    ("volume_stop_watch", "量增止跌觀察", "green"),
                    ("volume_surge", "成交量暴增型", "yellow"),
                ]
                if key in _signal_set(signal_types)
            ) or f"<span class='tag blue'>{escape(row['strategy_tag'])}</span>"
            change_class = _change_class(row["day_change_pct"])
            change_text = _fmt_pct(row["day_change_pct"]) if not pd.isna(row["day_change_pct"]) else "-"
            base_class = "pos" if row["distance_to_breakout_base_pct"] >= 0 else "neg"
            base_price_text = _fmt_optional(row["breakout_base_price"], ".2f")
            base_distance_text = (
                "-"
                if pd.isna(row["distance_to_breakout_base_pct"])
                else f"{row['distance_to_breakout_base_pct']:+.1f}%"
            )
            rr_text = _fmt_optional(row["risk_reward_ratio"], ".2f")
            card_id = escape(_card_id(str(row["symbol"]), str(row["market"])))
            search_text = escape(f"{row['symbol']} {_suffix(row['market'])} {row['name']} {row['market']} {row['industry']}")
            display_label = escape(f"{row['symbol']}{_suffix(row['market'])} {row['name']}")
            tv_url = _tradingview_url(str(row["symbol"]), str(row["market"]))
            impulse_tip = escape(
                "上漲量能 = 30 日內波段低點到波段高點期間的平均成交量 / 最近 90 日平均成交量。"
            )
            stop_tip = escape(_stop_signal_text(row))
            html.append(
                f"<div class='card' id='{card_id}' data-search='{search_text}' data-label='{display_label}'><div class='card-head'><div class='head-left'>"
                f"<a class='ticker' href='{tv_url}' target='_blank' rel='noopener'>{escape(row['symbol'])}{_suffix(row['market'])}</a><a class='name' href='{tv_url}' target='_blank' rel='noopener'>{escape(row['name'])}</a>"
                f"<span class='quote'><span class='price'>{row['close']:.2f}</span><span class='chg {change_class}'>{change_text}</span></span>"
                f"</div><div class='head-right'><span class='fit'>{escape(_readiness_label(row['score']))}</span>"
                f"{tags}"
                "</div></div><div class='grid'>"
                f"<div><span class='label'>突破基準</span><span class='val'>{base_price_text}</span></div>"
                f"<div><span class='label'>距突破基準</span><span class='val {base_class}'>{base_distance_text}</span></div>"
                f"<div><span class='label'>Fib位置</span><span class='val'>{row['fib_position']:.3f}</span></div>"
                f"<div><span class='label'>Fib區間</span><span class='val'>{escape(str(row['fib_zone']))}</span></div>"
                f"<div><span class='label'>30日均量</span><span class='val'>{row['avg_volume_30_lots']:,.0f} 張</span></div>"
                f"<div><span class='label'>今日成交量</span><span class='val'>{row['today_volume_lots']:,.0f} 張</span></div>"
                f"<div><span class='label'>量能倍數</span><span class='val'>{row['volume_surge_multiple']:.2f}x</span></div>"
                f"<details class='metric-info'><summary><span class='label'>上漲量能</span><span class='val'>{row['impulse_volume_multiple']:.2f}x</span></summary><div class='tip'>{impulse_tip}</div></details>"
                f"<details class='metric-info'><summary><span class='label'>止跌訊號</span><span class='val'>{int(row['stop_signal_count'])} 個</span></summary><div class='tip'>{stop_tip}</div></details>"
                "</div><div class='trade-grid'>"
                f"<div><span class='label'>進場價</span><span class='val'>{_fmt_optional(row['entry_price'], '.2f')}</span></div>"
                f"<div><span class='label'>目標價</span><span class='val pos'>{_fmt_optional(row['target_price'], '.2f')}</span></div>"
                f"<div><span class='label'>停損價</span><span class='val neg'>{_fmt_optional(row['stop_loss_price'], '.2f')}</span></div>"
                f"<div><span class='label'>風報比</span><span class='val'>{rr_text}</span></div>"
                "</div>"
                f"<div class='plan'>{plan}</div>"
                f"<div class='reasons'>觀察條件：<ul>{reasons}</ul></div></div>"
            )
    html.append(f"{js}</body></html>")
    return "".join(html)


def export_latest_report(output_path: Path | None = None) -> Path:
    con = connect()
    results = latest_scan(con)
    if output_path is None:
        output_path = PROJECT_ROOT / "reports" / REPORT_FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_report_html(results), encoding="utf-8")
    return output_path
