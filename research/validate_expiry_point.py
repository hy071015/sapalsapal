"""옵션 만기일 코스피200 종가를 한 점으로 예측: 지금 가격 vs 최대고통가격(Max Pain) vs 미결제약정 중심 vs 섞기.
자료: data_private/krx_api/drv_opt_bydd_trd (KRX, 2023-11~), 지수 종가: data_repo/index_daily_kospi200.json (한투)."""
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\hy071\OneDrive\바탕 화면\파이썬'")
OPT = ROOT / "data_private" / "krx_api" / "drv_opt_bydd_trd"
RE = re.compile(r"\s([CP])\s+(\S+)\s+([\d,\.]+)")
KIND = {"코스피200 옵션": "월물", "코스피200 위클리(목) 옵션": "위클리(목)", "코스피200 위클리(월) 옵션": "위클리(월)"}


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return np.nan


idx = pd.DataFrame(json.loads((ROOT / "data_repo" / "index_daily_kospi200.json").read_text(encoding="utf-8")))
close = pd.Series(idx.close.values, index=pd.to_datetime(idx.date))

book = defaultdict(dict)   # (상품, 만기코드) -> {날짜: [(C/P, 행사가, 미결제)]}
for f in sorted(OPT.glob("*.json")):
    d = pd.Timestamp(f.stem)
    for r in json.loads(f.read_text(encoding="utf-8")):
        k = KIND.get(r["PROD_NM"])
        m = RE.search(r["ISU_NM"])
        if not k or not m:
            continue
        oi = num(r["ACC_OPNINT_QTY"])
        if oi and oi > 0:
            book[(k, m.group(2))].setdefault(d, []).append((m.group(1), num(m.group(3)), oi))

last_day = max(pd.Timestamp(f.stem) for f in OPT.glob("*.json"))
tdays = close.index
rows = []
for (kind, code), days in book.items():
    exp = max(days)  # 마지막으로 거래된 날 = 만기일
    if exp >= last_day - pd.Timedelta(days=3) or exp not in close.index:
        continue
    settle = close.loc[exp]
    for d, opts in days.items():
        if d >= exp or d not in close.index:
            continue
        n = int(((tdays > d) & (tdays <= exp)).sum())  # 만기까지 남은 거래일
        if not 1 <= n <= 15:
            continue
        s = close.loc[d]
        opts = [o for o in opts if abs(o[1] / s - 1) <= 0.2]
        if len(opts) < 6:
            continue
        strikes = np.array(sorted({o[1] for o in opts}))
        pain = [sum(oi * max(0, K - k) if t == "C" else oi * max(0, k - K) for t, k, oi in opts) for K in strikes]
        mp = strikes[int(np.argmin(pain))]
        w = np.array([o[2] for o in opts]); ks = np.array([o[1] for o in opts])
        center = float((w * ks).sum() / w.sum())
        rows.append(dict(kind=kind, n=n, s=s, mp=mp, center=center, settle=settle, exp=exp))

df = pd.DataFrame(rows)
df["bucket"] = pd.cut(df.n, [0, 1, 3, 7, 15], labels=["1일 전", "2~3일 전", "4~7일 전", "8~15일 전"])
err = lambda p: (p / df.settle - 1).abs() * 100
df["e_now"] = err(df.s)
df["e_mp"] = err(df.mp)
df["e_center"] = err(df.center)
df["e_half"] = err(df.s + 0.5 * (df.mp - df.s))
df["e_quarter"] = err(df.s + 0.25 * (df.mp - df.s))
df["closer"] = (df.e_mp < df.e_now)
print(f"만기 {df.exp.nunique()}번, 예측 시점 {len(df)}개 ({df.exp.min().date()}~{df.exp.max().date()})\n")
g = df.groupby(["kind", "bucket"], observed=True).agg(
    n=("e_now", "size"), 지금가격=("e_now", "mean"), 최대고통=("e_mp", "mean"), 미결제중심=("e_center", "mean"),
    섞기50=("e_half", "mean"), 섞기25=("e_quarter", "mean"), 최대고통이더가까운비율=("closer", "mean"))
pd.set_option("display.width", 200)
print(g.round(3).to_string())
print("\n전체 평균 오차(%):", df[["e_now", "e_mp", "e_center", "e_half", "e_quarter"]].mean().round(3).to_dict())
