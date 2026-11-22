"""KRX에서 직접 내려받은 투자자별 수급 CSV를 읽어서 예측용 지표로 만들어요.

※ 원본 CSV(data_private/)는 KRX 약관상 남에게 공개하면 안 돼요. GitHub에도 올리지 않아요.
   예측에는 '가공한 지표'만 쓰고, 사이트에는 예측 결과만 보여줘요.

폴더 구조 (data_private/flows/)
  samsung, hynix, semco, hyundai : 종목별 투자자 순매수 (원)
  k200fut_regular                : 코스피200 선물 투자자별 순매수 (정규장, 백만원)
  k200opt_call_regular / _put_    : 코스피200 옵션 콜/풋 투자자별 순매수 (정규장, 백만원)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FLOW_DIR = ROOT / "data_private" / "flows"
COLS = {"기관 합계": "inst", "기타법인": "corp", "개인": "indiv", "외국인 합계": "foreign"}


def load_folder(name: str) -> pd.DataFrame | None:
    """한 폴더의 CSV들을 합쳐서 날짜순 표로."""
    folder = FLOW_DIR / name
    if not folder.exists():
        return None
    parts = []
    for f in folder.glob("*.csv"):
        d = pd.read_csv(f, encoding="cp949")
        d["일자"] = pd.to_datetime(d["일자"])
        parts.append(d.set_index("일자")[list(COLS)].rename(columns=COLS).astype(float))
    if not parts:
        return None
    df = pd.concat(parts).sort_index()
    return df[~df.index.duplicated(keep="last")]


def zscore(s: pd.Series, window: int = 120) -> pd.Series:
    """평소(최근 120일) 대비 얼마나 큰지. 종목마다 단위·규모가 달라도 비교할 수 있게."""
    return (s - s.rolling(window, min_periods=60).mean()) / s.rolling(window, min_periods=60).std()


def flow_features(asset_id: str) -> pd.DataFrame:
    """날짜 d 행에는 'd일 아침 9:30에 알 수 있는' 전날까지의 수급 지표가 들어가요 (하루 밀어서)."""
    out = {}
    stock = load_folder(asset_id) if asset_id != "kospi200" else None
    if stock is not None:
        for c in ("foreign", "inst", "indiv"):
            out[f"fl_{c}_1"] = zscore(stock[c])
        out["fl_foreign_5"] = zscore(stock["foreign"].rolling(5).sum())
    fut = load_folder("k200fut_regular")
    if fut is not None:
        out["fl_fut_foreign_1"] = zscore(fut["foreign"])
        out["fl_fut_foreign_5"] = zscore(fut["foreign"].rolling(5).sum())
        out["fl_fut_indiv_1"] = zscore(fut["indiv"])
    call, put = load_folder("k200opt_call_regular"), load_folder("k200opt_put_regular")
    if call is not None and put is not None:
        idx = call.index.intersection(put.index)
        out["fl_opt_foreign_cp"] = zscore(call.loc[idx, "foreign"] - put.loc[idx, "foreign"])
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    return df.shift(1)  # 전날 수급 → 오늘 9:30에 사용 (KRX는 당일 수급을 오후 6시 이후 제공)
