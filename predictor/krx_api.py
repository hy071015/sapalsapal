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


VK_PATH = "idx/drvprod_dd_trd"
VK_START = "2020-07-01"


def is_vkospi(r) -> bool:
    return "변동성지수" in r.get("IDX_NM", "") and "200" in r.get("IDX_NM", "")


def is_cached(path: str, bas_dd: str) -> bool:
    return (CACHE / path.replace("/", "_") / f"{bas_dd}.json").exists()


def vkospi(holidays=(), start: str = VK_START, max_fetch: int | None = None):
    """코스피200 변동성지수(VKOSPI) 일별 종가. 저장 안 된 날짜만 KRX에서 받아요 (처음엔 오래 걸림).
    오늘 자료는 내일 아침에 나오므로 어제까지만. 반환: {날짜: 종가} (pandas 없이)
    max_fetch: 새로 받을 최대 날짜 수 (매일 실행에서는 최근 며칠만 받게 제한)"""
    out, fetched = {}, 0
    d, end = dt.date.fromisoformat(start), dt.date.today() - dt.timedelta(days=1)
    while d <= end:
        s = d.strftime("%Y%m%d")
        if d.weekday() < 5 and d.isoformat() not in holidays:
            if not is_cached(VK_PATH, s):
                if max_fetch is not None and fetched >= max_fetch:
                    d += dt.timedelta(days=1)
                    continue
                fetched += 1
            for r in get(VK_PATH, s, keep=is_vkospi):
                if is_vkospi(r):
                    try:
                        out[d] = float(str(r["CLSPRC_IDX"]).replace(",", ""))
                    except ValueError:
                        pass
        d += dt.timedelta(days=1)
    return out


if __name__ == "__main__":  # GitHub에서 미리 채워두기용: python predictor/krx_api.py
    v = vkospi()
    print(f"VKOSPI {len(v)}일", max(v) if v else "")
