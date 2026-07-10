# -*- coding: utf-8 -*-
"""
자동 알림봇 (market_alert_bot.py)

켜두면 알아서 감시하고 텔레그램으로 쏩니다:
  1) 장중(평일 09:00~15:30): 감시 종목이 기준(%) 이상 움직이면 즉시 알림 (종목당 하루 1회)
  2) 수시: 감시 종목에 새 내부자/대량보유/자사주/공급계약 공시가 뜨면 즉시 알림 (중복 발송 없음)
  3) 매일 저녁(기본 18시, 평일): 종목별 데이터 다이제스트 자동 발송

실행:
    python market_alert_bot.py
    (market_research.py, config.txt 와 같은 폴더에서. 종료는 Ctrl+C)

사전 준비 (config.txt):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  ← 필수
    WATCHLIST=005930:삼성전자,066570:LG전자  ← 감시 종목
    CHECK_INTERVAL_MIN=30 / PRICE_ALERT_PCT=3.0 / DAILY_DIGEST_HOUR=18

주의:
- PC가 켜져 있고 이 창이 실행 중일 때만 동작합니다.
- 점검 주기를 10분 미만으로 줄이지 마세요 (API 차단 위험).
"""

import importlib
import json
import os
import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests

import market_research as mr

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE, "alert_state.json")

ALERT_KEYWORDS = [
    "임원ㆍ주요주주특정증권등소유상황보고서",
    "주식등의대량보유상황보고서",
    "자기주식",
    "최대주주",
    "단일판매ㆍ공급계약",
    "유상증자", "무상증자", "전환사채",
]

DISCLAIMER = "\n※ 데이터 기록 알림입니다. 매매 추천이 아니며, 투자 판단과 책임은 본인에게 있습니다."


# ── 봇 전용 설정 키 자가 추가 (엔진 버전과 무관하게 동작) ──
_BOT_CONFIG_BLOCKS = {
    "SUMMARY_INTERVAL_HOURS": (
        "\n# 장중 정기 시황 브리핑 주기(시간). 이 간격마다 감시종목 전체 등락 요약 발송 (0이면 끔)\n"
        "SUMMARY_INTERVAL_HOURS=2\n"),
    "NEWS_RSS_URL": (
        "\n# 뉴스 헤드라인 RSS 주소 (선택). 미국장/주말 브리핑에 제목+링크로 헤드라인 포함\n"
        "# 예시: 연합뉴스 경제 https://www.yna.co.kr/rss/economy.xml\n"
        "NEWS_RSS_URL=\n"),
}
_BOT_CONFIG_DEFAULTS = {"SUMMARY_INTERVAL_HOURS": "2", "NEWS_RSS_URL": ""}


def ensure_bot_config():
    """봇이 쓰는 설정 키가 config.txt에 없으면 직접 추가하고 기본값 적용"""
    for key, block in _BOT_CONFIG_BLOCKS.items():
        if key in mr._cfg:
            continue
        try:
            with open(mr.CONFIG_PATH, "a", encoding="utf-8") as f:
                f.write(block)
            print(f"[안내] config.txt에 {key} 항목을 추가했습니다. "
                  "메모장으로 열어 값을 확인/수정할 수 있습니다.")
        except OSError:
            pass
        mr._cfg[key] = _BOT_CONFIG_DEFAULTS[key]


# ── 설정/상태 ──────────────────────────────
def cfg_float(key, default):
    try:
        return float(mr._cfg.get(key, default))
    except (ValueError, TypeError):
        return default


def cfg_int(key, default):
    try:
        return int(float(mr._cfg.get(key, default)))
    except (ValueError, TypeError):
        return default


def build_top_watchlist(n):
    """코스피 시가총액 상위 n개로 감시목록 자동 생성"""
    if mr.krx is None or not mr._krx_ready():
        print("TOP 감시목록 생성 실패: pykrx 설치와 KRX 로그인(config.txt)이 필요합니다.")
        return []
    try:
        base = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        day = mr.krx.get_nearest_business_day_in_a_week(base)
        df = mr.krx.get_market_cap_by_ticker(day, market="KOSPI")
        col = "시가총액" if "시가총액" in df.columns else df.columns[0]
        top = df.sort_values(col, ascending=False).head(n)
        out = []
        for t in top.index:
            try:
                name = mr.krx.get_market_ticker_name(t)
            except Exception:
                name = str(t)
            out.append((str(t).zfill(6), str(name)))
        log(f"감시목록 자동 생성: 시가총액 상위 {len(out)}개 ({day} 기준)")
        return out
    except Exception as e:
        print(f"TOP 감시목록 생성 실패: {type(e).__name__}: {e}")
        return []


