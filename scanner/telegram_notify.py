from __future__ import annotations

import os
from pathlib import Path

import requests
from requests import RequestException

from .config import PROJECT_ROOT
from .db import connect
from .report import REPORT_FILENAME


def _signal_counts() -> str:
    con = connect()
    row = con.execute(
        """
        SELECT
            SUM(CASE WHEN list_contains(str_split(signal_types, ','), 'bottom_impulse') THEN 1 ELSE 0 END) AS bottom_impulse,
            SUM(CASE WHEN list_contains(str_split(signal_types, ','), 'breakout_pullback') THEN 1 ELSE 0 END) AS breakout_pullback,
            SUM(CASE WHEN list_contains(str_split(signal_types, ','), 'intraday_breakout_pullback') THEN 1 ELSE 0 END) AS intraday_breakout_pullback,
            SUM(CASE WHEN list_contains(str_split(signal_types, ','), 'high_pullback_watch') THEN 1 ELSE 0 END) AS high_pullback,
            SUM(CASE WHEN list_contains(str_split(signal_types, ','), 'volume_stop_reversal') THEN 1 ELSE 0 END) AS volume_stop_reversal,
            SUM(CASE WHEN list_contains(str_split(signal_types, ','), 'volume_stop_watch') THEN 1 ELSE 0 END) AS volume_stop_watch,
            SUM(CASE WHEN list_contains(str_split(signal_types, ','), 'volume_surge') THEN 1 ELSE 0 END) AS volume_surge,
            SUM(CASE WHEN contains(signal_types, ',') THEN 1 ELSE 0 END) AS multi_signal,
            MAX(as_of) AS as_of
        FROM scan_results
        WHERE passed
        """
    ).fetchone()
    return (
        f"資料日：{row[8]}\n"
        f"底部起漲型：{int(row[0] or 0)}\n"
        f"突破回踩型：{int(row[1] or 0)}\n"
        f"日內突破回踩：{int(row[2] or 0)}\n"
        f"高檔回落觀察型：{int(row[3] or 0)}\n"
        f"量增止跌型：{int(row[4] or 0)}\n"
        f"量增止跌觀察：{int(row[5] or 0)}\n"
        f"成交量暴增型：{int(row[6] or 0)}\n"
        f"多訊號：{int(row[7] or 0)}"
    )


def send_report(report_path: Path | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.")
        return

    report_path = report_path or PROJECT_ROOT / "reports" / REPORT_FILENAME
    report_url = os.getenv("TELEGRAM_REPORT_URL")
    caption = "每日線型掃描報告\n" + _signal_counts()
    if report_url:
        caption += f"\n\n報告網址：{report_url}"

    api = f"https://api.telegram.org/bot{token}"
    try:
        if report_url:
            response = requests.post(
                f"{api}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": caption,
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
        elif report_path.exists():
            with report_path.open("rb") as file_obj:
                response = requests.post(
                    f"{api}/sendDocument",
                    data={"chat_id": chat_id, "caption": caption},
                    files={"document": (report_path.name, file_obj, "text/html")},
                    timeout=60,
                )
        else:
            response = requests.post(
                f"{api}/sendMessage",
                data={"chat_id": chat_id, "text": caption},
                timeout=30,
            )
        response.raise_for_status()
    except RequestException as exc:
        status = getattr(exc.response, "status_code", None)
        body = getattr(exc.response, "text", "")
        detail = f" HTTP {status}: {body}" if status else f" {exc.__class__.__name__}"
        print(f"Telegram send failed.{detail}")
        return
    print("Telegram report sent.")
