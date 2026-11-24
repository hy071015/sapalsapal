"""코스피200 지수: KODEX 200 ETF 1분봉으로 만든 장 초반 30분 지표가 9:30 예측을 개선하는지 검증."""
import os
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\hy071\OneDrive\바탕 화면\파이썬'")
os.environ["SAPAL_DATA_DIR"] = str(ROOT / "data_repo")
sys.path.insert(0, str(ROOT / "predictor"))
sys.path.insert(0, str(ROOT / "research"))
import build as B  # noqa: E402
import validate_early as VE  # noqa: E402


def main():
    tickers = sorted({t for a in B.ASSETS for t in a["drivers"]} | set(B.COMMON) | {"EWY"})
    daily = B.fetch_daily(tickers)
    a = B.ASSETS[0]
    daily[a["ticker"]] = B.load_index_daily(a)
    vk = B.load_vkospi()
    kr = B.clean_korean(daily[a["ticker"]])
    df, f_morning, _, _ = B.build_frame(kr, a["drivers"], daily)
    if vk is not None:
        B.add_vkospi(df, vk)
    e = B.early_features(a["id"])
    df = df.join(e[B.EARLY_FEATS])
    f_open = f_morning + ["gap"] + (B.VK_FEATS if vk is not None else [])
    valid = df.iloc[:-1].dropna(subset=f_open + B.EARLY_FEATS + ["hi", "lo", "Open"])
    print(f"[코스피200] ETF 1분봉 {len(e)}일, 검증 {len(valid) - VE.START}일 ({valid.index[VE.START].date()}~{valid.index[-1].date()})")
    base = None
    for name, feats in {"지금 모델": f_open, "+장 초반 30분(ETF)": f_open + B.EARLY_FEATS}.items():
        r = VE.evaluate(valid, feats)
        base = base or r
        print(f"  {name:14s} 고가오차 {r['mae_hi']:.3f}% ({(1 - r['mae_hi'] / base['mae_hi']) * 100:+.1f}%)  "
              f"저가오차 {r['mae_lo']:.3f}% ({(1 - r['mae_lo'] / base['mae_lo']) * 100:+.1f}%)  "
              f"최대범위적중 {r['cover']:.1f}% 폭 {r['width']:.2f}%")


if __name__ == "__main__":
    main()
