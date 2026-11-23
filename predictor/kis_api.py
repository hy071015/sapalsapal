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
    wait = MIN_INTERVAL - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    r = requests.get(f"{BASE}{path}", params=params, timeout=30, headers={
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token()}",
        "appkey": _env("KIS_APP_KEY"), "appsecret": _env("KIS_APP_SECRET"), "tr_id": tr_id, "custtype": "P"})
    _last = time.time()
    try:
        d = r.json()
    except ValueError:
        d = {"rt_cd": "?", "msg1": r.text[:200]}
    d["_status"] = r.status_code
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


def today_open(snap: dict) -> float:
    """스냅샷에서 오늘 시작가 (없으면 0)."""
    try:
        return float(snap["price"]["output"]["stck_oprc"])
    except (KeyError, TypeError, ValueError):
        return 0.0
