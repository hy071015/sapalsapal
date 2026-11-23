"""KRX Open API 옵션 과거 데이터 천천히 받기 (코스피200 옵션·위클리 + 우리 종목 주식옵션만 저장).

저장: data_private/krx_api/ (GitHub에 안 올라감)
실행: python predictor/krx_backfill_opt.py 2023-11-01
"""
from __future__ import annotations

import datetime as dt
import sys

from krx_api import get

STOCKS = ("삼성전자 옵션", "SK하이닉스 옵션", "삼성전기 옵션", "현대차 옵션")
FIELDS = ("PROD_NM", "RGHT_TP_NM", "ISU_NM", "TDD_CLSPRC", "IMP_VOLT", "NXTDD_BAS_PRC", "ACC_TRDVOL", "ACC_OPNINT_QTY")


def active(r):
    return "(야간)" not in r["ISU_NM"] and (r["ACC_TRDVOL"] not in ("", "0") or r["ACC_OPNINT_QTY"] not in ("", "0"))


def slim(r):
    for k in list(r):
        if k not in FIELDS:
            r.pop(k)
    return True


def keep_index(r):
    return r["PROD_NM"].startswith("코스피200") and active(r) and slim(r)


def keep_stock(r):
    return r["PROD_NM"] in STOCKS and active(r) and slim(r)


def main():
    start = dt.date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2023-11-01")
    d, n = dt.date.today() - dt.timedelta(days=1), 0
    while d >= start:
        if d.weekday() < 5:
            s = d.strftime("%Y%m%d")
            get("drv/opt_bydd_trd", s, keep=keep_index)
            get("drv/eqsop_bydd_trd", s, keep=keep_stock)
            n += 1
            if n % 50 == 0:
                print(d, n, flush=True)
        d -= dt.timedelta(days=1)
    print("done", n)


if __name__ == "__main__":
    main()
