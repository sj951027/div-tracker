# -*- coding: utf-8 -*-
"""
fetch_rank.py — 배당 적립 도우미: "오늘 사기 좋은 배당 종목" 랭킹 (div-tracker)
================================================================================
아이디어: 절대 배당률 순위는 배당 트랩(폭락주) 랭킹이 되기 쉽다. 대신
  ① 일드 밴드: 현재 TTM 배당률이 "그 종목 자신의 5년 분포"에서 상위 몇 %인가(=역사적으로 싼가)
  ② 단기 낙폭(5일): 적립 타이밍 보조
  종합순위 = ①순위 + ②순위 동일가중 합(시장별 분리). 탐색된 가중치 없음.
대상: watchlist.csv 고정 관심종목(검증된 배당 이력 종목·ETF만 — 트랩 가드).
출력: docs/data.json (index.html 이 그림). 매수신호 아님 — 적립 참고용.
실행: python fetch_rank.py            (전 종목 yfinance 조회, ~1분)
      python fetch_rank.py --selftest (네트워크 없이 산식 검증)
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def yield_series(close: pd.Series, divs: pd.Series) -> pd.Series:
    """일별 TTM 배당률 시계열 = (과거 365일 배당합) / 종가."""
    if divs.empty or close.empty:
        return pd.Series(dtype=float)
    divs = divs.copy()
    divs.index = pd.to_datetime(divs.index).tz_localize(None)
    close = close.copy()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    ttm = pd.Series(0.0, index=close.index)
    for d, v in divs.items():
        mask = (close.index >= d) & (close.index < d + pd.Timedelta(days=365))
        ttm[mask] += float(v)
    return (ttm / close).replace([np.inf, -np.inf], np.nan)


def total_return_cagr(close: pd.Series, divs: pd.Series) -> float:
    """배당 재투자 가정 총수익 연평균(%). 각 배당을 지급일 종가로 재투자한 근사.
    이게 음수면 '배당을 받아도 가격 하락이 더 큰' 종목 — 적립 대상 부적합 신호."""
    c = close.copy()
    c.index = pd.to_datetime(c.index).tz_localize(None)
    d = divs.copy()
    if not d.empty:
        d.index = pd.to_datetime(d.index).tz_localize(None)
    factor = float(c.iloc[-1] / c.iloc[0])
    for dt, v in d.items():
        px = c[c.index <= dt]
        if len(px) and float(px.iloc[-1]) > 0:
            factor *= (1 + float(v) / float(px.iloc[-1]))
    years = max((c.index[-1] - c.index[0]).days / 365.25, 0.5)
    return (factor ** (1 / years) - 1) * 100


def analyze(close: pd.Series, divs: pd.Series) -> dict | None:
    ys = yield_series(close, divs).dropna()
    if len(ys) < 250 or len(close) < 10:
        return None
    cur_y = float(ys.iloc[-1])
    pctile = float((ys < cur_y).mean()) * 100          # 높을수록 '자기 역사 대비 싼' 상태
    r1 = float(close.iloc[-1] / close.iloc[-2] - 1) * 100
    r5 = float(close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) >= 6 else np.nan
    tr = total_return_cagr(close, divs)
    nodata = bool(cur_y <= 0)   # 야후가 KR ETF 분배금을 누락하는 경우 잦음(실측) — 오해 방지용 분리 표시
    # 지급주기: 최근 365일 지급 횟수로 판별 / 배당 1y 변화: TTM 배당금 now vs 1년 전
    d2 = divs.copy()
    if not d2.empty:
        d2.index = pd.to_datetime(d2.index).tz_localize(None)
    end = pd.to_datetime(close.index[-1]).tz_localize(None) if getattr(close.index, "tz", None) is not None \
        else pd.to_datetime(close.index[-1])
    n1y = int(((d2.index > end - pd.Timedelta(days=365)) & (d2.index <= end)).sum()) if not d2.empty else 0
    freq = "월" if n1y >= 11 else ("분기" if n1y >= 3 else ("반기" if n1y == 2 else ("연" if n1y == 1 else "—")))
    ttm_now = float(d2[(d2.index > end - pd.Timedelta(days=365)) & (d2.index <= end)].sum()) if not d2.empty else 0.0
    ttm_prv = float(d2[(d2.index > end - pd.Timedelta(days=730)) & (d2.index <= end - pd.Timedelta(days=365))].sum()) if not d2.empty else 0.0
    div_chg = round((ttm_now / ttm_prv - 1) * 100, 1) if ttm_prv > 0 else None
    return dict(cur_yield=round(cur_y * 100, 2), yield_pctile=round(pctile, 1),
                r1d=round(r1, 2), r5d=round(r5, 2),
                yr_min=round(float(ys.tail(1260).min()) * 100, 2),
                yr_max=round(float(ys.tail(1260).max()) * 100, 2),
                tr_cagr=round(tr, 1),
                freq=freq, div_chg=div_chg,
                nodata=nodata,
                slump=bool((tr < 0) and not nodata))   # 🚩 배당 포함 총수익 음수(데이터 있는 경우만)


def rank_market(rows: list[dict]) -> None:
    """시장별: 종합순위 = rank(밴드백분위 desc) + rank(5일수익 asc) 동일가중.
    단 slump(5년 총수익 연평균 < 0 — 배당보다 가격이 더 빠짐)는 순위 최하단으로 강등."""
    df = pd.DataFrame(rows)
    for mkt, g in df.groupby("market"):
        r = g["yield_pctile"].rank(ascending=False) + g["r5d"].rank(ascending=True)
        r = r + g.get("slump", pd.Series(False, index=g.index)).fillna(False).astype(int) * 2000
        r = r + g.get("nodata", pd.Series(False, index=g.index)).fillna(False).astype(int) * 1000
        order = r.rank(method="first")
        for i, rank in order.items():
            rows[i]["rank"] = int(rank)


def band_events(old_rows: list[dict], new_rows: list[dict], market: str) -> list[str]:
    """직전 실행 대비 이벤트: '싼편'(밴드>=80) 신규 진입, 🚩 신규 발생. 첫 실행(과거 없음)은 침묵."""
    old = {r["ticker"]: r for r in old_rows if r.get("market") == market}
    if not old:
        return []
    ev = []
    for r in new_rows:
        if r.get("market") != market:
            continue
        o = old.get(r["ticker"])
        if not o:
            continue
        if r.get("yield_pctile", 0) >= 80 and o.get("yield_pctile", 100) < 80 and not r.get("nodata"):
            ev.append(f"🟢 싼편 진입 — {r['name']} (배당률 {r['cur_yield']}%, 밴드 {r['yield_pctile']:.0f})")
        if r.get("slump") and not o.get("slump"):
            ev.append(f"🚩 총수익 음수 전환 — {r['name']} (5y {r['tr_cagr']}%/년) · 적립 재검토")
    return ev


def notify_telegram(lines: list[str]) -> None:
    """이벤트 있을 때만 발송. 토큰 없거나 실패해도 조용히 통과(비치명)."""
    import os
    import urllib.parse
    import urllib.request
    token = os.environ.get("TG_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT", "").strip()
    if not token or not chat or not lines:
        return
    msg = "💰 배당 밴드 알림\n" + "\n".join(lines) + "\n→ sj951027.github.io/div-tracker"
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
        print(f"📨 텔레그램 알림 {len(lines)}건 발송")
    except Exception as e:
        print(f"⚠ 텔레그램 발송 실패(무시): {e}")


def selftest() -> None:
    """네트워크 없이 산식 검증(합성 데이터)."""
    idx = pd.date_range("2021-01-01", periods=1300, freq="D")
    close = pd.Series(np.linspace(100, 60, 1300), index=idx)          # 가격 하락 추세
    divs = pd.Series(1.0, index=pd.date_range("2021-03-01", periods=20, freq="91D"))
    out = analyze(close, divs)
    assert out is not None
    # 가격이 역사상 최저권 + 배당 유지 → 배당률은 역사상 최고권이어야 함
    assert out["yield_pctile"] > 95, out
    assert out["cur_yield"] > 6, out                                   # 4/60 ≈ 6.7%
    # 배당 중단 시나리오: 최근 365일 배당 0 → TTM 배당률 0 (컷은 TTM 창 이전으로)
    divs2 = divs[divs.index < close.index[-1] - pd.Timedelta(days=400)]
    out2 = analyze(close, divs2)
    assert out2["cur_yield"] == 0.0, out2
    # 총수익 가드: 가격 −40%인데 배당 4/60 수준 → 총수익 CAGR 음수 → slump 플래그
    assert out["slump"] is True and out["tr_cagr"] < 0, out
    # 상승 종목은 slump 아님
    up = analyze(pd.Series(np.linspace(60, 100, 1300), index=idx), divs)
    assert up["slump"] is False, up
    # nodata: 배당 0 종목은 slump 가 아니라 nodata 로 분리(야후 KR ETF 커버리지 구멍 대응)
    assert out2["nodata"] is True and out2["slump"] is False, out2
    # 랭킹: 백분위 높고 많이 빠진 놈이 1등, slump 는 최하단 강등
    rows = [dict(market="US", yield_pctile=99, r5d=-5, slump=False),
            dict(market="US", yield_pctile=50, r5d=+2, slump=False),
            dict(market="US", yield_pctile=100, r5d=-9, slump=True),
            dict(market="US", yield_pctile=80, r5d=-1, slump=False)]
    rank_market(rows)
    assert rows[0]["rank"] == 1 and rows[2]["rank"] == 4, rows   # slump 가 지표 최상위여도 꼴찌
    # 지급주기·배당변화: 91일 간격 배당 → 분기 / 최근 1년 = 그 전 1년 → 변화 0%
    assert out["freq"] == "분기", out["freq"]
    assert out["div_chg"] is not None and abs(out["div_chg"]) < 1, out["div_chg"]
    monthly = pd.Series(0.1, index=pd.date_range("2022-01-15", periods=50, freq="30D"))
    om = analyze(close, monthly)
    assert om["freq"] == "월", om["freq"]
    # 이벤트 감지: 79→85 진입만 잡고, 이미 85였던 것·첫 실행(old 없음)은 침묵
    oldr = [dict(ticker="A", market="US", yield_pctile=79, slump=False, name="A", cur_yield=3, tr_cagr=5),
            dict(ticker="B", market="US", yield_pctile=90, slump=False, name="B", cur_yield=4, tr_cagr=5)]
    newr = [dict(ticker="A", market="US", yield_pctile=85, slump=False, nodata=False, name="A", cur_yield=3.2, tr_cagr=5),
            dict(ticker="B", market="US", yield_pctile=91, slump=False, nodata=False, name="B", cur_yield=4, tr_cagr=5)]
    ev = band_events(oldr, newr, "US")
    assert len(ev) == 1 and "A" in ev[0], ev
    assert band_events([], newr, "US") == []
    print("✅ selftest 통과 (TTM 배당률·밴드 백분위·배당중단·총수익 가드·랭킹·이벤트 감지)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    # [v2] 시장 분리 실행: 한국장 마감 후(KST 16:10)엔 KR만, 미국장 마감 후(KST 아침)엔 US만
    #      갱신하고 나머지 시장 행은 기존 data.json 것을 보존한다. auto = UTC 시각으로 판별.
    ap.add_argument("--market", default="auto", choices=["KR", "US", "all", "auto"])
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    target = args.market
    if target == "auto":
        h = datetime.utcnow().hour
        target = "KR" if 4 <= h < 12 else "US"   # UTC 04~12시(KST 13~21시) 실행이면 KR, 그 외 US
    print(f"▶ 갱신 대상 시장: {target}")

    import yfinance as yf
    wl = pd.read_csv(HERE / "watchlist.csv")
    if target != "all":
        wl = wl[wl.market == target]
    import time
    rows, fails = [], []
    for _, w in wl.iterrows():
        try:
            time.sleep(1.5)                     # 야후 429(rate limit) 예방 — Actions IP는 자주 걸림(실측 이슈 다수)
            h = None
            for wait in (0, 30, 90):            # 걸리면 백오프 재시도
                if wait:
                    print(f"  … {w.ticker} 재시도({wait}s 대기)")
                    time.sleep(wait)
                try:
                    t = yf.Ticker(w.ticker)
                    h = t.history(period="5y", auto_adjust=False)
                    if not h.empty:
                        break
                except Exception as e:
                    if "429" not in str(e) and "Rate" not in str(e):
                        raise
            if h is None or h.empty:
                fails.append(w.ticker)
                continue
            out = analyze(h["Close"].dropna(), t.dividends)
            if out is None:
                fails.append(w.ticker)
                continue
            out.update(ticker=w.ticker, market=w.market, name=w["name"],
                       type=w.type, memo=w.memo)
            rows.append(out)
        except Exception as e:
            print(f"  ⚠ {w.ticker}: {e}")
            fails.append(w.ticker)
    if not rows:
        print("❌ 수집 실패 — 네트워크/야후 상태 확인")
        sys.exit(1)
    # 다른 시장 행은 기존 파일에서 보존(시장 분리 실행) + 직전 상태로 이벤트 감지
    out_path = HERE / "docs" / "data.json"
    updated, old_fails, prev_rows = {}, [], []
    if out_path.exists():
        try:
            prev_rows = json.loads(out_path.read_text(encoding="utf-8")).get("rows", [])
        except Exception:
            prev_rows = []
    if target != "all" and out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            rows += [r for r in old.get("rows", []) if r.get("market") != target]
            updated = old.get("updated", {})
            old_fails = [f for f in old.get("fails", []) if not any(
                f == r.get("ticker") for r in rows)]
        except Exception:
            pass
    rank_market(rows)
    rows.sort(key=lambda r: (r["market"], r["rank"]))
    now = datetime.now().isoformat(timespec="seconds")
    updated[target if target != "all" else "KR"] = now
    if target == "all":
        updated["US"] = now
    data = dict(status="ok", generated=now, updated=updated,
                fails=sorted(set(fails) | set(old_fails)), rows=rows)
    (HERE / "docs").mkdir(exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"💾 docs/data.json — {target} 갱신, 총 {len(rows)}종목 (실패 {len(fails)}: {fails})")
    # 이벤트 알림: 싼편(밴드 80) 신규 진입·🚩 전환만 — 평소엔 침묵(적립 도구 철학)
    mkts = ["KR", "US"] if target == "all" else [target]
    events = []
    for m in mkts:
        events += band_events(prev_rows, rows, m)
    notify_telegram(events)


if __name__ == "__main__":
    main()
