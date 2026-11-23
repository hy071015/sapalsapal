"""KRX Open API 과거 데이터 천천히 받기 (한 번만 돌리면 돼요. 받은 날짜는 저장돼서 다시 안 받아요).

받는 것: 파생상품지수(VKOSPI 포함), 선물 일별매매정보(코스피200 선물 정규·야간)
저장: data_private/krx_api/ (GitHub에 안 올라감)
실행: python predictor/krx_backfill.py 2019-11-01
"""
from __future__ import annotations

import datetime as dt
import sys

from krx_api import get

PATHS = ["idx/drvprod_dd_trd", "drv/fut_bydd_trd"]


def main():
    start = dt.date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2019-11-01")
    end = dt.date.today() - dt.timedelta(days=1)  # 오늘 자료는 내일 아침에 나와요
    d, n = end, 0
    while d >= start:  # 최근 날짜부터 거꾸로
        if d.weekday() < 5:
            for p in PATHS:
                get(p, d.strftime("%Y%m%d"))
            n += 1
            if n % 50 == 0:
                print(d, n, flush=True)
        d -= dt.timedelta(days=1)
    print("done", n)


if __name__ == "__main__":
    main()
