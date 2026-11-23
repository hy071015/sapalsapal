"""KRX Open API (공식) 호출 도우미.

- 인증키는 Windows 사용자 환경변수 KRX_API_KEY에 저장돼 있어요 (save_krx_key.ps1로 저장).
  GitHub에서 돌릴 때는 GitHub Secrets의 KRX_API_KEY를 써요.
- 약관: 비상업적 이용, 원본 데이터 제3자 제공 금지, 화면에 '한국거래소 통계정보' 출처 표시, 하루 10,000회 이하.
- 받은 원본은 data_private/ 아래에만 저장하고 GitHub에 올리지 않아요.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import requests

BASE = "https://data-dbg.krx.co.kr/svc/apis"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data_private" / "krx_api"
MIN_INTERVAL = 1.0  # 호출 사이 최소 1초 (서버 부담 줄이기)
_last = 0.0


def api_key() -> str:
    key = os.environ.get("KRX_API_KEY")
    if not key and os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            key = winreg.QueryValueEx(k, "KRX_API_KEY")[0]
    if not key:
        raise RuntimeError("KRX_API_KEY가 없어요.")
    return key


def get(path: str, bas_dd: str, use_cache: bool = True, keep=None) -> list[dict]:
    """예: get('drv/opt_bydd_trd', '20261120'). 한 번 받은 날짜는 저장해 두고 다시 호출하지 않아요.
    keep: 저장 전에 필요한 행만 남기는 함수 (옵션처럼 자료가 큰 경우)."""
    global _last
    f = CACHE / path.replace("/", "_") / f"{bas_dd}.json"
    if use_cache and f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    wait = MIN_INTERVAL - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    r = requests.get(f"{BASE}/{path}", params={"basDd": bas_dd}, headers={"AUTH_KEY": api_key()}, timeout=30)
    _last = time.time()
    r.raise_for_status()
    data = r.json()
    rows = data.get("OutBlock_1", data if isinstance(data, list) else [])
    if not rows and bas_dd >= (dt.date.today() - dt.timedelta(days=4)).strftime("%Y%m%d"):
        return rows  # 최근 날짜가 비어 있으면 아직 안 나온 것일 수 있어서 저장하지 않아요
    if keep:
        rows = [r for r in rows if keep(r)]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows
