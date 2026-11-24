"""장 초반 30분(9:00~9:30) 1분봉 지표가 그날 고가·저가 예측을 개선하는지 walk-forward 검증 (종목 4개)."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\hy071\OneDrive\바탕 화면\파이썬'")
os.environ["SAPAL_DATA_DIR"] = str(ROOT / "data_repo")
sys.path.insert(0, str(ROOT / "predictor"))
import build as B  # noqa: E402

START = 100   # 최소 학습 일수 (1분봉 기간이 짧아서)
EARLY = ["e_ret", "e_hi", "e_lo", "e_vol", "e_flow", "e_vwap"]


def early_features(asset_id):
    rows = {}
    for f in sorted((B.DATA_DIR / "kis").glob(f"*/min_krx_{asset_id}.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        m = [r for r in m if r[0] <= "0930"]  # 끝시각 9:30까지 = 9:00~9:30
        if len(m) < 20:
            continue
        o = m[0][1]
        c = m[-1][4]
        hi = max(r[2] for r in m)
        lo = min(r[3] for r in m)
        vol = sum(r[5] for r in m)
        up = sum(r[5] for r in m if r[4] > r[1])
        dn = sum(r[5] for r in m if r[4] < r[1])
        vwap = sum(r[4] * r[5] for r in m) / vol if vol else c
        rows[pd.Timestamp(f.parent.name)] = dict(e_ret=c / o - 1, e_hi=hi / o - 1, e_lo=1 - lo / o, e_vol_raw=vol,
                                                 e_flow=(up - dn) / vol if vol else 0, e_vwap=c / vwap - 1)
    d = pd.DataFrame(rows).T.sort_index()
    d["e_vol"] = np.log(d.e_vol_raw / d.e_vol_raw.rolling(20, min_periods=10).median().shift(1))  # 평소 대비 첫 30분 거래량
    return d


def evaluate(valid, feats):
    parts = []
    for i in range(START, len(valid), B.BLOCK):
        p = B.open_predict(valid.iloc[:i], valid.iloc[i:i + B.BLOCK], feats)
        parts.append(p)
    wf = pd.concat(parts).join(valid[["hi", "lo", "gap", "e_hi", "e_lo"]])
    # 9:30에 이미 아는 사실: 고가는 9:30까지의 고가보다 낮을 수 없고, 저가도 마찬가지
    known_hi = (1 + wf.gap) * (1 + wf.e_hi) - 1
    known_lo = (1 + wf.gap) * (1 - wf.e_lo) - 1
    for s in ("", "_band"):
        wf[f"p_hi{s}"] = np.maximum(wf[f"p_hi{s}"], known_hi)
        wf[f"p_lo{s}"] = np.minimum(wf[f"p_lo{s}"], known_lo)
    inband = (wf.hi <= wf.p_hi_band + 1e-9) & (wf.lo >= wf.p_lo_band - 1e-9)
    return dict(mae_hi=(wf.p_hi - wf.hi).abs().mean() * 100, mae_lo=(wf.p_lo - wf.lo).abs().mean() * 100,
                cover=inband.mean() * 100, width=(wf.p_hi_band - wf.p_lo_band).mean() * 100, n=len(wf),
                above=(wf.hi > wf.p_hi).mean() * 100, below=(wf.lo < wf.p_lo).mean() * 100)


def main():
    tickers = sorted({a["ticker"] for a in B.ASSETS} | {t for a in B.ASSETS for t in a["drivers"]} | set(B.COMMON) | {"EWY"})
    daily = B.fetch_daily(tickers)
    for a in B.ASSETS:
        if a.get("kis_index"):
            continue
        B.patch_stock_daily(a, daily)
        kr = B.clean_korean(daily[a["ticker"]])
        df, f_morning, _, _ = B.build_frame(kr, a["drivers"], daily)
        e = early_features(a["id"])
        df = df.join(e[EARLY])
        f_open = f_morning + ["gap"]
        valid = df.iloc[:-1].dropna(subset=f_open + EARLY + ["hi", "lo", "Open"])
        print(f"\n[{a['name']}] 1분봉 있는 날 {len(e)}일, 검증 {len(valid) - START}일 ({valid.index[START].date()}~{valid.index[-1].date()})")
        sets = {"A 지금 모델": f_open, "B +초반30분 전부": f_open + EARLY,
                "C +움직임·고저만": f_open + ["e_ret", "e_hi", "e_lo"], "D +거래량·매수세만": f_open + ["e_vol", "e_flow", "e_vwap"]}
        base = None
        for name, feats in sets.items():
            r = evaluate(valid, feats)
            base = base or r
            print(f"  {name:16s} 고가오차 {r['mae_hi']:.3f}% ({(1 - r['mae_hi'] / base['mae_hi']) * 100:+.1f}%)  "
                  f"저가오차 {r['mae_lo']:.3f}% ({(1 - r['mae_lo'] / base['mae_lo']) * 100:+.1f}%)  "
                  f"최대범위적중 {r['cover']:.1f}% 폭 {r['width']:.2f}%  상방넘음 {r['above']:.0f}% 하방넘음 {r['below']:.0f}%")


if __name__ == "__main__":
    main()