def parse_watchlist():
    rawv = str(mr._cfg.get("WATCHLIST", "")).strip()
    m = re.match(r"^TOP\s*(\d{1,3})$", rawv.upper())
    if m:
        return build_top_watchlist(max(1, min(100, int(m.group(1)))))
    out = []
    for part in rawv.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            code, name = part.split(":", 1)
        else:
            code, name = part, part
        code = code.strip().zfill(6)
        if code.isdigit() and len(code) == 6:
            out.append((code, name.strip() or code))
    return out


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {"seen_rcept": [], "price_alerted": {}, "digest_date": ""}


def save_state(state):
    # seen 목록이 무한히 커지지 않게 최근 3000건만 유지 (50종목 규모 대응)
    state["seen_rcept"] = state["seen_rcept"][-3000:]
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except OSError:
        pass


def log(msg):
    print(f"[{datetime.now().strftime('%m/%d %H:%M:%S')}] {msg}")


def send(text):
    ok, msg = mr.send_telegram(text)
    log(("전송 OK: " if ok else "전송 실패: ") + text.splitlines()[0][:50] + (" / " + msg if not ok else ""))
    return ok


# ── 감시 1: 장중 가격 급변 ──────────────────
def fetch_price_ratio(code):
    """(가격문자열, 부호 반영된 등락률 float) 반환. 실패 시 (None, None)"""
    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
    try:
        res = requests.get(url, headers=mr.HEADERS, timeout=5)
        res.raise_for_status()
        item = res.json()["datas"][0]
        price = item["closePrice"]
        ratio = abs(float(item["fluctuationsRatio"]))
        d = str(item["compareToPreviousPrice"].get("code", ""))
        if d == "5":
            ratio = -ratio
        elif d != "2":
            ratio = 0.0
        return price, ratio
    except Exception:
        return None, None


def is_market_hours(now):
    t = now.hour * 60 + now.minute
    return now.weekday() < 5 and (9 * 60 <= t <= 15 * 60 + 30)


def flow_context(code):
    """알림에 붙일 한 줄 수급 맥락 (전일까지 확정된 최근 5영업일 누적)"""
    if mr.krx is None or not mr._krx_ready():
        return None
    try:
        start = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = mr.krx.get_market_trading_value_by_date(start, end, code, detail=True)
        if df is None or df.empty:
            return None
        df = df.tail(5)
        parts = []
        for col in ("외국인", "연기금", "개인"):
            if col in df.columns:
                parts.append(f"{col} {df[col].sum()/1e8:+,.0f}억")
        if parts:
            return "└ 최근 5일 수급(전일까지 확정): " + ", ".join(parts)
    except Exception:
        return None
    return None


def check_prices(state, watchlist, threshold, now):
    if not is_market_hours(now):
        log("장외 시간 — 가격 알림 대기 중 (공시 감시는 계속 동작)")
        return
    today = now.strftime("%Y%m%d")
    hits = []
    for code, name in watchlist:
        key = f"{code}:{today}"
        if state["price_alerted"].get(key):
            continue
        price, ratio = fetch_price_ratio(code)
        if price is None:
            continue
        if abs(ratio) >= threshold:
            hits.append((code, name, price, ratio))
            state["price_alerted"][key] = True
        time.sleep(1)  # 종목 간 호출 간격

    if not hits:
        if watchlist:
            log(f"가격 점검 완료: {len(watchlist)}개 중 기준(±{threshold:.1f}%) 초과 없음 "
                f"(오늘 이미 알림 간 종목은 재알림 안 함)")
        return

    if len(hits) >= 4:
        # 동시다발 = 종목 이슈가 아니라 시장 전체 흐름일 가능성 → 한 통으로 요약
        up = sum(1 for h in hits if h[3] > 0)
        down = len(hits) - up
        direction = "상승" if up >= down else "하락"
        lines = [f"🌊 시장 흐름 알림: 감시 종목 중 {len(hits)}개가 동시에 "
                 f"기준(±{threshold:.1f}%)을 넘었습니다 ({now.strftime('%H:%M')} 기준)"]
        for code, name, price, ratio in sorted(hits, key=lambda h: -abs(h[3]))[:10]:
            arrow = "🔺" if ratio > 0 else "🔻"
            lines.append(f"{arrow} {name}({code}) {price}원 {ratio:+.2f}%")
        if len(hits) > 10:
            lines.append(f"... 외 {len(hits)-10}개")
        lines.append(f"\n여러 종목이 같은 방향({direction})으로 함께 움직이는 것은 "
                     "개별 종목 이슈보다 시장 전체 요인일 가능성이 있습니다. "
                     "개별 수급은 오늘 저녁 다이제스트에서 확정됩니다.")
        send("\n".join(lines) + DISCLAIMER)
    else:
        for code, name, price, ratio in hits:
            arrow = "🔺" if ratio > 0 else "🔻"
            msg = (f"{arrow} 가격 알림: {name}({code})\n"
                   f"현재가 {price}원, 전일대비 {ratio:+.2f}%\n"
                   f"기준({threshold:.1f}%) 이상 변동 감지, {now.strftime('%H:%M')} 기준")
            ctx = flow_context(code)
            if ctx:
                msg += "\n" + ctx
            send(msg + DISCLAIMER)
            time.sleep(1)


