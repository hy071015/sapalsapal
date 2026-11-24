"""한투 종목 뉴스 제목 수집 (계산용, 사이트에 제목을 보여주지 않음).

- news_between(code, 시작, 끝): 그 사이에 나온 종목 뉴스 제목 목록 (최신 → 과거로 40건씩)
- 실행: python predictor/kis_news.py <저장 폴더(data_repo)> [거래일 수=250]
  → kis/<날짜>/news_<종목>.json : 전날 15:30 ~ 당일 12:00 뉴스 [[날짜, 시각, 언론사, 제목], ...]
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import kis_api

STOCKS = {"samsung": "005930", "hynix": "000660", "semco": "009150", "hyundai": "005380"}


def news_between(code: str, start: str, end: str, max_calls: int = 8) -> list[list]:
    """start/end = 'YYYYMMDDHHMMSS'. end부터 거꾸로 받아 start 이전이 나오면 멈춰요."""
    out, seen = [], set()
    day, hour = end[:8], end[8:]
    for _ in range(max_calls):
        d = kis_api.get(f"{kis_api.Q}/news-title", "FHKST01011800", {
            "FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": code, "FID_TITL_CNTT": "",
            "FID_INPUT_DATE_1": day, "FID_INPUT_HOUR_1": hour, "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": ""})
        rows = d.get("output") or []
        if d.get("rt_cd") != "0" or not rows:
            break
        stop = False
        for r in rows:
            ts = r.get("data_dt", "") + r.get("data_tm", "")
            key = (ts, r.get("hts_pbnt_titl_cntt", ""))
            if ts < start:
                stop = True
                break
            if ts <= end and key not in seen:
                seen.add(key)
                out.append([r.get("data_dt"), r.get("data_tm"), r.get("dorg", ""), r.get("hts_pbnt_titl_cntt", "")])
        if stop or len(rows) < 40:
            break
        last = rows[-1].get("data_dt", "") + rows[-1].get("data_tm", "")
        day, hour = last[:8], last[8:]
        if (day + hour) <= start:
            break
    return out


def main():
    root = Path(sys.argv[1])
    n_days = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    days = [r["date"] for r in kis_api.index_daily("2001", start="20240101")]
    days = days[-n_days - 1:]
    for i in range(len(days) - 1, 0, -1):
        day, prev = days[i], days[i - 1]
        folder = root / "kis" / f"{day[:4]}-{day[4:6]}-{day[6:]}"
        for sid, code in STOCKS.items():
            f = folder / f"news_{sid}.json"
            if f.exists():
                continue
            rows = news_between(code, prev + "153000", day + "120000")
            folder.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        if (len(days) - i) % 20 == 0:
            print(f"{day} ({len(days) - i}/{len(days) - 1}일)", flush=True)
    print("done")


if __name__ == "__main__":
    main()
