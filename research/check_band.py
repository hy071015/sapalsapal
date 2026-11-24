"""새 모델(장 초반 30분)의 최대 범위가 너무 좁은지: 기존·새·합친 범위의 적중률 비교."""
import os, sys
from pathlib import Path
ROOT = Path(r"C:\Users\hy071\OneDrive\바탕 화면\파이썬'")
os.environ["SAPAL_DATA_DIR"] = str(ROOT / "data_repo")
sys.path.insert(0, str(ROOT / "predictor")); sys.path.insert(0, str(ROOT / "research"))
import numpy as np, pandas as pd
import build as B, validate_early as VE

def wf_preds(valid, feats):
    return pd.concat([B.open_predict(valid.iloc[:i], valid.iloc[i:i + B.BLOCK], feats) for i in range(VE.START, len(valid), B.BLOCK)])

tickers = sorted({t for a in B.ASSETS for t in a["drivers"]} | set(B.COMMON) | {"EWY"} | {a["ticker"] for a in B.ASSETS if not a.get("kis_index")})
daily = B.fetch_daily(tickers)
vk = B.load_vkospi()
for a in B.ASSETS:
    if a.get("kis_index"):
        daily[a["ticker"]] = B.load_index_daily(a)
    else:
        B.patch_stock_daily(a, daily)
    kr = B.clean_korean(daily[a["ticker"]])
    df, f_morning, _, _ = B.build_frame(kr, a["drivers"], daily)
    f_open = f_morning + ["gap"]
    if a.get("vkospi") and vk is not None:
        B.add_vkospi(df, vk); f_open += B.VK_FEATS
    df = df.join(B.early_features(a["id"])[B.EARLY_FEATS])
    valid = df.iloc[:-1].dropna(subset=f_open + B.EARLY_FEATS + ["hi", "lo", "Open"])
    old, new = wf_preds(valid, f_open), wf_preds(valid, f_open + B.EARLY_FEATS)
    y = valid.loc[new.index]
    cov = lambda h, l: ((y.hi <= h + 1e-9) & (y.lo >= l - 1e-9)).mean() * 100
    hb, lb = np.maximum(new.p_hi_band, old.p_hi_band), np.minimum(new.p_lo_band, old.p_lo_band)
    w = lambda h, l: (h - l).mean() * 100
    print(f"[{a['name']}] {len(y)}일 최대범위 적중: 기존 {cov(old.p_hi_band, old.p_lo_band):.1f}% (폭 {w(old.p_hi_band, old.p_lo_band):.1f}%) | "
          f"새 {cov(new.p_hi_band, new.p_lo_band):.1f}% (폭 {w(new.p_hi_band, new.p_lo_band):.1f}%) | 합침 {cov(hb, lb):.1f}% (폭 {w(hb, lb):.1f}%)", flush=True)
