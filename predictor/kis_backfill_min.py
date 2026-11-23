"""한투 과거 1분봉 채우기 (종목만, 이미 있는 날은 건너뜀). 비공개 저장소 폴더에 저장해요.

실행: python predictor/kis_backfill_min.py <저장 폴더(data_repo)> [거래일 수=250]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import kis_api

STOCKS = {"samsung": "005930", "hynix": "000660", "semco": "009150", "hyundai": "005380"}


def main():
    root = Path(sys.argv[1])
    n_days = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    days = [r["date"] for r in kis_api.index_daily("2001", start="20240101")][-n_days - 1:-1]  # 오늘 제외 최근 거래일
    done = 0
    for i, day in enumerate(reversed(days)):
        folder = root / "kis" / f"{day[:4]}-{day[4:6]}-{day[6:]}"
        for sid, code in STOCKS.items():
            f = folder / f"min_krx_{sid}.json"
            if f.exists():
                continue
            m = kis_api.minute_bars_day(code, day)
            m = [r for r in m if not (r[0] > "1520" and r[0] != "1530" and r[5] == 0)]  # 종가 동시호가 중 빈 봉 제거
            if m:
                folder.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(m), encoding="utf-8")
                done += 1
        if (i + 1) % 20 == 0:
            print(f"{day} ({i + 1}/{len(days)}일)", flush=True)
    print("done", done)


if __name__ == "__main__":
    main()
