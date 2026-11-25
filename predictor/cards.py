"""인스타그램용 카드 이미지 (1080×1080) 자동 생성.

- site/cards/morning.png : 오늘의 9:30 예측 (시작가·상방·하방)
- site/cards/result.png  : 예측 vs 실제 (장 마감 후)
build.py가 실행될 때마다 오늘 기록(log)으로 다시 그려요. 사이트 주소: sapalsapal.com/cards/result.png
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "cards"
S = 2  # 2배로 그린 뒤 줄여서 선을 부드럽게
W = H = 1080
BG, CARD, LINE = "#0D1117", "#161B22", "#2A313C"
TEXT, MUTED, UP, DOWN, OK = "#E6E9EE", "#9AA3AE", "#FF5A5F", "#5B9BFF", "#3FB950"
DAYS = "월화수목금토일"

FONT_CANDIDATES = {
    "bold": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", r"C:\Windows\Fonts\malgunbd.ttf"],
    "reg": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", r"C:\Windows\Fonts\malgun.ttf"],
}


def font(kind, size):
    for p in FONT_CANDIDATES[kind]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size * S, index=1 if p.endswith(".ttc") else 0)  # .ttc 1번 = 한국어(KR)
    raise RuntimeError("한글 글꼴이 없어요 (fonts-noto-cjk 설치 필요)")


def fmt(v, decimals):
    return f"{v:,.{decimals}f}"


class Canvas:
    def __init__(self):
        self.img = Image.new("RGB", (W * S, H * S), BG)
        self.d = ImageDraw.Draw(self.img)

    def text(self, xy, s, f, fill, anchor="la"):
        self.d.text((xy[0] * S, xy[1] * S), s, font=f, fill=fill, anchor=anchor)

    def rect(self, box, fill, r=0, outline=None):
        self.d.rounded_rectangle([v * S for v in box], radius=r * S, fill=fill, outline=outline, width=2 * S if outline else 0)

    def line(self, a, b, fill, w):
        self.d.line([a[0] * S, a[1] * S, b[0] * S, b[1] * S], fill=fill, width=int(w * S))
        for p in (a, b):
            r = w * S / 2
            self.d.ellipse([p[0] * S - r, p[1] * S - r, p[0] * S + r, p[1] * S + r], fill=fill)

    def logo(self, x, y, k=0.9):
        """사팔사팔 ㅅㅍ 로고 (favicon과 같은 모양)"""
        L = lambda a, b, c, w: self.line((x + a[0] * k, y + a[1] * k), (x + b[0] * k, y + b[1] * k), c, w * k)
        L((9, 62), (23, 17), UP, 6); L((23, 17), (33, 42), DOWN, 6)
        L((26, 41), (33, 45), DOWN, 5); L((33, 45), (36, 37), DOWN, 5)
        L((44, 22), (70, 22), UP, 6); L((44, 58), (70, 58), DOWN, 6)
        L((51, 22), (51, 58), MUTED, 4.5); L((63, 22), (63, 58), MUTED, 4.5)
        cx, cy, r = x + 57 * k, y + 40 * k, 4.5 * k
        self.d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], fill=BG, outline=TEXT, width=int(3 * k * S))

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.img.resize((W, H), Image.LANCZOS).save(path, optimize=True)


def header(c, title, day):
    c.logo(56, 52)
    c.text((140, 62), "사팔", font("bold", 40), TEXT)
    c.text((140 + c.d.textlength("사팔", font=font("bold", 40)) / S, 62), "사팔", font("bold", 40), UP)
    d = dt.date.fromisoformat(day)
    c.text((W - 56, 70), f"{d.month}월 {d.day}일 ({DAYS[d.weekday()]})", font("reg", 30), MUTED, "ra")
    c.text((56, 170), title, font("bold", 54), TEXT)


def footer(c):
    c.text((56, H - 70), "sapalsapal.com", font("bold", 28), TEXT)
    c.text((W - 56, H - 66), "통계 모델로 계산한 참고용 정보이며 투자 권유가 아닙니다", font("reg", 20), MUTED, "ra")


def morning_card(day, items, path):
    """items: [dict(name, decimals, open, high, low, prev_close)]"""
    c = Canvas()
    header(c, "오늘의 9:30 예측", day)
    c.text((56, 248), "시작가 기준, 오늘 고가(상방)와 저가(하방)는 어디쯤일까?", font("reg", 26), MUTED)
    cols = (56, 400, 640, 1024)
    y0 = 320
    c.text((cols[1], y0), "시작가", font("reg", 24), MUTED)
    c.text((cols[2] + 150, y0), "하방", font("reg", 24), DOWN, "ra")
    c.text((cols[3], y0), "상방", font("reg", 24), UP, "ra")
    for i, it in enumerate(items):
        y = y0 + 46 + i * 112
        c.rect((40, y - 8, W - 40, y + 92), CARD, 22)
        dec = it["decimals"]
        c.text((cols[0] + 20, y + 26), it["name"], font("bold", 34), TEXT)
        chg = (it["open"] / it["prev_close"] - 1) * 100
        c.text((cols[1], y + 12), fmt(it["open"], dec), font("bold", 32), TEXT)
        c.text((cols[1], y + 54), f"전일 대비 {chg:+.2f}%", font("reg", 20), UP if chg > 0 else DOWN if chg < 0 else MUTED)
        c.text((cols[2] + 150, y + 28), fmt(it["low"], dec), font("bold", 34), DOWN, "ra")
        c.text((cols[3] - 20, y + 28), fmt(it["high"], dec), font("bold", 34), UP, "ra")
    footer(c)
    c.save(path)


def result_card(day, items, path):
    """items: [dict(name, decimals, high, low, a_high, a_low, a_close, prev_close, band_high, band_low)]"""
    c = Canvas()
    header(c, "예측 vs 실제", day)
    c.text((56, 248), "9:30에 예측한 상방·하방과 실제 고가·저가 (KRX 정규장)", font("reg", 26), MUTED)
    y0 = 320
    c.text((320, y0), "고가: 예측 → 실제", font("reg", 24), UP)
    c.text((668, y0), "저가: 예측 → 실제", font("reg", 24), DOWN)
    for i, it in enumerate(items):
        y = y0 + 46 + i * 112
        c.rect((40, y - 8, W - 40, y + 92), CARD, 22)
        dec = it["decimals"]
        c.text((76, y + 12), it["name"], font("bold", 32), TEXT)
        cl = (it["a_close"] / it["prev_close"] - 1) * 100
        c.text((76, y + 56), f"종가 {fmt(it['a_close'], dec)} ({cl:+.2f}%)", font("reg", 20), UP if cl > 0 else DOWN if cl < 0 else MUTED)
        for x0, pred, act in ((320, it["high"], it["a_high"]), (668, it["low"], it["a_low"])):
            diff = (act / pred - 1) * 100
            near = abs(diff) <= 0.3
            c.text((x0, y + 12), f"{fmt(pred, dec)} → {fmt(act, dec)}", font("bold", 26), TEXT)
            c.text((x0, y + 52), f"차이 {diff:+.2f}%" + ("  · 거의 적중" if near else ""), font("bold" if near else "reg", 21),
                   OK if near else MUTED)
        inside = it["band_low"] <= it["a_low"] and it["a_high"] <= it["band_high"]
        cx, cy = W - 64, y + 42
        c.d.ellipse([(cx - 9) * S, (cy - 9) * S, (cx + 9) * S, (cy + 9) * S], fill=OK if inside else "#6E7681")
    c.text((W - 56, H - 112), "초록 점 = 하루 전체가 최대 범위 안", font("reg", 20), MUTED, "ra")
    lx = W - 56 - c.d.textlength("초록 점 = 하루 전체가 최대 범위 안", font=font("reg", 20)) / S - 18
    c.d.ellipse([(lx - 7) * S, (H - 99 - 7) * S, (lx + 7) * S, (H - 99 + 7) * S], fill=OK)
    footer(c)
    c.save(path)


def make(log, assets, day):
    """build.py에서 호출. 오늘 9:30 예측이 있으면 오전 카드, 실제 결과까지 있으면 결과 카드."""
    rec = log.get(day, {})
    m, r = [], []
    for a in assets:
        x = rec.get(a["id"], {})
        o, act = x.get("open"), x.get("actual")
        if not o:
            continue
        base = dict(name=a["name"], decimals=a["decimals"], open=o["open"], high=o["high"], low=o["low"],
                    prev_close=o["prev_close"], band_high=o["band_high"], band_low=o["band_low"])
        m.append(base)
        if act:
            r.append(dict(base, a_high=act["high"], a_low=act["low"], a_close=act["close"]))
    made = []
    if len(m) == len(assets):
        morning_card(day, m, OUT / "morning.png")
        made.append("오전")
    if len(r) == len(assets):
        result_card(day, r, OUT / "result.png")
        made.append("결과")
    return made


if __name__ == "__main__":  # 시험: python predictor/cards.py 2026-11-25
    import json
    import sys
    day = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    log = json.loads((ROOT / "site" / "log.json").read_text(encoding="utf-8-sig"))
    data = json.loads((ROOT / "site" / "data.json").read_text(encoding="utf-8-sig"))
    print(make(log, data["assets"], day))
