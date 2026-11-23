"""한국투자증권 Open API (KIS Developers) 조회 도우미 — 시세·수급 '조회'만 해요 (주문 기능은 만들지 않음).

- 키: Windows 사용자 환경변수 KIS_APP_KEY / KIS_APP_SECRET (save_kis_key.ps1로 저장).
  GitHub에서 돌릴 때는 GitHub Secrets의 같은 이름을 써요.
- 접속 토큰은 하루 동안 유효하고 자주 새로 받으면 안 돼서 data_private/kis_token.json에 저장해 두고 다시 써요.
- 받은 원본은 data_private/ 아래에만 저장하고 GitHub에 올리지 않아요.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import requests

BASE = "https://openapi.koreainvestment.com:9443"
ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "data_private" / "kis_token.json"
MIN_INTERVAL = 0.25  # 초당 호출 제한 여유
_last = 0.0


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = winreg.QueryValueEx(k, name)[0]
        except OSError:
            v = None
    if not v:
        raise RuntimeError(f"{name}가 없어요.")
    return v


def token() -> str:
    if TOKEN_FILE.exists():
        t = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        if dt.datetime.fromisoformat(t["expires"]) > dt.datetime.now() + dt.timedelta(minutes=30):
            return t["access_token"]
    r = requests.post(f"{BASE}/oauth2/tokenP", timeout=30, json={
        "grant_type": "client_credentials", "appkey": _env("KIS_APP_KEY"), "appsecret": _env("KIS_APP_SECRET")})
    if r.status_code != 200:
        raise RuntimeError(f"토큰 발급 실패 {r.status_code}: {r.text[:200]}")
    d = r.json()
    exp = dt.datetime.now() + dt.timedelta(seconds=int(d.get("expires_in", 86400)))
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"access_token": d["access_token"], "expires": exp.isoformat()}), encoding="utf-8")
    return d["access_token"]


def get(path: str, tr_id: str, params: dict) -> dict:
    """예: get('/uapi/domestic-stock/v1/quotations/inquire-price', 'FHKST01010100', {...})"""
    global _last
    for attempt in range(4):  # 초당 호출 제한(EGW00201)이나 일시 오류면 잠깐 쉬었다가 다시
        wait = MIN_INTERVAL - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(f"{BASE}{path}", params=params, timeout=30, headers={
                "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token()}",
                "appkey": _env("KIS_APP_KEY"), "appsecret": _env("KIS_APP_SECRET"), "tr_id": tr_id, "custtype": "P"})
        except requests.RequestException as e:  # 연결 끊김·시간 초과 등
            _last = time.time()
            if attempt == 3:
                return {"rt_cd": "?", "msg1": str(e)[:200], "_status": 0}
            time.sleep(1 + attempt)
            continue
        _last = time.time()
        try:
            d = r.json()
        except ValueError:
            d = {"rt_cd": "?", "msg1": r.text[:200]}
        d["_status"] = r.status_code
        if d.get("rt_cd") == "0" or attempt == 3:
            return d
        if d.get("msg_cd") == "EGW00201" or r.status_code >= 500:
            time.sleep(1 + attempt)
            continue
        return d
    return d


# ───────────── 장중 스냅샷 (현재가·외국인기관 추정·프로그램 매매·호가) ─────────────
Q = "/uapi/domestic-stock/v1/quotations"
SNAP = [  # (저장 이름, 경로, tr_id, 파라미터 만드는 함수)
    ("price", f"{Q}/inquire-price", "FHKST01010100", lambda c: {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": c}),
    ("estimate", f"{Q}/investor-trend-estimate", "HHPTJ04160200", lambda c: {"MKSC_SHRN_ISCD": c}),
    ("program", f"{Q}/program-trade-by-stock", "FHPPG04650101", lambda c: {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": c}),
    ("orderbook", f"{Q}/inquire-asking-price-exp-ccn", "FHKST01010200", lambda c: {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": c}),
]


def snapshot(code: str) -> dict:
    """한 종목의 지금 상태를 전부 받아요. 실패한 항목은 빠져요."""
    out = {}
    for name, path, tr, params in SNAP:
        try:
            d = get(path, tr, params(code))
        except Exception as e:  # noqa: BLE001
            out[f"{name}_error"] = str(e)[:200]
            continue
        if d.get("rt_cd") == "0":
            out[name] = {k: v for k, v in d.items() if k.startswith("output")}
        else:
            out[f"{name}_error"] = f"{d.get('_status')} {d.get('msg1', '')}"[:200]
    return out


def minute_bars(code: str, market: str = "J", start: str = "0900", end: str = "1530") -> list[list]:
    """오늘 1분봉. market: J=KRX, NX=NXT. 반환: [[끝시각 'HHMM', 시, 고, 저, 종, 거래량], ...] 시간순.
    한 번에 30개씩 주므로 30분 간격으로 끝시각을 바꿔가며 받아요."""
    def hm(m):
        return f"{m // 60:02d}{m % 60:02d}"
    s_min = int(start[:2]) * 60 + int(start[2:])
    e_min = int(end[:2]) * 60 + int(end[2:])
    rows = {}
    t = e_min
    while t > s_min:
        d = get(f"{Q}/inquire-time-itemchartprice", "FHKST03010200", {
            "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": hm(t) + "00", "FID_PW_DATA_INCU_YN": "N"})
        if d.get("rt_cd") != "0":
            break
        for r in d.get("output2") or []:
            h = r.get("stck_cntg_hour", "")[:4]
            try:
                hmv = int(h[:2]) * 60 + int(h[2:])
                o, hi, lo, c = (float(r[k]) for k in ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr"))
            except (ValueError, KeyError):
                continue
            if s_min < hmv <= e_min and c > 0:
                rows[h] = [h, o, hi, lo, c, float(r.get("cntg_vol") or 0)]
        t -= 30
    return [rows[k] for k in sorted(rows)]


def minute_bars_day(code: str, day: str) -> list[list]:
    """과거 하루치 1분봉 (종목, KRX, 09:00~15:30). day='YYYYMMDD'. 한 번에 120개씩 4번.
    반환 형식은 minute_bars와 같아요. 15:30 봉은 종가 동시호가 체결(확정 종가)."""
    rows = {}
    for end in ("153000", "133000", "113000", "093000"):
        d = get(f"{Q}/inquire-time-dailychartprice", "FHKST03010230", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": end,
            "FID_INPUT_DATE_1": day, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": ""})
        if d.get("rt_cd") != "0":
            break
        for r in d.get("output2") or []:
            if r.get("stck_bsop_date") != day:
                continue
            h = r.get("stck_cntg_hour", "")[:4]
            try:
                o, hi, lo, c = (float(r[k]) for k in ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr"))
            except (ValueError, KeyError):
                continue
            if "0900" <= h <= "1530" and c > 0:
                rows[h] = [h, o, hi, lo, c, float(r.get("cntg_vol") or 0)]
    return [rows[k] for k in sorted(rows)]


def index_daily(code: str = "2001", start: str = "20191101") -> list[dict]:
    """지수 일봉 (2001 = 코스피200). 한 번에 50일씩 거꾸로 받아요. 반환: [{date, open, high, low, close, volume}] 시간순."""
    end = dt.date.today()
    out = {}
    while True:
        d = get(f"{Q}/inquire-daily-indexchartprice", "FHKUP03500100", {
            "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"), "FID_PERIOD_DIV_CODE": "D"})
        rows = d.get("output2") or []
        if d.get("rt_cd") != "0" or not rows:
            break
        oldest = None
        for r in rows:
            try:
                day = r["stck_bsop_date"]
                v = dict(date=day, open=float(r["bstp_nmix_oprc"]), high=float(r["bstp_nmix_hgpr"]),
                         low=float(r["bstp_nmix_lwpr"]), close=float(r["bstp_nmix_prpr"]), volume=float(r.get("acml_vol") or 0))
            except (KeyError, ValueError):
                continue
            if v["close"] > 0:
                out[day] = v
                oldest = day if oldest is None or day < oldest else oldest
        if oldest is None or oldest <= start:
            break
        end = dt.date(int(oldest[:4]), int(oldest[4:6]), int(oldest[6:])) - dt.timedelta(days=1)
    return [out[k] for k in sorted(out)]


def index_price(code: str = "2001") -> dict:
    d = get(f"{Q}/inquire-index-price", "FHPUP02100000", {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code})
    return {"price": {"output": d.get("output")}} if d.get("rt_cd") == "0" else {"price_error": d.get("msg1", "")}


def index_5m_bars(code: str = "2001") -> list[list]:
    """오늘 지수 5분봉 [[끝시각 'HHMM', 시, 고, 저, 종, 0], ...] 시간순 (09:00 = 시작 지수)."""
    d = get(f"{Q}/inquire-time-indexchartprice", "FHKUP03500200", {
        "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": "300",
        "FID_PW_DATA_INCU_YN": "Y", "FID_ETC_CLS_CODE": "0"})
    today = dt.date.today().strftime("%Y%m%d")
    rows = {}
    for r in d.get("output2") or []:
        h = r.get("stck_cntg_hour", "")
        if r.get("stck_bsop_date") != today or not h[:4].isdigit() or h.startswith("88"):
            continue
        try:
            o, hi, lo, c = (float(r[k]) for k in ("bstp_nmix_oprc", "bstp_nmix_hgpr", "bstp_nmix_lwpr", "bstp_nmix_prpr"))
        except (KeyError, ValueError):
            continue
        if c > 0 and "0900" <= h[:4] <= "1530":
            rows[h[:4]] = [h[:4], o, hi, lo, c, 0.0]
    return [rows[k] for k in sorted(rows)]


def today_open(snap: dict) -> float:
    """스냅샷에서 오늘 시작가 (없으면 0)."""
    o = (snap.get("price") or {}).get("output") or {}
    for k in ("stck_oprc", "bstp_nmix_oprc"):  # 종목 / 지수
        try:
            v = float(o[k])
            if v > 0:
                return v
        except (KeyError, TypeError, ValueError):
            pass
    return 0.0