# ── 감시 2: 신규 공시 ───────────────────────
def check_disclosures(state, watchlist):
    if not mr._dart_ready():
        return
    bgn = (datetime.now() - timedelta(days=2)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    cc_cache = state.setdefault("corp_codes", {})
    baselined = state.setdefault("baselined", [])
    new_alerts = []      # 이번 사이클에서 발견된 진짜 '신규' 공시
    baseline_count = 0   # 조용히 기록만 한 기존 공시 수
    baseline_stocks = 0

    for code, name in watchlist:
        try:
            corp_code = cc_cache.get(code)
            if not corp_code:
                corp_code = mr._get_corp_code(code)  # 대용량 XML 파싱 — 종목당 1회만
                if corp_code:
                    cc_cache[code] = corp_code
            if not corp_code:
                continue
            res = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={"crtfc_key": mr.DART_API_KEY, "corp_code": corp_code,
                        "bgn_de": bgn, "end_de": end, "page_count": 50},
                timeout=10,
            )
            data = res.json()
            if data.get("status") != "000":
                continue

            first_time = code not in baselined  # 처음 감시하는 종목인가?
            for item in data.get("list", []):
                rcept = item.get("rcept_no", "")
                nm = item.get("report_nm", "").strip()
                if not rcept or rcept in state["seen_rcept"]:
                    continue
                state["seen_rcept"].append(rcept)
                if first_time:
                    baseline_count += 1  # 기존 공시: 기록만, 알림 생략
                elif any(k in nm for k in ALERT_KEYWORDS):
                    new_alerts.append((name, code, item.get("rcept_dt", ""),
                                       nm, item.get("flr_nm", "?"), rcept))
            if first_time:
                baselined.append(code)
                baseline_stocks += 1
        except Exception as e:
            log(f"공시 확인 실패({name}): {type(e).__name__}: {e}")
        time.sleep(1)

    if baseline_stocks:
        log(f"공시 감시 초기화: 신규 감시종목 {baseline_stocks}개의 기존 공시 "
            f"{baseline_count}건을 기록만 함 (이후 새로 뜨는 공시부터 알림)")

    if not new_alerts:
        return

    # ── 같은 회사 + 같은 공시 유형이 한 번에 몰리면 1건으로 압축 ──
    # (예: 임원 30명이 같은 날 보상 주식을 일괄 보고하는 경우)
    grouped = {}
    for name, code, dt, nm, flr, rcept in new_alerts:
        key = (name, code, nm)
        grouped.setdefault(key, []).append((dt, flr, rcept))

    messages = []
    for (name, code, nm), items in grouped.items():
        if len(items) >= 3:
            dt, _, rcept = items[0]
            messages.append(
                f"📢 {name}({code}): '{nm}' {len(items)}건 동시 접수 ({dt})\n"
                f"같은 날 다수 임원의 동시 보고는 스톡옵션·주식 보상 일괄 지급 등 "
                f"정기성 이벤트일 가능성이 높습니다. 취득/처분 방향은 원문에서 확인하세요.\n"
                f"대표 원문: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}")
        else:
            for dt, flr, rcept in items:
                messages.append(
                    f"📢 신규 공시: {name}({code})\n"
                    f"{dt}: {nm}\n"
                    f"제출인: {flr}\n"
                    f"원문: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}")

    if len(messages) >= 4:
        header = f"📢 신규 공시 묶음 ({len(messages)}건)"
        body = "\n\n".join(m.replace("📢 ", "• ", 1) for m in messages[:10])
        if len(messages) > 10:
            body += f"\n\n... 외 {len(messages)-10}건"
        send(header + "\n\n" + body + DISCLAIMER)
    else:
        for m in messages:
            send(m + DISCLAIMER)
            time.sleep(1)


