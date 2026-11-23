"""사팔사팔 예측 계산 프로그램

하루 흐름 (한국시간):
  16:10 저녁  - 오늘 실제 시세로 '내일 예비 예측' (미국장 반영 전)
  07:40 아침  - 밤사이 미국 시장(나스닥·S&P 선물, EWY, 반도체지수 등)을 반영해 '오늘 예측 확정'
  12:30 점심  - 아침 예측과 실제 오전장을 비교해서 '보정' (오전 고가 돌파·저가 이탈 확률, 최대 범위)
  + 만기 예측 - 이번 주 월·목 위클리 만기, 이번 달 월물 만기까지의 예상 변동 범위

어느 시간에 실행해도 그 시간에 맞는 일을 알아서 해요. (--mode 로 강제 가능)
결과: site/data.json (사이트가 읽는 파일), site/log.json (매일 예측과 실제 결과 기록)

※ 코스피200 선물·옵션 데이터(미결제약정, 위클리·월물 옵션 내재변동성)는
   KRX Open API 승인 후 추가할 예정이에요. 들어갈 자리는 'TODO(KRX)'로 표시해 뒀어요.

실행: python predictor/build.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import warnings
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data.json"
LOG = ROOT / "site" / "log.json"

# 예측할 종목과, 각 종목에 영향을 주는 해외 지표(첫 번째가 가장 중요한 지표)
ASSETS = [
    dict(id="kospi200", name="코스피200", ticker="069500.KS", level_ticker="^KS200",
         unit="pt", decimals=2, drivers=["NQ=F", "ES=F", "EWY"]),
    dict(id="samsung", name="삼성전자", ticker="005930.KS", unit="원", decimals=0,
         drivers=["EWY", "^SOX", "NQ=F", "MU", "NVDA"]),
    dict(id="hynix", name="SK하이닉스", ticker="000660.KS", unit="원", decimals=0,
         drivers=["^SOX", "MU", "NVDA", "NQ=F", "EWY"]),
    dict(id="semco", name="삼성전기", ticker="009150.KS", unit="원", decimals=0,
         drivers=["^SOX", "EWY", "AAPL", "NQ=F"]),
    dict(id="hyundai", name="현대차", ticker="005380.KS", unit="원", decimals=0,
         drivers=["EWY", "ES=F", "NQ=F", "TM"]),
]
COMMON = ["^VIX", "KRW=X"]
LABELS = {
    "EWY": "한국 ETF(EWY)", "ES=F": "S&P500 선물", "NQ=F": "나스닥 선물", "^SOX": "반도체지수",
    "MU": "마이크론", "NVDA": "엔비디아", "AAPL": "애플", "TM": "도요타", "^VIX": "공포지수(VIX)",
    "KRW=X": "원/달러 환율",
}

# 한국거래소 휴장일 (주말 제외). 매년 말 거래소 공지를 보고 다음 해 날짜를 추가해야 해요.
KR_HOLIDAYS = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02", "2026-05-01",
    "2026-05-05", "2026-05-25", "2026-06-03", "2026-08-17", "2026-09-24", "2026-09-25",
    "2026-10-05", "2026-10-09", "2026-12-25", "2026-12-31",
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-05", "2027-05-13",
    "2027-08-16", "2027-09-14", "2027-09-15", "2027-09-16", "2027-10-04", "2027-10-11",
    "2027-12-27", "2027-12-31",
}

TARGETS = ["gap", "hi", "lo", "cl"]  # 시가, 고가, 저가, 종가 (전일 종가 대비 변화율)
STAT_DAYS = 250      # 성적표 기간 (약 1년)
WF_DAYS = 760        # 과거 예측을 다시 계산해 둘 기간 (점심 보정 학습에 사용)
BLOCK = 20           # 20거래일마다 모델을 다시 학습
MIN_TRAIN = 300      # 최소 학습 기간
HISTORY_DAYS = 60    # 사이트 차트에 보여줄 기간
MID_CUTOFF = 12      # 점심 보정: 12시 전까지의 오전장 데이터 사용
MID_MIN_TRAIN = 200
BREAK = 0.001        # 오전 고가·저가를 0.1% 넘게 넘어서면 '돌파'로 봄
MID_BASE = ["m_open", "m_high", "m_low", "m_last", "m_range", "m_pos", "atr10"]
MID_SURPRISE = ["s_open", "s_high", "s_low"]  # 아침 예측과 실제 오전장의 차이
RANGE_Q = (0.10, 0.90)  # 만기 예측: 80% 확률 범위


# ───────────────────────────── 공통 도구 ─────────────────────────────
def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in KR_HOLIDAYS


def next_trading_day(d) -> pd.Timestamp:
    n = pd.Timestamp(d) + pd.Timedelta(days=1)
    while not is_trading_day(n.date()):
        n += pd.Timedelta(days=1)
    return n


def prev_trading_day_or_same(d) -> pd.Timestamp:
    n = pd.Timestamp(d)
    while not is_trading_day(n.date()):
        n -= pd.Timedelta(days=1)
    return n


def trading_days_between(a, b) -> int:
    """a 다음 날부터 b까지(포함) 거래일 수."""
    n, d = 0, pd.Timestamp(a)
    while d < pd.Timestamp(b):
        d = next_trading_day(d)
        n += 1
    return n


def tick_round(price: float) -> int:
    """국내 주식 호가 단위에 맞춰 반올림 (실제로 거래할 수 있는 가격으로)."""
    for limit, tick in [(2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)]:
        if price < limit:
            return int(round(price / tick) * tick)
    return int(round(price / 1_000) * 1_000)


def pct(x: float) -> float:
    return round(float(x) * 100, 3)


def _design(X, mu, sd):
    return np.c_[np.ones(len(X)), (X - mu) / sd]


def _scale(X):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    return mu, sd


def ridge_fit(X, y, lam=3.0):
    mu, sd = _scale(X)
    Z = _design(X, mu, sd)
    reg = np.eye(Z.shape[1]) * lam
    reg[0, 0] = 0
    return mu, sd, np.linalg.solve(Z.T @ Z + reg, Z.T @ y)


def quantile_fit(X, y, q=0.5, lam=3.0, iters=40):
    """분위 회귀: q=0.5면 '가운데 값', q=0.9면 '90%의 날은 이보다 작았다'는 값을 학습."""
    mu, sd, beta = ridge_fit(X, y, lam)
    Z = _design(X, mu, sd)
    reg = np.eye(Z.shape[1]) * lam
    reg[0, 0] = 0
    for _ in range(iters):
        r = y - Z @ beta
        w = np.where(r >= 0, q, 1 - q) / np.maximum(np.abs(r), 1e-4)
        beta = np.linalg.solve(Z.T @ (Z * w[:, None]) + reg, Z.T @ (w * y))
    return mu, sd, beta


def logistic_fit(X, y, lam=3.0, iters=25):
    """확률 모델: 어떤 일이 일어날 확률(0~100%)을 학습."""
    mu, sd = _scale(X)
    Z = _design(X, mu, sd)
    reg = np.eye(Z.shape[1]) * lam
    reg[0, 0] = 0
    beta = np.zeros(Z.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Z @ beta))
        w = np.maximum(p * (1 - p), 1e-6)
        beta += np.linalg.solve(Z.T @ (Z * w[:, None]) + reg, Z.T @ (y - p) - reg @ beta)
    return mu, sd, beta


def predict(model, X):
    mu, sd, beta = model
    return _design(X, mu, sd) @ beta


def predict_prob(model, X):
    return 1 / (1 + np.exp(-predict(model, X)))


# ───────────────────────────── 데이터 받기 ─────────────────────────────
def _download(tickers, **kw):
    """야후에서 받기. 한꺼번에 받다가 빠진 종목은 하나씩 최대 3번 다시 받아요."""
    import time
    raw = yf.download(tickers, group_by="ticker", auto_adjust=False, progress=False, threads=True, **kw)
    out = {}
    for t in tickers:
        try:
            d = raw[t].dropna(subset=["Close"]).copy()
        except KeyError:
            d = pd.DataFrame()
        out[t] = d
    for t in [t for t in tickers if out[t].empty]:
        for attempt in range(3):
            time.sleep(2 + attempt * 3)
            d = yf.download(t, auto_adjust=False, progress=False, **kw)
            if len(d):
                if isinstance(d.columns, pd.MultiIndex):
                    d.columns = d.columns.get_level_values(0)
                out[t] = d.dropna(subset=["Close"]).copy()
                break
    return {t: d for t, d in out.items() if not d.empty}


def fetch_daily(tickers):
    raw = _download(tickers, period="7y", interval="1d")
    out = {}
    for t, d in raw.items():
        d = d[["Open", "High", "Low", "Close", "Volume"]].copy()
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        out[t] = d[~d.index.duplicated(keep="last")].sort_index()
    return out


def fetch_intraday(tickers):
    raw = _download(tickers, period="730d", interval="60m")
    out = {}
    for t, d in raw.items():
        d = d[["Open", "High", "Low", "Close"]].dropna().copy()
        idx = pd.to_datetime(d.index)
        d.index = idx.tz_convert(KST) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(KST)
        out[t] = d[(d > 0).all(axis=1)]
    return out


ARCHIVE = ROOT / "predictor" / "archive"   # 5분봉을 매일 쌓아두는 곳 (나중에 9:30 가격 흐름 학습에 사용)
INTRADAY_OUT = ROOT / "site" / "intraday"  # 사이트 팝업용 종목별 분봉 파일


def fetch_5m(tickers):
    """최근 60거래일 5분봉을 받아서, 예전에 저장한 것과 합쳐 보관해요."""
    raw = yf.download(tickers, period="60d", interval="5m", group_by="ticker",
                      auto_adjust=False, progress=False, threads=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    out = {}
    for t in tickers:
        try:
            d = raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]).copy()
        except KeyError:
            d = pd.DataFrame()
        if len(d):
            idx = pd.to_datetime(d.index)
            d.index = idx.tz_convert(KST) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(KST)
            d = d[(d[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
        path = ARCHIVE / f"{t.replace('.', '_')}_5m.csv"
        if path.exists():
            old = pd.read_csv(path, index_col=0)
            old.index = pd.to_datetime(old.index, utc=True).tz_convert(KST)
            d = pd.concat([old, d]) if len(d) else old
        d = d[~d.index.duplicated(keep="last")].sort_index()
        if len(d):
            d.to_csv(path)
        out[t] = d
    return out


def write_intraday(asset_id, bars, history, lv, today_rec=None):
    """사이트 팝업용: 차트에 나오는 날들의 5분봉을 가격(코스피200은 포인트)으로 저장."""
    INTRADAY_OUT.mkdir(parents=True, exist_ok=True)
    days = {h["day"] for h in history}
    if today_rec:
        days.add(today_rec)
    res = {}
    if len(bars):
        for day, g in bars.groupby(bars.index.date):
            k = day.isoformat()
            if k in days:
                res[k] = [[ts.strftime("%H:%M"), lv(r.Open), lv(r.High), lv(r.Low), lv(r.Close)] for ts, r in g.iterrows()]
    (INTRADAY_OUT / f"{asset_id}.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def clean_korean(d):
    """국내 종목: 이상한 행을 지우고, 장중(미완성) 데이터는 제외한다."""
    d = d[(d[["Open", "High", "Low", "Close"]] > 0).all(axis=1) & (d.Volume > 0)]
    d = d[(d.High >= d[["Open", "Close"]].max(axis=1)) & (d.Low <= d[["Open", "Close"]].min(axis=1))]
    n = now_kst()
    if len(d) and d.index[-1].date() == n.date() and n.time() < dt.time(15, 50):
        d = d.iloc[:-1]  # 오늘 장이 아직 끝나지 않음
    return d


def asof_before(s, dates):
    """각 날짜 d에 대해, d '이전' 날짜의 마지막 해외 종가.
    (미국 d-1일 장은 한국 시간 d일 새벽에 끝나므로 아침 7시에 알 수 있다)"""
    idx = s.index.searchsorted(dates, side="left") - 1
    vals = s.values[np.clip(idx, 0, None)].astype(float)
    vals[idx < 0] = np.nan
    return vals


# ───────────────────────────── 아침·저녁 예측 ─────────────────────────────
def build_frame(kr, drivers, mkt):
    """학습용 표: 한 행 = 국내 거래일 하루. 마지막 행은 다음 거래일(예측 대상)."""
    next_day = next_trading_day(kr.index[-1])
    dates = kr.index.append(pd.DatetimeIndex([next_day]))
    df = kr.reindex(dates)
    df["prev_close"] = df.Close.shift(1)
    prev_dates = dates[:-1].insert(0, dates[0] - pd.Timedelta(days=1))

    # 전날 국내 장 정보 (저녁에도, 아침에도 알 수 있음)
    rng = (df.High - df.Low) / df.prev_close
    df["k_ret"] = (df.Close / df.prev_close - 1).shift(1)
    df["k_range"] = rng.shift(1)
    df["k_pos"] = ((df.Close - df.Low) / (df.High - df.Low).replace(0, np.nan)).fillna(0.5).shift(1)
    df["atr10"] = rng.rolling(10).mean().shift(1)
    df["ret5"] = (df.Close / df.Close.shift(5) - 1).shift(1)
    evening = ["k_ret", "k_range", "k_pos", "atr10", "ret5"]
    # TODO(KRX): 코스피200 선물 미결제약정 변화, 외국인 선물 순매수, 위클리옵션 내재변동성을 여기 추가

    morning = list(evening)
    for t in drivers + ["KRW=X"]:
        if t in mkt:
            c = mkt[t].Close
            df[f"r_{t}"] = asof_before(c, dates) / asof_before(c, prev_dates) - 1
            morning.append(f"r_{t}")
    if "^VIX" in mkt:
        v = mkt["^VIX"].Close
        df["vix"] = np.log(asof_before(v, dates))
        df["vix_chg"] = df["vix"] - np.log(asof_before(v, prev_dates))
        df["vix_e"] = np.log(asof_before(v, prev_dates))  # 전날 저녁에 알 수 있는 VIX
        morning += ["vix", "vix_chg"]
        evening.append("vix_e")
    df["abs_main"] = df[f"r_{drivers[0]}"].abs()
    morning.append("abs_main")

    df["gap"] = df.Open / df.prev_close - 1
    df["hi"] = df.High / df.prev_close - 1
    df["lo"] = df.Low / df.prev_close - 1
    df["cl"] = df.Close / df.prev_close - 1
    df["base_hi"] = df.hi.rolling(20).mean().shift(1)  # 비교용 '단순 예측': 최근 20일 평균
    df["base_lo"] = df.lo.rolling(20).mean().shift(1)
    return df, morning, evening, next_day


def predict_rows(train, test, feats):
    out = pd.DataFrame(index=test.index)
    for t in TARGETS:
        out[f"p_{t}"] = predict(ridge_fit(train[feats].values, train[t].values), test[feats].values)
    out["p_hi"] = np.maximum(out.p_hi, out.p_gap)  # 고가는 시가보다 낮을 수 없다
    out["p_lo"] = np.minimum(out.p_lo, out.p_gap)
    return out


def walk_forward(valid, feats):
    """과거를 '그때 알 수 있던 정보만으로' 다시 예측해 보기."""
    start = max(MIN_TRAIN, len(valid) - WF_DAYS)
    parts = [predict_rows(valid.iloc[:i], valid.iloc[i:i + BLOCK], feats) for i in range(start, len(valid), BLOCK)]
    return pd.concat(parts).join(valid[TARGETS + ["base_hi", "base_lo"]])


def day_stats(wf):
    wf = wf.tail(STAT_DAYS)
    mae_hi, mae_lo = (wf.p_hi - wf.hi).abs().mean(), (wf.p_lo - wf.lo).abs().mean()
    base_hi, base_lo = (wf.base_hi - wf.hi).abs().mean(), (wf.base_lo - wf.lo).abs().mean()
    return dict(
        days=int(len(wf)), start=wf.index[0].strftime("%Y-%m-%d"), end=wf.index[-1].strftime("%Y-%m-%d"),
        mae_high=pct(mae_hi), mae_low=pct(mae_lo), mae_open=pct((wf.p_gap - wf.gap).abs().mean()),
        base_mae_high=pct(base_hi), base_mae_low=pct(base_lo),
        improve=round(float(1 - (mae_hi + mae_lo) / (base_hi + base_lo)) * 100, 1),
        dir_acc=round(float((np.sign(wf.p_cl) == np.sign(wf.cl)).mean()) * 100, 1),
        in_range=round(float(((wf.cl >= wf.p_lo) & (wf.cl <= wf.p_hi)).mean()) * 100, 1),
        # 시작가 예측 성적: 오차, '전일 종가 그대로 시작' 가정 대비, 상승·하락 출발 방향 적중
        base_mae_open=pct(wf.gap.abs().mean()),
        open_dir_acc=round(float((np.sign(wf.p_gap) == np.sign(wf.gap)).mean()) * 100, 1),
    )


# ───────────────────────────── 시가 반영 (9:30) ─────────────────────────────
GAP_FEATS = ["abs_gap", "gap_pos", "gap_neg", "gap_atr"]  # 갭 크기·방향 (최대 범위 민감도용)


def add_gap_feats(d):
    d = d.copy()
    d["abs_gap"] = d.gap.abs()
    d["gap_pos"] = d.gap.clip(lower=0)
    d["gap_neg"] = d.gap.clip(upper=0)
    d["gap_atr"] = d.abs_gap / d.atr10  # 평소 변동폭 대비 갭이 얼마나 큰지
    return d


def open_predict(train, test, feats):
    """실제 시작가를 안 뒤: 시작가에서 위로 얼마(up), 아래로 얼마(dn) 움직일지.
    - 상방·하방 숫자: 가운데 값(중앙값) → 평균적으로 가장 정확
    - 최대 범위: 갭 크기·방향까지 반영 → 갭이 크게 난 날은 범위가 자동으로 넓어짐 (2026-11 검증으로 채택)"""
    train, test = add_gap_feats(train), add_gap_feats(test)
    up = (train.High / train.Open - 1).values
    dn = (1 - train.Low / train.Open).values
    out = pd.DataFrame(index=test.index)
    g = test.gap.values
    for name, q, fs in (("", 0.5, feats), ("_band", 0.95, feats + GAP_FEATS)):
        X, Xt = train[fs].values, test[fs].values
        u = predict(quantile_fit(X, up, q), Xt).clip(min=0)
        d = predict(quantile_fit(X, dn, q), Xt).clip(min=0)
        out[f"p_hi{name}"] = (1 + g) * (1 + u) - 1   # 전일 종가 대비
        out[f"p_lo{name}"] = (1 + g) * (1 - d) - 1
    out["p_hi_band"] = np.maximum(out.p_hi_band, out.p_hi)
    out["p_lo_band"] = np.minimum(out.p_lo_band, out.p_lo)
    return out


def open_eval(valid, feats, wf_morning):
    start = max(MIN_TRAIN, len(valid) - STAT_DAYS)
    wf = pd.concat([open_predict(valid.iloc[:i], valid.iloc[i:i + BLOCK], feats) for i in range(start, len(valid), BLOCK)])
    wf = wf.join(valid[["hi", "lo", "gap", "High", "Low", "Open", "Close", "prev_close"]])
    up = (valid.High / valid.Open - 1).rolling(20).median().shift(1).reindex(wf.index)
    dn = (1 - valid.Low / valid.Open).rolling(20).median().shift(1).reindex(wf.index)
    b_hi, b_lo = (1 + wf.gap) * (1 + up) - 1, (1 + wf.gap) * (1 - dn) - 1  # 단순: 시가 ± 최근 20일 보통 폭
    m = wf_morning.reindex(wf.index)
    big = wf.gap.abs() >= wf.gap.abs().quantile(0.8)  # 갭이 큰 상위 20% 날
    inband = (wf.hi <= wf.p_hi_band + 1e-9) & (wf.lo >= wf.p_lo_band - 1e-9)
    return wf.tail(HISTORY_DAYS), dict(
        days=int(len(wf)), start=wf.index[0].strftime("%Y-%m-%d"), end=wf.index[-1].strftime("%Y-%m-%d"),
        mae_high=pct((wf.p_hi - wf.hi).abs().mean()), mae_low=pct((wf.p_lo - wf.lo).abs().mean()),
        morning_mae_high=pct((m.p_hi - wf.hi).abs().mean()), morning_mae_low=pct((m.p_lo - wf.lo).abs().mean()),
        base_mae_high=pct((b_hi - wf.hi).abs().mean()), base_mae_low=pct((b_lo - wf.lo).abs().mean()),
        band_cover=round(float(inband.mean()) * 100, 1),
        big_gap=pct(wf.gap.abs().quantile(0.8)), big_days=int(big.sum()),
        big_cover=round(float(inband[big].mean()) * 100, 1),
        big_mae_high=pct((wf.p_hi - wf.hi)[big].abs().mean()), big_mae_low=pct((wf.p_lo - wf.lo)[big].abs().mean()),
    )


# ───────────────────────────── 점심 보정 ─────────────────────────────
def morning_session(intra):
    """1시간봉에서 날짜별 오전장(9시~12시 전) 요약."""
    m = intra[intra.index.hour < MID_CUTOFF]
    g = m.groupby(m.index.date)
    s = pd.DataFrame({"o": g.Open.first(), "h": g.High.max(), "l": g.Low.min(), "c": g.Close.last(), "n": g.Open.size()})
    s.index = pd.to_datetime(s.index)
    return s[s.n >= 3]


def mid_features(s, prev_close, atr10, morning_pred):
    f = pd.DataFrame(index=s.index)
    f["m_open"] = s.o / prev_close - 1
    f["m_high"] = s.h / prev_close - 1
    f["m_low"] = s.l / prev_close - 1
    f["m_last"] = s.c / prev_close - 1
    f["m_range"] = f.m_high - f.m_low
    f["m_pos"] = ((f.m_last - f.m_low) / f.m_range.replace(0, np.nan)).fillna(0.5)
    f["atr10"] = atr10
    # 아침 예측 대비 실제 오전장이 얼마나 달랐는지 (보정의 핵심)
    f["s_open"] = f.m_open - morning_pred.p_gap
    f["s_high"] = f.m_high - morning_pred.p_hi
    f["s_low"] = f.m_low - morning_pred.p_lo
    return f


def mid_predict(train, test, feats):
    X, Xt = train[feats].values, test[feats].values
    out = pd.DataFrame(index=test.index)
    out["prob_up"] = predict_prob(logistic_fit(X, (train.up > BREAK).astype(float).values), Xt)
    out["prob_dn"] = predict_prob(logistic_fit(X, (train.dn > BREAK).astype(float).values), Xt)
    out["band_hi"] = test.m_high + predict(quantile_fit(X, train.up.values, 0.9), Xt).clip(min=0)
    out["band_lo"] = test.m_low - predict(quantile_fit(X, train.dn.values, 0.9), Xt).clip(min=0)
    out["p_cl"] = test.m_last + predict(quantile_fit(X, train.rest.values, 0.5), Xt)
    out["p_cl"] = out.p_cl.clip(out.band_lo, out.band_hi)
    return out


def mid_eval(valid, feats):
    start = max(MID_MIN_TRAIN, len(valid) - STAT_DAYS)
    wf = pd.concat([mid_predict(valid.iloc[:i], valid.iloc[i:i + BLOCK], feats) for i in range(start, len(valid), BLOCK)])
    wf = wf.join(valid[["hi", "lo", "cl", "up", "dn", "m_last"]])
    act_up, act_dn = wf.up > BREAK, wf.dn > BREAK

    def gain(p, y):  # 확률이 '평소 비율'만 쓰는 것보다 얼마나 나은지 (%)
        return round(float(1 - ((p - y) ** 2).mean() / ((y.mean() - y) ** 2).mean()) * 100, 1)

    return dict(
        days=int(len(wf)),
        up_rate=round(float(act_up.mean()) * 100, 1), dn_rate=round(float(act_dn.mean()) * 100, 1),
        up_acc=round(float(((wf.prob_up > 0.5) == act_up).mean()) * 100, 1),
        dn_acc=round(float(((wf.prob_dn > 0.5) == act_dn).mean()) * 100, 1),
        up_gain=gain(wf.prob_up, act_up.astype(float)), dn_gain=gain(wf.prob_dn, act_dn.astype(float)),
        band_cover=round(float(((wf.hi <= wf.band_hi + 1e-9) & (wf.lo >= wf.band_lo - 1e-9)).mean()) * 100, 1),
        mae_close=pct((wf.p_cl - wf.cl).abs().mean()), base_mae_close=pct((wf.m_last - wf.cl).abs().mean()),
    )


def run_midday(df, kr, intra, wf_morning, morning_today, today):
    """아침 예측과 오전장을 비교해 오늘 남은 시간을 보정 예측."""
    s = morning_session(intra)
    hist = df.iloc[:-1]
    f = mid_features(s, hist.prev_close.reindex(s.index), hist.atr10.reindex(s.index),
                     wf_morning.reindex(s.index))
    f = f.join(hist[["hi", "lo", "cl"]])
    f["up"] = (f.hi - f.m_high).clip(lower=0)
    f["dn"] = (f.m_low - f.lo).clip(lower=0)
    f["rest"] = f.cl - f.m_last
    feats = MID_BASE + MID_SURPRISE
    valid = f.dropna(subset=feats + ["up", "dn", "rest"])
    valid = valid[valid.index < today]
    stats = mid_eval(valid, feats)
    stats_base = mid_eval(valid, MID_BASE)  # 아침 예측 정보 없이 했을 때 (비교용)
    stats["without_morning"] = {k: stats_base[k] for k in ("up_acc", "dn_acc", "up_gain", "dn_gain", "band_cover")}

    if today not in s.index or morning_today is None:
        return None, stats
    pc = float(kr.Close.iloc[-1])  # 장중에는 kr의 마지막 행이 어제
    atr = float(((kr.High - kr.Low) / kr.Close.shift(1)).tail(10).mean())
    mp = pd.DataFrame([morning_today], index=[today])
    row = mid_features(s.loc[[today]], pd.Series(pc, index=[today]), pd.Series(atr, index=[today]), mp)
    p = mid_predict(valid, row, feats).iloc[0]
    m = s.loc[today]
    return dict(prev_close=pc, m_open=float(m.o), m_high=float(m.h), m_low=float(m.l), m_last=float(m.c),
                band_hi=float(p.band_hi), band_lo=float(p.band_lo), p_cl=float(p.p_cl),
                prob_up=round(float(p.prob_up) * 100), prob_dn=round(float(p.prob_dn) * 100),
                s_open=float(row.s_open.iloc[0])), stats


# ───────────────────────────── 만기 예측 ─────────────────────────────
def expiry_dates(base: pd.Timestamp) -> list[tuple[str, str, pd.Timestamp]]:
    """base(마지막 종가일) 이후 다가오는 월요일 위클리, 목요일 위클리, 월물 만기일.
    만기일이 휴장일이면 직전 거래일로 당겨요."""
    def nxt_weekday(wd):
        d = base + pd.Timedelta(days=1)
        while True:
            e = prev_trading_day_or_same(d) if d.weekday() == wd else None
            if e is not None and e > base:
                return e
            d += pd.Timedelta(days=1)

    def monthly(y, m):  # 매월 둘째 목요일
        d = pd.Timestamp(y, m, 1)
        d += pd.Timedelta(days=(3 - d.weekday()) % 7 + 7)
        return prev_trading_day_or_same(d)

    mon = monthly(base.year, base.month)
    if mon <= base:
        y, m = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
        mon = monthly(y, m)
    return [("weekly_mon", "월요일 위클리 만기", nxt_weekday(0)),
            ("weekly_thu", "목요일 위클리 만기", nxt_weekday(3)),
            ("monthly", "이번 달 월물 만기", mon)]


def ewma_vol(close: pd.Series, lam=0.94) -> pd.Series:
    r = np.log(close).diff()
    return np.sqrt((r ** 2).ewm(alpha=1 - lam, adjust=False).mean())


def expiry_ranges(kr: pd.DataFrame, lv) -> list[dict]:
    """만기일 종가가 80% 확률로 들어올 범위.
    지금은 '최근 변동성(EWMA) × 과거에 실제로 나타난 움직임의 분포'로 계산해요.
    TODO(KRX): 해당 만기 옵션의 내재변동성(시장이 예상하는 변동폭)으로 바꾸면 더 정확해져요."""
    close = kr.Close
    vol = ewma_vol(close)
    base = kr.index[-1]
    out = []
    for key, label, exp in expiry_dates(base):
        n = trading_days_between(base, exp)
        fwd = np.log(close.shift(-n) / close)
        z = (fwd / (vol * np.sqrt(n))).dropna()
        z = z[z.index > z.index[0] + pd.Timedelta(days=60)]  # 변동성 추정이 안정된 뒤부터
        # 과거 상승장·하락장의 '추세'가 섞이지 않도록 위아래를 같은 폭으로 잡아요 (현재가 중심)
        calib, test = z.iloc[:-STAT_DAYS].abs(), z.iloc[-STAT_DAYS:].abs()
        k_level = RANGE_Q[1] - RANGE_Q[0]
        cover = float((test <= calib.quantile(k_level)).mean()) * 100  # 최근 1년 실제 적중률
        k = float(z.abs().quantile(k_level))                          # 발표용은 전체 기간으로
        s = float(vol.iloc[-1] * np.sqrt(n))
        c = float(close.iloc[-1])
        lo, hi = c * np.exp(-k * s), c * np.exp(k * s)
        out.append(dict(key=key, label=label, date=exp.strftime("%Y-%m-%d"), days=n,
                        low=lv(lo), high=lv(hi), low_pct=pct(lo / c - 1), high_pct=pct(hi / c - 1),
                        cover=round(cover, 1), method="최근 변동성 기반 (옵션 데이터 연결 예정)"))
    return out


# ───────────────────────────── 종목별 실행 ─────────────────────────────
def run_asset(a, daily, intra, now, log, mode, bars5):
    kr = clean_korean(daily[a["ticker"]])
    df, f_morning, f_evening, next_day = build_frame(kr, a["drivers"], daily)
    need = TARGETS + ["base_hi", "base_lo"]
    valid_m = df.iloc[:-1].dropna(subset=f_morning + need)
    valid_e = df.iloc[:-1].dropna(subset=f_evening + need)
    wf_m, wf_e = walk_forward(valid_m, f_morning), walk_forward(valid_e, f_evening)
    pred_m = predict_rows(valid_m, df.iloc[[-1]], f_morning).iloc[0]
    pred_e = predict_rows(valid_e, df.iloc[[-1]], f_evening).iloc[0]
    prev_close = float(kr.Close.iloc[-1])

    # 국내 마지막 거래일 이후 미국 정규장이 끝났는지. (선물은 한국 낮에도 거래돼서 기준으로 못 씀 → EWY로 판단)
    main_last = daily["EWY"].index[-1]
    ready = bool(main_last >= kr.index[-1])
    pred, kind = (pred_m, "morning") if ready else (pred_e, "evening")

    scale, note = 1.0, None
    if a.get("level_ticker") and a["level_ticker"] in daily:
        lvs, etf = daily[a["level_ticker"]].Close, daily[a["ticker"]].Close
        common = lvs.index.intersection(etf.index)
        if len(common):
            scale = float(lvs.loc[common[-1]] / etf.loc[common[-1]])
            note = "KODEX 200 ETF 데이터로 학습하고 코스피200 지수 단위로 환산한 값이에요."

    def lv(price):
        v = price * scale
        return tick_round(v) if a["unit"] == "원" else round(v, a["decimals"])

    def level(r):
        return lv(prev_close * (1 + float(r)))

    drivers = []
    if ready:
        for t in a["drivers"] + COMMON:
            col = "vix_chg" if t == "^VIX" else f"r_{t}"
            if col in df.columns and pd.notna(df[col].iloc[-1]):
                v = df[col].iloc[-1]
                drivers.append(dict(name=LABELS.get(t, t), ret=pct(np.expm1(v) if t == "^VIX" else v)))

    # ── 기록(log) 갱신 ──
    stamp = now.strftime("%Y-%m-%d %H:%M")
    tgt = next_day.strftime("%Y-%m-%d")
    today = pd.Timestamp(now.date())
    today_key = today.strftime("%Y-%m-%d")
    rec = log.setdefault(tgt, {}).setdefault(a["id"], {})
    snap = dict(at=stamp, prev_close=lv(prev_close), open=level(pred.p_gap), high=level(pred.p_hi), low=level(pred.p_lo),
                pct=dict(open=pct(pred.p_gap), high=pct(pred.p_hi), low=pct(pred.p_lo)))
    if kind == "morning" and ("morning" not in rec or now.time() < dt.time(9, 0) or mode == "morning"):
        rec["morning"] = snap
    if kind == "evening" and ("evening" not in rec or mode == "evening"):
        rec["evening"] = snap
    if not rec:
        log[tgt].pop(a["id"])

    # 시가 반영 (9:30): 오늘 실제 시가를 알면 범위를 다시 계산
    f_open = f_morning + ["gap"]
    valid_o = valid_m.dropna(subset=["Open"])
    hist_wf, open_stats = open_eval(valid_o, f_open, wf_m[["p_hi", "p_lo"]])
    # 차트용: 실제 캔들(시작가·고가·저가·종가)과 9:30 예상 상방·하방을 가격(코스피200은 포인트)으로
    history = [dict(date=d.strftime("%m/%d"), day=d.strftime("%Y-%m-%d"), o=lv(r.Open), h=lv(r.High), l=lv(r.Low), c=lv(r.Close),
                    pc=lv(r.prev_close), ph=lv(r.prev_close * (1 + r.p_hi)), pl=lv(r.prev_close * (1 + r.p_lo)),
                    bh=lv(r.prev_close * (1 + r.p_hi_band)), bl=lv(r.prev_close * (1 + r.p_lo_band)))
               for d, r in hist_wf.iterrows()]
    write_intraday(a["id"], bars5.get(a["ticker"], pd.DataFrame()), history, lv, now.strftime("%Y-%m-%d"))
    raw = daily[a["ticker"]]
    o = float(raw.loc[today, "Open"]) if today in raw.index else 0.0
    if not o > 0 and a["ticker"] in intra:
        # 장중에는 야후 일봉 시작가가 0으로 나와요 → 1시간봉 첫 봉의 시작가 사용 (과거 61일×5종목 100% 일치 확인)
        h = intra[a["ticker"]]
        h = h[h.index.date == today.date()]  # intra는 한국시간 기준
        o = float(h.Open.iloc[0]) if len(h) and h.Open.iloc[0] > 0 else 0.0
    in_open_window = dt.time(9, 25) <= now.time() < dt.time(15, 30)
    open_done = "open" in log.get(today_key, {}).get(a["id"], {})  # 예비 실행이 이미 발표한 값을 덮어쓰지 않게
    if (is_trading_day(now.date()) and today == next_day and o > 0
            and ((in_open_window and not open_done) or mode == "open")):
        row = df.iloc[[-1]].copy()
        row["gap"] = o / prev_close - 1
        po = open_predict(valid_o, row, f_open).iloc[0]
        log.setdefault(today_key, {}).setdefault(a["id"], {})["open"] = dict(
            at=stamp, prev_close=lv(prev_close), open=lv(o), high=level(po.p_hi), low=level(po.p_lo),
            band_high=level(po.p_hi_band), band_low=level(po.p_lo_band), drivers=drivers)

    # 점심 보정
    midday, mid_stats = None, None
    if a["ticker"] in intra:
        mt = log.get(today_key, {}).get(a["id"], {}).get("morning")
        morning_today = None
        if mt:
            morning_today = dict(p_gap=mt["pct"]["open"] / 100, p_hi=mt["pct"]["high"] / 100, p_lo=mt["pct"]["low"] / 100)
        midday, mid_stats = run_midday(df, kr, intra[a["ticker"]], wf_m[["p_gap", "p_hi", "p_lo"]], morning_today, today)
    in_mid_window = dt.time(MID_CUTOFF, 0) <= now.time() < dt.time(15, 30)
    mid_done = "midday" in log.get(today_key, {}).get(a["id"], {})
    if midday and is_trading_day(now.date()) and ((in_mid_window and not mid_done) or mode == "midday"):
        pc = midday["prev_close"]
        log.setdefault(today_key, {}).setdefault(a["id"], {})["midday"] = dict(
            at=stamp, m_open=lv(midday["m_open"]), m_high=lv(midday["m_high"]), m_low=lv(midday["m_low"]),
            m_last=lv(midday["m_last"]), band_high=lv(pc * (1 + midday["band_hi"])),
            band_low=lv(pc * (1 + midday["band_lo"])), close=lv(pc * (1 + midday["p_cl"])),
            prob_up=midday["prob_up"], prob_dn=midday["prob_dn"], s_open=pct(midday["s_open"]))

    for d, r in kr.tail(10).iterrows():  # 장이 끝난 날의 실제 결과 채우기
        day = d.strftime("%Y-%m-%d")
        if day in log and a["id"] in log[day]:
            log[day][a["id"]]["actual"] = dict(open=lv(r.Open), high=lv(r.High), low=lv(r.Low), close=lv(r.Close))

    # ── 실전 기록: 아침에 발표한 예측 vs 실제 ──
    live = []
    for day in sorted(log, reverse=True):
        x = log[day].get(a["id"], {})
        if "open" in x and "actual" in x:  # 9:30에 실제로 발표한 상방·하방 vs 실제
            live.append(dict(date=day, **{f"o_{k}": x["open"][k] for k in ("open", "high", "low", "band_high", "band_low")},
                             **{f"a_{k}": x["actual"][k] for k in ("open", "high", "low", "close")}))
        if len(live) >= 20:
            break

    return dict(
        id=a["id"], name=a["name"], unit=a["unit"], decimals=a["decimals"], note=note,
        target_date=tgt, prev_date=kr.index[-1].strftime("%Y-%m-%d"),
        us_date=main_last.strftime("%Y-%m-%d"), ready=ready, kind=kind, prev_close=lv(prev_close),
        pred=dict(open=level(pred.p_gap), high=level(pred.p_hi), low=level(pred.p_lo)),
        pred_pct=dict(open=pct(pred.p_gap), high=pct(pred.p_hi), low=pct(pred.p_lo), close=pct(pred.p_cl)),
        drivers=drivers, stats=day_stats(wf_m), evening_stats=day_stats(wf_e), history=history,
        today=dict(date=today_key, **log[today_key][a["id"]]) if a["id"] in log.get(today_key, {}) else None,
        mid_stats=mid_stats, open_stats=open_stats, live=live, expiry=expiry_ranges(kr, lv),
    )


# ───────────────────────────── 참고지표 ─────────────────────────────
REF_TICKERS = {"^TNX": ("미국채 10년물 금리", "%", 2), "CL=F": ("WTI 유가", "달러", 2), "BZ=F": ("브렌트 유가", "달러", 2)}
FG_LABELS = [(25, "극단적 공포"), (45, "공포"), (55, "중립"), (75, "탐욕"), (101, "극단적 탐욕")]


def fg_label(x):
    return next(lab for lim, lab in FG_LABELS if x < lim)


def pct_rank(s: pd.Series, window=250) -> pd.Series:
    """지난 1년 중 지금 값이 몇 % 위치인지 (0~100)."""
    return s.rolling(window, min_periods=120).apply(lambda w: (w[:-1] < w[-1]).mean() * 100, raw=True)


def kospi_fear_greed(daily):
    """사팔사팔 코스피 공포탐욕지수 (0=극단적 공포, 100=극단적 탐욕). 5가지 항목의 1년 내 위치를 평균."""
    k = daily["^KS11"].Close.dropna()
    vix = daily["^VIX"].Close.reindex(k.index, method="ffill")
    krw = daily["KRW=X"].Close.reindex(k.index, method="ffill")
    r = np.log(k).diff()
    comps = {
        "주가 추세": (pct_rank(k / k.rolling(125).mean() - 1), "코스피가 125일 평균보다 얼마나 위에 있는지"),
        "변동성": (100 - pct_rank(r.rolling(20).std()), "최근 20일 변동성 (클수록 공포)"),
        "주가 위치": (((k - k.rolling(250).min()) / (k.rolling(250).max() - k.rolling(250).min())) * 100,
                  "1년 최저가~최고가 사이 위치"),
        "원화 흐름": (100 - pct_rank(krw / krw.shift(20) - 1), "최근 20일 원/달러 (원화 약세일수록 공포)"),
        "글로벌 위험선호": (100 - pct_rank(vix), "미국 VIX 수준 (높을수록 공포)"),
    }
    df = pd.DataFrame({n: s for n, (s, _) in comps.items()}).dropna()
    score = df.mean(axis=1)
    last = df.iloc[-1]
    return dict(
        score=round(float(score.iloc[-1]), 1), label=fg_label(score.iloc[-1]), date=score.index[-1].strftime("%Y-%m-%d"),
        prev=round(float(score.iloc[-2]), 1), week=round(float(score.iloc[-6]), 1), month=round(float(score.iloc[-21]), 1),
        components=[dict(name=n, score=round(float(last[n]), 1), desc=comps[n][1]) for n in df.columns],
        history=[[d.strftime("%m/%d"), round(float(v), 1)] for d, v in score.tail(120).items()],
        kospi=[[d.strftime("%m/%d"), round(float(v), 2)] for d, v in k.reindex(score.tail(120).index).items()],
    )


# ───────────────────────────── 이벤트 달력 ─────────────────────────────
# 공식 일정 (2026-11-22 확인). 매년 새 일정이 나오면 여기에 추가해야 해요.
#  - FOMC: 연준 federalreserve.gov (결정은 둘째 날 오후 2시 ET → 한국시간 다음 날 새벽)
#  - CPI : 미국 노동통계국 bls.gov (발표 08:30 ET → 한국시간 같은 날 밤)
#  - 금통위: 한국은행 (2026-10-29 의결한 2027년 일정 + 2026년 마지막 회의)
FOMC_DECISION_US = ["2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
                    "2027-07-28", "2027-09-15", "2027-11-17"]  # 2027-10-26~27 회의는 금리 결정 없음
CPI_RELEASE_US = ["2026-12-10", "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12",
                  "2027-06-10", "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10"]
BOK_DECISION = ["2026-11-26", "2027-01-14", "2027-02-25", "2027-04-09", "2027-05-27", "2027-07-15",
                "2027-08-26", "2027-10-15", "2027-11-25"]


def us_dst(d: pd.Timestamp) -> bool:
    """미국 서머타임 여부 (3월 둘째 일요일 ~ 11월 첫째 일요일)."""
    y = d.year
    start = pd.Timestamp(y, 3, 1) + pd.Timedelta(days=(6 - pd.Timestamp(y, 3, 1).weekday()) % 7 + 7)
    end = pd.Timestamp(y, 11, 1) + pd.Timedelta(days=(6 - pd.Timestamp(y, 11, 1).weekday()) % 7)
    return start <= d < end


def build_events(start="2026-11-01", end="2027-12-31"):
    ev = []
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    # 휴장일
    for h in sorted(KR_HOLIDAYS):
        d = pd.Timestamp(h)
        if s <= d <= e:
            ev.append(dict(date=h, type="holiday", title="휴장", note="한국거래소 휴장일"))
    # 옵션·선물 만기 (만기일이 휴장이면 직전 거래일)
    monthly = set()
    for y in range(s.year, e.year + 1):
        for m in range(1, 13):
            d = pd.Timestamp(y, m, 1)
            d += pd.Timedelta(days=(3 - d.weekday()) % 7 + 7)  # 둘째 목요일
            d = prev_trading_day_or_same(d)
            if s <= d <= e:
                monthly.add(d)
                quad = m in (3, 6, 9, 12)
                ev.append(dict(date=d.strftime("%Y-%m-%d"), type="quad" if quad else "expiry_m",
                               title="선물·옵션 동시만기" if quad else "월물 옵션 만기",
                               note="코스피200 선물·옵션 동시 만기 (쿼드러플 위칭데이)" if quad else "코스피200 월물 옵션 만기"))
    d = s
    while d <= e:
        if d.weekday() in (0, 3):
            x = prev_trading_day_or_same(d)
            if x not in monthly and x >= s:
                ev.append(dict(date=x.strftime("%Y-%m-%d"), type="expiry_w",
                               title=f"위클리 만기({'월' if d.weekday() == 0 else '목'})",
                               note=f"코스피200 {'월요일' if d.weekday() == 0 else '목요일'} 위클리 옵션 만기"))
        d += pd.Timedelta(days=1)
    # 미국·한국 일정 (한국시간 기준 날짜)
    for u in FOMC_DECISION_US:
        d = pd.Timestamp(u)
        k = d + pd.Timedelta(days=1)
        t = "03:00" if us_dst(d) else "04:00"
        ev.append(dict(date=k.strftime("%Y-%m-%d"), type="fomc", title="FOMC 금리 결정",
                       note=f"미국 연준 금리 결정 · 한국시간 {t} 발표"))
    for u in CPI_RELEASE_US:
        d = pd.Timestamp(u)
        t = "21:30" if us_dst(d) else "22:30"
        ev.append(dict(date=u, type="cpi", title="미국 CPI", note=f"미국 소비자물가지수 발표 · 한국시간 {t}"))
    for b in BOK_DECISION:
        ev.append(dict(date=b, type="bok", title="금통위 금리 결정", note="한국은행 기준금리 결정 · 오전 발표"))
    ev = [x for x in ev if s.strftime("%Y-%m-%d") <= x["date"] <= e.strftime("%Y-%m-%d")]
    return sorted(ev, key=lambda x: (x["date"], x["type"]))


def reference(daily):
    items = []
    for t, (name, unit, dec) in REF_TICKERS.items():
        if t not in daily:
            continue
        c = daily[t].Close.dropna()
        items.append(dict(name=name, unit=unit, value=round(float(c.iloc[-1]), dec), date=c.index[-1].strftime("%Y-%m-%d"),
                          chg=round(float(c.iloc[-1] - c.iloc[-2]), dec), chg_pct=pct(c.iloc[-1] / c.iloc[-2] - 1),
                          chg_20=pct(c.iloc[-1] / c.iloc[-21] - 1),
                          history=[[d.strftime("%m/%d"), round(float(v), dec)] for d, v in c.tail(120).items()]))
    # TODO(KRX): VKOSPI (코스피 변동성지수) — KRX Open API 연결 후 추가
    return dict(items=items, vkospi=None, fear_greed=kospi_fear_greed(daily), events=build_events())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["morning", "open", "midday", "evening"], help="시간과 상관없이 강제로 실행할 단계")
    args = ap.parse_args()
    now = now_kst()

    tickers = sorted({a["ticker"] for a in ASSETS} | {a["level_ticker"] for a in ASSETS if a.get("level_ticker")}
                     | {t for a in ASSETS for t in a["drivers"]} | set(COMMON) | {"EWY", "^KS11"} | set(REF_TICKERS))
    print(f"[{now:%Y-%m-%d %H:%M}] 일별 시세 받는 중... ({len(tickers)}개)")
    daily = fetch_daily(tickers)
    print("1시간봉 받는 중...")
    intra = fetch_intraday([a["ticker"] for a in ASSETS])
    print("5분봉 받는 중... (매일 누적 저장)")
    bars5 = fetch_5m([a["ticker"] for a in ASSETS])
    missing = [t for t in tickers if t not in daily]
    if missing:
        print("받지 못한 데이터:", missing)

    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {}
    results = []
    for a in ASSETS:
        r = run_asset(a, daily, intra, now, log, args.mode, bars5)
        s, e, ms = r["stats"], r["evening_stats"], r["mid_stats"]
        print(f"\n[{r['name']}] {r['target_date']} ({'아침 확정' if r['kind'] == 'morning' else '저녁 예비'}) "
              f"예상 고가 {r['pred']['high']:,} / 저가 {r['pred']['low']:,}")
        print(f"  아침모델 오차 고{s['mae_high']}%·저{s['mae_low']}% 방향{s['dir_acc']}% | "
              f"저녁모델 오차 고{e['mae_high']}%·저{e['mae_low']}% 방향{e['dir_acc']}% | 단순 {s['base_mae_high']}·{s['base_mae_low']}")
        os_ = r["open_stats"]
        print(f"  시가반영 오차 고{os_['mae_high']}%·저{os_['mae_low']}% (아침예측 {os_['morning_mae_high']}·"
              f"{os_['morning_mae_low']}, 시가±보통폭 {os_['base_mae_high']}·{os_['base_mae_low']}) 최대범위적중 {os_['band_cover']}% "
              f"| 큰갭(≥{os_['big_gap']}%) {os_['big_days']}일 범위적중 {os_['big_cover']}%")
        if r["today"] and "open" in r["today"]:
            o = r["today"]["open"]
            print(f"    오늘 시가 {o['open']:,} → 예상 고가 {o['high']:,} / 저가 {o['low']:,} (최대 {o['band_low']:,}~{o['band_high']:,})")
        if ms:
            w = ms["without_morning"]
            print(f"  점심보정 돌파적중 {ms['up_acc']}% (아침정보X {w['up_acc']}%) 이탈적중 {ms['dn_acc']}% "
                  f"(X {w['dn_acc']}%) 범위적중 {ms['band_cover']}% (X {w['band_cover']}%) | "
                  f"종가오차 {ms['mae_close']}% (현재가 그대로 {ms['base_mae_close']}%)")
        for x in r["expiry"]:
            print(f"  {x['label']} {x['date']} ({x['days']}거래일): {x['low']:,} ~ {x['high']:,} "
                  f"({x['low_pct']:+.2f}% ~ {x['high_pct']:+.2f}%), 최근1년 적중 {x['cover']}%")
        results.append(r)

    LOG.write_text(json.dumps(dict(sorted(log.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    ref = reference(daily)
    fg = ref["fear_greed"]
    print(f"\n[참고지표] 코스피 공포탐욕지수 {fg['score']} ({fg['label']}) · "
          + " · ".join(f"{x['name']} {x['value']}{x['unit']}" for x in ref["items"]))
    OUT.write_text(json.dumps(dict(generated_at=now.strftime("%Y-%m-%d %H:%M"), assets=results, ref=ref),
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n저장 완료")


if __name__ == "__main__":
    main()
