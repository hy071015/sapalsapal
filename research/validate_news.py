"""9:30 이전 종목 뉴스(개수·좋은/나쁜 단어)가 9:30 상방·하방 예측을 개선하는지 검증 (장 초반 30분 모델 위에 추가)."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Users\hy071\OneDrive\바탕 화면\파이썬'")
os.environ["SAPAL_DATA_DIR"] = str(ROOT / "data_repo")
sys.path.insert(0, str(ROOT / "predictor"))
sys.path.insert(0, str(ROOT / "research"))
import build as B  # noqa: E402
import validate_early as VE  # noqa: E402

POS = ["급등", "상승", "강세", "수주", "계약", "호실적", "서프라이즈", "최대", "상향", "신고가", "돌파", "반등", "호재", "흑자", "증가", "매수"]
NEG = ["급락", "하락", "약세", "쇼크", "부진", "적자", "감소", "하향", "신저가", "우려", "리스크", "악재", "소송", "리콜", "유상증자", "매도"]
NEWS = ["n_news", "n_abn", "sent", "n_pos", "n_neg"]


def news_features(asset_id):
    rows = {}
    for f in (B.DATA_DIR / "kis").glob(f"*/news_{asset_id}.json"):
        day = f.parent.name.replace("-", "")
        items = [r for r in json.loads(f.read_text(encoding="utf-8")) if (r[0] + r[1]) <= day + "093000"]
        titles = [r[3] for r in items]
        pos = sum(any(k in t for k in POS) for t in titles)
        neg = sum(any(k in t for k in NEG) for t in titles)
        rows[pd.Timestamp(f.parent.name)] = dict(n_news=np.log1p(len(titles)), n_pos=np.log1p(pos), n_neg=np.log1p(neg),
                                                 sent=(pos - neg) / (pos + neg + 1))
    d = pd.DataFrame(rows).T.sort_index()
    d["n_abn"] = d.n_news - d.n_news.rolling(20, min_periods=10).median().shift(1)  # 평소보다 뉴스가 많은지
    return d


def main():
    tickers = sorted({a["ticker"] for a in B.ASSETS} | {t for a in B.ASSETS for t in a["drivers"]} | set(B.COMMON) | {"EWY"})
    daily = B.fetch_daily(tickers)
    for a in B.ASSETS:
        if a.get("kis_index"):
            continue
        B.patch_stock_daily(a, daily)
        kr = B.clean_korean(daily[a["ticker"]])
        df, f_morning, _, _ = B.build_frame(kr, a["drivers"], daily)
        df = df.join(VE.early_features(a["id"])[VE.EARLY]).join(news_features(a["id"])[NEWS])
        f_base = f_morning + ["gap"] + B.EARLY_FEATS
        valid = df.iloc[:-1].dropna(subset=f_base + NEWS + ["hi", "lo", "Open", "e_vol"])
        rng = valid.hi - valid.lo
        print(f"\n[{a['name']}] 검증 {len(valid) - VE.START}일 | 하루폭과 상관: 뉴스개수 {np.corrcoef(valid.n_news, rng)[0, 1]:.2f}, "
              f"평소대비 뉴스 {np.corrcoef(valid.n_abn, rng)[0, 1]:.2f}, 좋은-나쁜 단어 vs 종가방향 {np.corrcoef(valid.sent, valid.cl - valid.gap)[0, 1]:.2f}")
        sets = {"지금 모델(초반30분)": f_base, "+뉴스 개수": f_base + ["n_news", "n_abn"],
                "+좋은/나쁜 단어": f_base + ["sent", "n_pos", "n_neg"], "+뉴스 전부": f_base + NEWS}
        base = None
        for name, feats in sets.items():
            r = VE.evaluate(valid, feats)
            base = base or r
            print(f"  {name:14s} 고가오차 {r['mae_hi']:.3f}% ({(1 - r['mae_hi'] / base['mae_hi']) * 100:+.1f}%)  "
                  f"저가오차 {r['mae_lo']:.3f}% ({(1 - r['mae_lo'] / base['mae_lo']) * 100:+.1f}%)  "
                  f"최대범위적중 {r['cover']:.1f}% 폭 {r['width']:.2f}%")


if __name__ == "__main__":
    main()