# ── 감시 3: 장중 정기 시황 (N시간마다) ──────
def market_summary(state, watchlist, now):
    hours = cfg_float("SUMMARY_INTERVAL_HOURS", 2)
    if hours <= 0 or not is_market_hours(now):
        return
    last = state.get("last_summary", "")
    if last:
        try:
            prev = datetime.strptime(last, "%Y%m%d%H%M")
            if (now - prev).total_seconds() < hours * 3600 - 60:
                return
        except ValueError:
            pass

    quotes = []
    for code, name in watchlist:
        price, ratio = fetch_price_ratio(code)
        if price is not None:
            quotes.append((name, code, price, ratio))
        time.sleep(0.7)
    if not quotes:
        return

    ups = sum(1 for q in quotes if q[3] > 0)
    downs = sum(1 for q in quotes if q[3] < 0)
    quotes.sort(key=lambda q: -q[3])
    lines = [f"⏱ 정기 시황 ({now.strftime('%H:%M')}, 감시 {len(quotes)}개)",
             f"상승 {ups} / 하락 {downs} / 보합 {len(quotes) - ups - downs}",
             "", "🔺 상승 상위"]
    for name, code, price, ratio in quotes[:5]:
        lines.append(f"• {name} {price}원 ({ratio:+.2f}%)")
    lines.append("🔻 하락 상위")
    for name, code, price, ratio in quotes[-5:][::-1]:
        lines.append(f"• {name} {price}원 ({ratio:+.2f}%)")
    send("\n".join(lines) + DISCLAIMER)
    state["last_summary"] = now.strftime("%Y%m%d%H%M")


# ── 감시 4: 미국장/주말 글로벌 브리핑 ───────
US_TICKERS = [
    ("^GSPC", "S&P500"),
    ("^IXIC", "나스닥"),
    ("^SOX", "필라델피아 반도체"),
    ("NVDA", "엔비디아"),
    ("MU", "마이크론"),
    ("KRW=X", "원/달러"),
]


def global_snapshot():
    """미국 지수·반도체·환율의 현재 수준 (숫자만, 해석 없음)"""
    if mr.yf is None:
        return None
    lines = []
    for tk, name in US_TICKERS:
        try:
            hist = mr.yf.Ticker(tk).history(period="5d")
            if hist.empty or len(hist) < 2:
                continue
            last = hist["Close"].iloc[-1]
            prev = hist["Close"].iloc[-2]
            first = hist["Close"].iloc[0]
            lines.append(f"• {name}: {last:,.2f} "
                         f"(직전대비 {(last/prev-1)*100:+.2f}%, 5일 {(last/first-1)*100:+.1f}%)")
        except Exception:
            continue
        time.sleep(0.5)
    return lines or None


def headlines_block(n=3):
    """config의 NEWS_RSS_URL에서 헤드라인 제목+링크만 가져옴 (미설정 시 생략)"""
    url = str(mr._cfg.get("NEWS_RSS_URL", "")).strip()
    if not url:
        return []
    try:
        res = requests.get(url, headers=mr.HEADERS, timeout=10)
        root = ET.fromstring(res.content)
        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title:
                items.append(f"• {title}" + (f"\n  {link}" if link else ""))
            if len(items) >= n:
                break
        return (["", "📰 헤드라인"] + items) if items else []
    except Exception as e:
        log(f"헤드라인 수집 실패: {type(e).__name__}: {e}")
        return []


def _in_window(now, h1, m1, h2, m2):
    t = now.hour * 60 + now.minute
    return h1 * 60 + m1 <= t <= h2 * 60 + m2


def maybe_global_briefs(state, now):
    today = now.strftime("%Y%m%d")

    # (a) 평일 아침 07:00~08:50 — 미국 마감 브리핑 (한국장 개장 준비)
    if (now.weekday() < 5 and _in_window(now, 7, 0, 8, 50)
            and state.get("us_close_date") != today):
        snap = global_snapshot()
        if snap:
            send("🌅 미국 마감 브리핑 (한국장 개장 전)\n"
                 + "\n".join(snap)
                 + "\n".join(headlines_block())
                 + DISCLAIMER)
            state["us_close_date"] = today

    # (b) 평일 밤 23:00~23:59 — 미국 장중 체크 (미국장은 KST 22:30 개장)
    if (now.weekday() < 5 and _in_window(now, 23, 0, 23, 59)
            and state.get("us_night_date") != today):
        snap = global_snapshot()
        if snap:
            send("🌙 미국 장중 체크\n"
                 + "\n".join(snap)
                 + "\n".join(headlines_block())
                 + DISCLAIMER)
            state["us_night_date"] = today

    # (c) 주말 오전 09:00~10:30 — 주말 글로벌 체크
    if (now.weekday() >= 5 and _in_window(now, 9, 0, 10, 30)
            and state.get("weekend_date") != today):
        snap = global_snapshot()
        if snap:
            send("🧭 주말 글로벌 체크 (금요일 마감 기준)\n"
                 + "\n".join(snap)
                 + "\n".join(headlines_block())
                 + DISCLAIMER)
            state["weekend_date"] = today


# ── 감시 5: 저녁 다이제스트 ─────────────────
def maybe_daily_digest(state, watchlist, digest_hour, now):
    global mr
    today = now.strftime("%Y%m%d")
    if now.weekday() >= 5:  # 주말 제외
        return
    if state.get("digest_date") == today or now.hour < digest_hour:
        return
    digest_max = max(1, cfg_int("DAILY_DIGEST_MAX", 10))
    targets = watchlist[:digest_max]
    log(f"저녁 다이제스트 생성 시작 (대상 {len(targets)}개"
        + (f", 전체 {len(watchlist)}개 중 상한 적용" if len(watchlist) > digest_max else "")
        + ", 날짜 갱신을 위해 엔진 리로드)")
    mr = importlib.reload(mr)  # 모듈 상단의 날짜(START/END) 갱신
    for code, name in targets:
        try:
            raw = mr.build_raw_data(name, code)
            digest = mr.make_telegram_digest(name, code, raw)
            send("🌙 장마감 다이제스트\n" + digest)
        except Exception as e:
            log(f"다이제스트 실패({name}): {type(e).__name__}: {e}")
        time.sleep(2)
    state["digest_date"] = today


# ── 메인 루프 ───────────────────────────────
def main():
    ensure_bot_config()
    if not (mr.TELEGRAM_BOT_TOKEN and mr.TELEGRAM_CHAT_ID):
        print("텔레그램 미설정입니다. config.txt에 TELEGRAM_BOT_TOKEN과 "
              "TELEGRAM_CHAT_ID를 입력한 뒤 다시 실행하세요.")
        return

    watchlist = parse_watchlist()
    if not watchlist:
        print("감시 종목이 없습니다. config.txt의 WATCHLIST를 확인하세요. "
              "(예: WATCHLIST=005930:삼성전자,066570:LG전자)")
        return

    interval = max(10, cfg_int("CHECK_INTERVAL_MIN", 30))
    threshold = cfg_float("PRICE_ALERT_PCT", 3.0)
    digest_hour = cfg_int("DAILY_DIGEST_HOUR", 18)

    state = load_state()
    if len(watchlist) >= 30 and interval < 30:
        log(f"[권고] 감시 종목 {len(watchlist)}개에 주기 {interval}분은 짧습니다. "
            "CHECK_INTERVAL_MIN을 30 이상으로 올리세요.")

    if len(watchlist) > 10:
        head = ", ".join(f"{n}" for _, n in watchlist[:10])
        names = f"{head} 외 {len(watchlist)-10}개 (총 {len(watchlist)}개)"
    else:
        names = ", ".join(f"{n}({c})" for c, n in watchlist)
    log(f"알림봇 시작. 감시: {names} / 주기 {interval}분 / "
        f"가격기준 ±{threshold}% / 다이제스트 {digest_hour}시")
    send(f"🤖 알림봇 시작\n감시 종목: {names}\n"
         f"점검 주기 {interval}분, 가격 알림 기준 ±{threshold}%, "
         f"저녁 다이제스트 {digest_hour}시" + DISCLAIMER)

    while True:
        try:
            now = datetime.now()
            check_prices(state, watchlist, threshold, now)
            market_summary(state, watchlist, now)
            check_disclosures(state, watchlist)
            maybe_global_briefs(state, now)
            maybe_daily_digest(state, watchlist, digest_hour, now)
            save_state(state)
        except KeyboardInterrupt:
            raise
        except Exception:
            log("루프 오류 (봇은 계속 동작):\n" + traceback.format_exc())
        try:
            time.sleep(interval * 60)
        except KeyboardInterrupt:
            raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n알림봇 종료.")