# -*- coding: utf-8 -*-
"""
주식 대본용 자료조사 파이프라인 v2 (풀 버전)

데이터 소스 (13개 섹션):
  [안정] 현재가(네이버), 가격/거래대금/52주위치(KRX), 투자자별 세부수급(KRX),
         공매도 거래+잔고(KRX), 외국인 지분율(KRX), PER/PBR(KRX),
         환율(yfinance), 글로벌 반도체/지수(yfinance),
         DART 공시목록, DART 임원·주요주주 상세(매수/매도 방향 자동 판별)
  [실험] 프로그램 매매(네이버 크롤링), 신용융자 잔고(금투협 크롤링)
         → 실패해도 "데이터 없음"으로만 표시되고 나머지는 정상 동작
  [수동] 관세청 수출 잠정치 등은 MANUAL_NOTES 에 직접 붙여넣기

설치:
    pip install requests pykrx yfinance pandas lxml
"""

import io
import os
import re
import time
import zipfile
import base64
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests
from datetime import datetime, timedelta, timezone

KST_TZ = timezone(timedelta(hours=9))

# ═══════════════════════════════════════════════
#  인증 설정: 같은 폴더의 config.txt 에서 읽어옵니다 (메모장으로 편집)
#  config.txt가 없으면 첫 실행 시 자동으로 템플릿이 생성됩니다.
#  우선순위: config.txt > OS 환경변수
# ═══════════════════════════════════════════════
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_BASE_DIR, "config.txt")
OUTPUT_DIR = os.path.join(_BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

_TELEGRAM_BLOCK = """
# 텔레그램 알림 (선택사항)
# 1) 텔레그램에서 @BotFather 검색 → /newbot → 봇 이름 정하면 토큰을 줌
# 2) 만든 봇에게 아무 메시지 하나 보낸 뒤,
#    브라우저에서 https://api.telegram.org/bot<토큰>/getUpdates 열면
#    "chat":{"id":숫자 가 보임 → 그 숫자가 CHAT_ID
#    (채널로 보내려면 봇을 채널 관리자로 추가하고 채널의 chat_id 사용)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
"""

_BOT_BLOCK = """
# ── 자동 알림봇 설정 (market_alert_bot.py 실행 시 사용) ──
# 감시 종목: 코드:이름 형식을 쉼표로 나열
# 또는 TOP50 처럼 쓰면 코스피 시가총액 상위 N개를 자동으로 감시 (TOP10~TOP100)
WATCHLIST=005930:삼성전자,066570:LG전자
# 점검 주기(분). 너무 짧게 잡으면 API 차단 위험 (최소 10, 종목 30개 이상이면 30 이상 권장)
CHECK_INTERVAL_MIN=30
# 장중 등락 알림 기준(%). 이 이상 움직이면 알림
PRICE_ALERT_PCT=3.0
# 저녁 데이터 다이제스트 발송 시각(24시간제, 그날 수급 확정 후인 18시 권장)
DAILY_DIGEST_HOUR=18
"""

_BOT_EXTRA_BLOCK = """
# 저녁 다이제스트를 보낼 최대 종목 수 (감시종목이 많을 때 도배 방지, 목록 앞에서부터)
DAILY_DIGEST_MAX=10
"""

_CONFIG_TEMPLATE = """# ── 주식 자료조사기 설정 파일 ──────────────────
# 메모장으로 이 파일을 열어 '=' 오른쪽에 값을 입력하고 저장하세요.
# 따옴표는 필요 없습니다. '#'으로 시작하는 줄은 무시됩니다.
# ★ 이 파일은 절대 남에게 공유하거나 인터넷에 올리지 마세요 ★

# KRX 계정 (data.krx.co.kr 무료 회원가입 후 입력)
KRX_ID=
KRX_PW=

# DART API 키 (opendart.fss.or.kr 에서 발급받은 40자리 키)
DART_API_KEY=

# OpenAI API 키 (대본/썸네일 자동 생성용)
OPENAI_API_KEY=

# Gemini API 키 (선택, 전체 대본생성 및 체인 보완용)
GEMINI_API_KEY=

# OpenAI 모델명
# 대본/리포트/썸네일 문구: gpt-5.5 권장. 계정에서 미지원이면 접근 가능한 모델명으로 바꾸세요.
OPENAI_TEXT_MODEL=gpt-5.5

# AI 썸네일 이미지: gpt-image-1.5 권장. 미지원이면 gpt-image-1 또는 gpt-image-1-mini로 바꾸세요.
OPENAI_IMAGE_MODEL=gpt-image-1.5

# Gemini 텍스트 모델명
GEMINI_TEXT_MODEL=gemini-2.5-pro

# 전체 대본생성 엔진: chain / openai / gemini / mixed
BATCH_ENGINE_MODE=chain

# 전체 대본생성 동시 작업 수. mixed는 2 권장, 3 이상은 API 제한 위험
BATCH_PARALLEL_WORKERS=2
""" + _TELEGRAM_BLOCK + _BOT_BLOCK


def _load_config():
    cfg = {}
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(_CONFIG_TEMPLATE)
        print(f"[안내] 설정 파일을 새로 만들었습니다: {CONFIG_PATH}")
        print("       메모장으로 열어 KRX 아이디/비번, DART 키, OpenAI 키를 입력한 뒤 다시 실행하세요.")
        return cfg
    try:
        raw = open(CONFIG_PATH, encoding="utf-8-sig").read()
    except UnicodeDecodeError:
        raw = open(CONFIG_PATH, encoding="cp949").read()  # 메모장 ANSI 저장 대응

    # 구버전 config.txt 마이그레이션: 새로 생긴 설정 항목을 자동으로 붙여줌
    for _marker, _block, _msg in [
        ("TELEGRAM_BOT_TOKEN", _TELEGRAM_BLOCK, "텔레그램 설정 항목"),
        ("WATCHLIST", _BOT_BLOCK, "자동 알림봇 설정 항목"),
        ("DAILY_DIGEST_MAX", _BOT_EXTRA_BLOCK, "다이제스트 상한 설정 항목"),
        ("OPENAI_API_KEY", "\n# OpenAI API 키 (대본/썸네일 자동 생성용)\nOPENAI_API_KEY=\n", "OpenAI API 키 항목"),
        ("GEMINI_API_KEY", "\n# Gemini API 키 (선택, 전체 대본생성 및 체인 보완용)\nGEMINI_API_KEY=\n", "Gemini API 키 항목"),
        ("OPENAI_TEXT_MODEL", "\n# OpenAI 텍스트 모델명\nOPENAI_TEXT_MODEL=gpt-5.5\n", "OpenAI 텍스트 모델 항목"),
        ("OPENAI_IMAGE_MODEL", "\n# OpenAI 이미지 모델명\nOPENAI_IMAGE_MODEL=gpt-image-1.5\n", "OpenAI 이미지 모델 항목"),
        ("GEMINI_TEXT_MODEL", "\n# Gemini 텍스트 모델명\nGEMINI_TEXT_MODEL=gemini-2.5-pro\n", "Gemini 텍스트 모델 항목"),
        ("BATCH_ENGINE_MODE", "\n# 전체 대본생성 엔진: chain / openai / gemini / mixed\nBATCH_ENGINE_MODE=chain\n", "전체 대본생성 엔진 항목"),
        ("BATCH_PARALLEL_WORKERS", "\n# 전체 대본생성 동시 작업 수. mixed는 2 권장\nBATCH_PARALLEL_WORKERS=2\n", "전체 대본생성 병렬 수 항목"),
        ("OPENAI_MAX_OUTPUT_TOKENS", "\n# OpenAI 1회 출력 토큰 상한. gpt-5.5 기본 TPM 10,000이면 12000 권장\nOPENAI_MAX_OUTPUT_TOKENS=12000\n", "OpenAI 출력 토큰 상한 항목"),
        ("CACHE_MIN", "\n# 수집 데이터 캐시 유지 시간(분). 같은 종목 재수집 시 저장본 사용 (0=끔)\nCACHE_MIN=10\n", "수집 캐시 설정 항목"),
    ]:
        if _marker not in raw:
            try:
                with open(CONFIG_PATH, "a", encoding="utf-8") as f:
                    f.write("\n" + _block)
                raw += "\n" + _block
                print(f"[안내] config.txt에 {_msg}을 추가했습니다. "
                      "메모장으로 열어 값을 확인/수정하세요.")
            except OSError:
                pass

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


_cfg = _load_config()

KRX_ID = _cfg.get("KRX_ID") or os.environ.get("KRX_ID", "")
KRX_PW = _cfg.get("KRX_PW") or os.environ.get("KRX_PW", "")
DART_API_KEY = _cfg.get("DART_API_KEY") or os.environ.get("DART_API_KEY", "")
OPENAI_API_KEY = _cfg.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = _cfg.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
OPENAI_TEXT_MODEL = _cfg.get("OPENAI_TEXT_MODEL") or os.environ.get("OPENAI_TEXT_MODEL", "gpt-5.5")
OPENAI_IMAGE_MODEL = _cfg.get("OPENAI_IMAGE_MODEL") or os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
GEMINI_TEXT_MODEL = _cfg.get("GEMINI_TEXT_MODEL") or os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-pro")
BATCH_ENGINE_MODE = (_cfg.get("BATCH_ENGINE_MODE") or os.environ.get("BATCH_ENGINE_MODE", "mixed")).strip().lower()
try:
    OPENAI_MAX_OUTPUT_TOKENS = int(_cfg.get("OPENAI_MAX_OUTPUT_TOKENS") or os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "12000"))
except Exception:
    OPENAI_MAX_OUTPUT_TOKENS = 12000
try:
    BATCH_PARALLEL_WORKERS = int(_cfg.get("BATCH_PARALLEL_WORKERS") or os.environ.get("BATCH_PARALLEL_WORKERS", "2"))
except Exception:
    BATCH_PARALLEL_WORKERS = 2
BATCH_PARALLEL_WORKERS = max(1, min(BATCH_PARALLEL_WORKERS, 4))
TELEGRAM_BOT_TOKEN = _cfg.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _cfg.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")

# pykrx가 임포트 시점에 환경변수를 읽으므로, 임포트 '전에' 설정해야 함
for _k, _v in {"KRX_ID": KRX_ID, "KRX_PW": KRX_PW}.items():
    if _v:
        os.environ[_k] = _v

# ═══════════════════════════════════════════════
#  수동 조사 메모 (자동화 불가 데이터는 여기에 직접 붙여넣기)
#  예: 관세청 수출입 잠정치(매월 11일/21일경 발표, customs.go.kr 보도자료),
#      주요 뉴스 헤드라인, 증권사 리포트 요약 등
# ═══════════════════════════════════════════════
MANUAL_NOTES = """
(여기에 직접 조사한 내용을 붙여넣으면 AI 리포트에 반영됩니다. 없으면 비워두세요)
"""

# 글로벌 크로스체크 티커 (종목 성격에 맞게 수정 가능)
# 반도체주 기준 기본값. 예: 2차전지주라면 "ALB": "앨버말" 등으로 교체
GLOBAL_TICKERS = {
    "^KS11": "KOSPI",
    "^SOX": "필라델피아 반도체지수",
    "MU": "마이크론",
    "NVDA": "엔비디아",
}


try:
    from pykrx import stock as krx
except ImportError:
    krx = None
    print("[경고] pykrx 미설치. `pip install pykrx` 필요")

try:
    import yfinance as yf
except ImportError:
    yf = None
    print("[경고] yfinance 미설치. `pip install yfinance` 필요")

try:
    import pandas as pd
except ImportError:
    pd = None
    print("[경고] pandas 미설치. `pip install pandas lxml` 필요")


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Referer": "https://m.stock.naver.com/",
}

_CORP_CODE_CACHE = "dart_corp_codes.xml"

FMT = "%Y%m%d"

# 대본 품질 기준
# - 대본 생성/확장/품질 표시에서 공통으로 쓰는 상수입니다.
# - 누락되면 AI 대본 생성 단계에서 NameError가 발생합니다.
MIN_SCRIPT_CHARS = 10000
MAX_SCRIPT_CHARS = 12000
HARD_MIN_SCRIPT_CHARS = 9000
WEEKEND_HARD_MIN_SCRIPT_CHARS = 9000
MIN_SEPARATOR_COUNT = 30
MAX_SEPARATOR_COUNT = 50
TARGET_SEPARATOR_COUNT = 36
MAX_AI_EXPAND_ROUNDS = 1
NEWS_RSS_TIMEOUT = 5
NEWS_REDIRECT_TIMEOUT = 3
NEWS_META_TIMEOUT = 3
NEWS_META_DETAIL_LIMIT = 3
NEWS_LOOKBACK_DAYS = 7


def _refresh_time_context(now=None):
    """자료를 새로 수집할 때마다 기준 시각/조회 기간을 현재 시각으로 갱신한다."""
    global TODAY, START, START_1Y, END
    TODAY = now or datetime.now()
    START = (TODAY - timedelta(days=21)).strftime(FMT)      # 최근 5영업일용
    START_1Y = (TODAY - timedelta(days=370)).strftime(FMT)  # 52주 계산용
    END = TODAY.strftime(FMT)



_refresh_time_context()


def get_market_phase(now=None):
    """현재 시각 기준 시장 단계를 반환한다. 사용자의 PC 시간이 KST라고 가정한다."""
    now = now or TODAY
    if now.weekday() >= 5:
        return "WEEKEND"
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60:
        return "PREOPEN"
    if minutes <= 15 * 60 + 30:
        return "INTRADAY"
    if minutes < 18 * 60:
        return "AFTER_CLOSE_PENDING"
    return "AFTER_CLOSE"


def get_market_phase_label(now=None):
    phase = get_market_phase(now)
    return {
        "PREOPEN": "장전/아침 자료 수집",
        "INTRADAY": "정규장 장중 자료 수집",
        "AFTER_CLOSE_PENDING": "정규장 종료 직후 자료 수집",
        "AFTER_CLOSE": "장마감 이후 자료 수집",
        "WEEKEND": "주말/휴장일 자료 수집",
    }.get(phase, phase)


def get_market_phase_guidance(now=None):
    """AI가 장중/장전/장마감 시점을 섞지 않도록 수집 데이터에 함께 넣는 안전 가이드."""
    now = now or TODAY
    phase = get_market_phase(now)
    label = get_market_phase_label(now)
    common = [
        f"[시장 단계] {label}",
        f"[단계 기준 시각] {now.strftime('%Y-%m-%d %H:%M')} KST",
    ]
    if phase == "PREOPEN":
        common += [
            "- 아직 오늘 정규장이 시작되지 않았습니다.",
            "- 오늘 시가·고가·저가·종가·거래량·투자자별 수급은 확정되지 않았습니다.",
            "- KRX 가격 추이의 마지막 종가는 직전 확정 거래일 종가입니다. 이것을 오늘 종가라고 말하지 마십시오.",
            "- 대본에서는 '오늘 종가', '오늘 마감', '장마감', '마감했습니다', '장을 마쳤습니다' 표현을 쓰지 마십시오.",
            "- 허용 표현: '장전 기준', '직전 거래일 종가 기준', '오늘 장이 시작되면 확인할 것'.",
        ]
    elif phase == "INTRADAY":
        common += [
            "- 지금은 정규장이 진행 중입니다.",
            "- 네이버 현재가는 장중 현재가입니다. 오늘 종가와 오늘 장마감 결과는 아직 확정되지 않았습니다.",
            "- KRX 가격 추이의 마지막 종가는 최근 확정 거래일 종가일 수 있습니다. 이것을 오늘 종가라고 말하지 마십시오.",
            "- 투자자별 수급, 외국인 지분율, 공매도는 최근 확정 데이터 기준으로만 말하고, 오늘 수급은 장 마감 후 확인이라고 말하십시오.",
            "- 대본에서는 '오늘 종가', '오늘 마감', '장마감', '마감했습니다', '장을 마쳤습니다' 표현을 쓰지 마십시오.",
            "- 허용 표현: '자료 수집 시점에 확인된 가격', '장중 확인 가격', '지금 확인되는 가격', '오늘 장이 끝난 뒤 확인할 것'.",
            "- '이 시각 기준 현재가', '이 시각 기준 가격'처럼 어색한 표현은 쓰지 마십시오.",
        ]
    elif phase == "AFTER_CLOSE_PENDING":
        common += [
            "- 정규장은 끝났지만 KRX 확정 데이터가 일부 늦게 반영될 수 있는 시간대입니다.",
            "- 현재가가 시간외/대체거래소 가격일 수 있으므로 정규장 종가와 섞어 말하지 마십시오.",
            "- 장마감 브리핑 포맷에서만 정규장 마감 흐름을 중심으로 쓰십시오. 다른 포맷은 '정규장 이후 확인 기준'으로 표현하십시오.",
        ]
    elif phase == "AFTER_CLOSE":
        common += [
            "- 장마감 이후 자료 수집입니다.",
            "- 장마감 브리핑 포맷에서는 정규장 종가와 마감 위치를 사용할 수 있습니다.",
            "- 정프로/이면추적 포맷에서는 장마감 브리핑처럼 종가 위치만으로 시작하지 말고, 수급·가격·거래량의 충돌을 중심으로 말하십시오.",
        ]
    else:
        common += [
            "- 주말 또는 휴장일 기준 자료 수집입니다.",
            "- 현재가를 실시간 장중 가격처럼 말하지 말고, 최근 확정 거래일 기준으로 표현하십시오.",
            "- 오늘 종가·오늘 장마감처럼 정규장 진행을 전제로 한 표현은 쓰지 마십시오.",
        ]
    return "\n".join(common)


_KRX_LOGIN_MSG = ("데이터 없음 (KRX 로그인 미설정: data.krx.co.kr 무료 가입 후 "
                  "config.txt에 KRX_ID/KRX_PW를 입력하세요)")


def _krx_ready():
    return bool(os.environ.get("KRX_ID")) and bool(os.environ.get("KRX_PW"))


def _num(s):
    """DART가 주는 '1,234' / '-1,234' 형태 문자열을 int로"""
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────
# 1. 실시간 현재가 (네이버 폴링 API)
# ──────────────────────────────────────────────
def get_price(stock_code):
    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{stock_code}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        res.raise_for_status()
        item = res.json()["datas"][0]

        price = item["closePrice"]
        ratio = abs(float(item["fluctuationsRatio"]))
        compare = item["compareToPreviousPrice"]
        direction = compare.get("text", "")
        code = str(compare.get("code", ""))

        if code == "2":
            ratio_str = f"+{ratio:.2f}%"
        elif code == "5":
            ratio_str = f"-{ratio:.2f}%"
        else:
            ratio_str = "0.00%"

        change_amt = item.get("compareToPreviousClosePrice", "N/A")
        line = (f"현재가 {price}원 / 전일대비 {direction} {change_amt}원 ({ratio_str}) "
                f"/ 조회시각 {TODAY.strftime('%Y-%m-%d %H:%M')}")

        # 정규장(평일 09:00~15:30) 밖에 조회하면 시간외·대체거래소(NXT) 가격일 수 있음
        t = TODAY.hour * 60 + TODAY.minute
        if TODAY.weekday() >= 5 or not (9 * 60 <= t <= 15 * 60 + 30):
            line += (" ※ 정규장 외 시간 조회 — 이 가격은 시간외/대체거래소 체결가일 수 있어 "
                     "KRX 정규장 종가(섹션 2의 마지막 종가)와 다를 수 있음. "
                     "두 값이 다르면 '정규장 종가 대비 시간외에서 어느 방향으로 움직이는 중'으로 해석할 것")
        return line
    except requests.exceptions.RequestException as e:
        return f"데이터 없음 (요청 실패: {e})"
    except (KeyError, IndexError, ValueError) as e:
        return f"데이터 없음 (파싱 실패: {type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 1-1. 시간대별 가격 흐름 (네이버 차트 분봉, 실험적)
# ──────────────────────────────────────────────
def _parse_naver_chart_item(raw):
    """네이버 fchart item의 data 속성을 안전하게 해석한다.

    day 데이터는 YYYYMMDD|시가|고가|저가|종가|거래량,
    minute 데이터는 YYYYMMDDHHMM|null|null|null|현재가|거래량 형태로 들어오는 경우가 많다.
    """
    parts = str(raw or "").split("|")
    if len(parts) < 6:
        return None
    dt = parts[0].strip()
    if len(dt) < 12 or not dt[:12].isdigit():
        return None
    try:
        def to_int(value):
            value = str(value).replace(",", "").strip()
            if not value or value.lower() == "null":
                return None
            return int(float(value))

        p1, p2, p3, p4, p5 = (to_int(parts[i]) for i in range(1, 6))
        # 분봉: 시가/고가/저가가 null이고 4번째 숫자가 현재가/마지막가격이다.
        if p1 is None and p2 is None and p3 is None and p4 is not None:
            price = p4
            open_ = high = low = p4
            volume = p5 or 0
        else:
            open_ = p1
            high = p2
            low = p3
            price = p4
            volume = p5 or 0
        if price is None:
            return None
        return {
            "dt": datetime.strptime(dt[:12], "%Y%m%d%H%M"),
            "price": price,
            "open": open_ if open_ is not None else price,
            "high": high if high is not None else price,
            "low": low if low is not None else price,
            "volume": volume,
        }
    except (TypeError, ValueError):
        return None


def _fetch_naver_intraday_rows(stock_code, count=420):
    """네이버 차트 분봉을 가져온다. 실패해도 전체 자료조사는 죽이지 않는다."""
    code = str(stock_code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return []
    url = (
        "https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={code}&timeframe=minute&count={int(count)}&requestType=0"
    )
    try:
        headers = {**HEADERS, "Referer": f"https://finance.naver.com/item/main.naver?code={code}"}
        res = requests.get(url, headers=headers, timeout=6)
        res.raise_for_status()
        # 네이버 fchart XML은 EUC-KR 선언을 달고 오는 경우가 있어 bytes 직접 파싱이
        # 환경에 따라 "multi-byte encodings are not supported"로 실패할 수 있다.
        xml_text = res.content.decode("euc-kr", errors="ignore")
        xml_text = re.sub(r"^\s*<\?xml[^>]*\?>", "", xml_text).strip()
        root = ET.fromstring(xml_text)
        rows = []
        for item in root.findall(".//item"):
            row = _parse_naver_chart_item(item.attrib.get("data", ""))
            if row:
                rows.append(row)
        return sorted(rows, key=lambda r: r["dt"])
    except Exception:
        return []


def get_intraday_price_timeline(stock_code):
    """대본에 쓸 수 있는 시간대별 가격 흐름 요약을 만든다.

    원자료에서 받은 가격·고가·저가·거래량만 표시한다. 거래대금 추정처럼
    원자료에 없는 금액 환산은 하지 않는다.
    """
    rows = _fetch_naver_intraday_rows(stock_code)
    if not rows:
        return "데이터 없음 (네이버 분봉 수집 실패 또는 장중 데이터 없음)"

    # 여러 날짜가 섞이면 가장 최근 날짜만 사용한다.
    latest_day = max(r["dt"].date() for r in rows)
    day_rows = [r for r in rows if r["dt"].date() == latest_day]
    if not day_rows:
        return "데이터 없음"

    lines = [
        f"  [기준일] {latest_day.strftime('%Y-%m-%d')} / 네이버 차트 분봉 기준(실험적)",
        "  ※ KRX 확정 일봉과 값이 다르면 정규장 확정값은 KRX를 우선한다.",
    ]

    first = day_rows[0]
    last = day_rows[-1]
    high_row = max(day_rows, key=lambda r: r["high"])
    low_row = min(day_rows, key=lambda r: r["low"])

    def fmt_row(label, row):
        return (
            f"  {label}: 가격 {row['price']:,}원 / "
            f"고가 {row['high']:,}원 / 저가 {row['low']:,}원 / "
            f"거래량 {row['volume']:,}주"
        )

    lines.append(fmt_row("첫 체결권 " + first["dt"].strftime("%H:%M"), first))

    targets = ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "15:20", "15:30"]
    used_times = {first["dt"].strftime("%H:%M")}
    for t in targets:
        hh, mm = map(int, t.split(":"))
        candidates = [r for r in day_rows if (r["dt"].hour, r["dt"].minute) <= (hh, mm)]
        if not candidates:
            continue
        row = candidates[-1]
        label_time = row["dt"].strftime("%H:%M")
        if label_time in used_times:
            continue
        used_times.add(label_time)
        lines.append(fmt_row(label_time, row))

    last_time = last["dt"].strftime("%H:%M")
    if last_time not in used_times:
        lines.append(fmt_row("마지막 체결권 " + last_time, last))

    lines.append(
        f"  [시간대 고점] {high_row['dt'].strftime('%H:%M')} {high_row['high']:,}원"
        f" / [시간대 저점] {low_row['dt'].strftime('%H:%M')} {low_row['low']:,}원"
    )
    try:
        move = last["price"] - first["price"]
        sign = "+" if move > 0 else ""
        pct = move / first["price"] * 100 if first["price"] else 0
        lines.append(f"  [시간대 변화] 첫 체결권 대비 마지막 체결권 {sign}{move:,}원 ({sign}{pct:.2f}%)")
    except Exception:
        pass
    return "\n".join(lines)


# ──────────────────────────────────────────────
# 2. 가격/거래대금 추이 + 52주 위치 (상승·하락에 '진짜 돈'이 실렸는지)
# ──────────────────────────────────────────────
def get_price_volume_trend(stock_code):
    if krx is None:
        return "데이터 없음 (pykrx 미설치)"
    if not _krx_ready():
        return _KRX_LOGIN_MSG
    try:
        df = krx.get_market_ohlcv_by_date(START, END, stock_code)
        if df is None or df.empty:
            return "데이터 없음"
        df5 = df.tail(5)
        lines = []
        for date, row in df5.iterrows():
            d = date.strftime("%m/%d")
            open_ = row.get("시가", 0)
            high = row.get("고가", 0)
            low = row.get("저가", 0)
            close = row.get("종가", 0)
            vol = row.get("거래량", 0)
            amount = row.get("거래대금", None)
            chg_rate = row.get("등락률", None)
            line = (f"  {d}: 시가 {open_:,.0f}원 / 고가 {high:,.0f}원 / "
                    f"저가 {low:,.0f}원 / 종가 {close:,.0f}원 / 거래량 {vol:,.0f}주")
            if chg_rate is not None:
                try:
                    line += f" / 등락률 {float(chg_rate):+.2f}%"
                except (TypeError, ValueError):
                    pass
            if amount:
                line += f" / 거래대금 {amount/1e8:,.0f}억"
            lines.append(line)

        # KRX가 제공하는 최근 확정 거래일 종가 위치. 장전/장중 대본에서는 오늘 마감으로 말하면 안 됨.
        try:
            last = df5.iloc[-1]
            high, low, close = last.get("고가", 0), last.get("저가", 0), last.get("종가", 0)
            if high and low and high > low:
                day_pos = (close - low) / (high - low) * 100
                if day_pos >= 70:
                    pos_msg = "고점권 마감"
                elif day_pos <= 30:
                    pos_msg = "저점권 마감"
                else:
                    pos_msg = "중간권 마감"
                lines.append(f"  [최근 확정 거래일 위치] KRX 최근 확정 거래일 종가는 해당 거래일 저가~고가 범위의 {day_pos:.0f}% 지점 ({pos_msg})")
        except Exception:
            pass

        # 거래량 추세: 최근일 vs 직전 4일 평균
        if len(df5) >= 2:
            recent = df5["거래량"].iloc[-1]
            base = df5["거래량"].iloc[:-1].mean()
            if base > 0:
                lines.append(f"  [거래량] 최근일이 직전 4일 평균의 {recent/base*100:.0f}% 수준")

        # 52주 위치
        try:
            df1y = krx.get_market_ohlcv_by_date(START_1Y, END, stock_code)
            hi, lo = df1y["고가"].max(), df1y["저가"].min()
            cur = df1y["종가"].iloc[-1]
            pos = (cur - lo) / (hi - lo) * 100 if hi > lo else 0
            lines.append(f"  [52주] 최고 {hi:,.0f} / 최저 {lo:,.0f} / "
                         f"현재는 밴드 하단에서 {pos:.0f}% 위치 "
                         f"(고점대비 {(cur/hi-1)*100:+.1f}%)")
        except Exception:
            lines.append("  [52주] 데이터 없음")
        return "\n".join(lines)
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 3. 투자자별 '세부' 수급 (연기금/금융투자/사모 등) - KRX 공식
# ──────────────────────────────────────────────
def get_investor_flows(stock_code):
    if krx is None:
        return "데이터 없음 (pykrx 미설치)"
    if not _krx_ready():
        return _KRX_LOGIN_MSG
    try:
        df = krx.get_market_trading_value_by_date(START, END, stock_code, detail=True)
        if df is None or df.empty:
            return "데이터 없음 (조회 결과 없음)"
        df = df.tail(5)

        lines = []
        for date, row in df.iterrows():
            d = date.strftime("%m/%d")
            parts = []
            for col in ["금융투자", "투신", "사모", "연기금", "외국인", "개인"]:
                if col in row.index:
                    parts.append(f"{col} {row[col]/1e8:+,.0f}억")
            lines.append(f"  {d}: " + ", ".join(parts))

        cum = []
        for col in ["연기금", "외국인", "금융투자", "개인"]:
            if col in df.columns:
                cum.append(f"{col} {df[col].sum()/1e8:+,.0f}억")
        lines.append("  [5일 누적] " + ", ".join(cum))
        return "\n".join(lines)
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 4. 공매도: 일별 거래 + 잔고 (잔고 = 언젠가 되사야 할 '예약된 매수 물량')
# ──────────────────────────────────────────────
def get_short_selling(stock_code):
    if krx is None:
        return "데이터 없음 (pykrx 미설치)"
    if not _krx_ready():
        return _KRX_LOGIN_MSG

    lines = []
    # (1) 일별 공매도 거래량
    try:
        df = krx.get_shorting_volume_by_date(START, END, stock_code)
        if df is not None and not df.empty:
            df = df.tail(5)
            last_date = df.index[-1]
            for date, row in df.iterrows():
                d = date.strftime("%m/%d")
                vol = row.get("공매도", row.get("공매도거래량", None))
                ratio = row.get("비중", None)
                part = (f"  {d}: 공매도 {vol:,.0f}주" if vol is not None
                        else f"  {d}: 공매도 데이터 없음")
                if ratio is not None:
                    part += f" (거래량 대비 {ratio:.2f}%)"
                if date == last_date and (vol == 0 or vol is None):
                    part += " ← 당일분 미집계 가능성 높음, 분석에서 제외할 것"
                lines.append(part)
        else:
            lines.append("  일별 거래: 데이터 없음")
    except Exception as e:
        lines.append(f"  일별 거래: 데이터 없음 ({type(e).__name__}: {e})")

    # (2) 공매도 잔고 (T+2 지연 집계)
    try:
        bal = krx.get_shorting_balance_by_date(START, END, stock_code)
        if bal is not None and not bal.empty:
            # 최근일부터 거슬러 올라가며 잔고가 0이 아닌 마지막 집계일을 찾는다
            found = None
            for date in reversed(bal.index):
                row = bal.loc[date]
                qty = row.get("공매도잔고", None)
                if qty is not None and qty > 0:
                    found = (date, qty, row.get("비중", None))
                    break
            if found:
                date, qty, rate = found
                msg = f"  [잔고] {date.strftime('%m/%d')} 기준 공매도 잔고 {qty:,.0f}주"
                if rate is not None:
                    msg += f" (상장주식수 대비 {rate:.2f}%)"
                msg += " — 잔고는 반드시 되사야 하는 물량(숏스퀴즈 연료)"
                lines.append(msg)
            else:
                lines.append("  [잔고] 조회 기간 내 집계값 없음 "
                             "(전량 0으로 표시됨 — 미집계 또는 컬럼 구조 변경 의심, 분석에서 제외할 것)")
        else:
            lines.append("  [잔고] 데이터 없음 (잔고는 T+2 지연 집계)")
    except Exception as e:
        lines.append(f"  [잔고] 데이터 없음 ({type(e).__name__}: {e})")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 5. 외국인 지분율 추이
# ──────────────────────────────────────────────
def get_foreign_ownership(stock_code):
    if krx is None:
        return "데이터 없음 (pykrx 미설치)"
    if not _krx_ready():
        return _KRX_LOGIN_MSG
    try:
        df = krx.get_exhaustion_rates_of_foreign_investment_by_date(START, END, stock_code)
        if df is None or df.empty:
            return "데이터 없음"
        df = df.tail(5)
        col = "지분율" if "지분율" in df.columns else df.columns[-1]
        lines = [f"  {date.strftime('%m/%d')}: {row[col]:.2f}%"
                 for date, row in df.iterrows()]
        first, last = df[col].iloc[0], df[col].iloc[-1]
        trend = "증가(축적)" if last > first else ("감소(이탈)" if last < first else "보합")
        lines.append(f"  [추세] {first:.2f}% → {last:.2f}% ({trend})")
        return "\n".join(lines)
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 6. 밸류에이션
# ──────────────────────────────────────────────
def get_fundamentals(stock_code):
    """PER/PBR — 네이버 표시값 우선 (시청자가 보는 값과 일치), 실패 시 KRX 폴백"""
    # 1차: 네이버 통합 API (HTS/네이버 화면과 동일 기준)
    try:
        res = requests.get(
            f"https://m.stock.naver.com/api/stock/{stock_code}/integration",
            headers=HEADERS, timeout=6)
        res.raise_for_status()
        infos = {str(it.get("code", "")).lower(): str(it.get("value", "")).strip()
                 for it in res.json().get("totalInfos", [])}
        parts = []
        if infos.get("per"):
            parts.append(f"PER {infos['per']}")
        if infos.get("pbr"):
            parts.append(f"PBR {infos['pbr']}")
        for k in ("dividend", "dividendyield", "dvr"):
            if infos.get(k):
                parts.append(f"배당수익률 {infos[k]}")
                break
        if parts:
            return " / ".join(parts) + " (네이버 표시 기준)"
    except Exception:
        pass

    # 2차: KRX (직전 사업연도 EPS 기준이라 시장 표시값과 다를 수 있음)
    if krx is None:
        return "데이터 없음 (pykrx 미설치)"
    if not _krx_ready():
        return _KRX_LOGIN_MSG
    try:
        df = krx.get_market_fundamental_by_date(START, END, stock_code)
        if df is None or df.empty:
            return "데이터 없음"
        row = df.iloc[-1]
        return (f"PER {row.get('PER', 0):.1f}배 / PBR {row.get('PBR', 0):.2f}배 "
                f"/ 배당수익률 {row.get('DIV', 0):.2f}% "
                f"(KRX 기준 — 직전 사업연도 EPS 산출이라 네이버 표시값과 다를 수 있음)")
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


def _format_won_compact(value):
    """원 단위 큰 금액을 조/억 단위로 읽기 좋게 표시한다."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "-" if v < 0 else ""
    v = abs(v)
    jo = int(v // 1_0000_0000_0000)
    eok = int(round((v - jo * 1_0000_0000_0000) / 1_0000_0000))
    if eok >= 10000:
        jo += 1
        eok = 0
    if jo and eok:
        return f"{sign}{jo:,}조 {eok:,}억 원"
    if jo:
        return f"{sign}{jo:,}조 원"
    if eok:
        return f"{sign}{eok:,}억 원"
    return f"{sign}{v:,.0f}원"


def get_market_scale(stock_code):
    """시가총액·상장주식수 — KRX 공식값 우선, 네이버 표시값은 보조 확인용."""
    lines = []

    if krx is not None and _krx_ready():
        try:
            day = krx.get_nearest_business_day_in_a_week(END)
            df = krx.get_market_cap_by_date(day, day, stock_code)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                cap = row.get("시가총액")
                shares = row.get("상장주식수")
                amount = row.get("거래대금")
                close = row.get("종가")
                parts = [f"  [KRX 기준일] {day}"]
                if cap is not None:
                    parts.append(f"시가총액 {_format_won_compact(cap)}")
                if shares is not None:
                    parts.append(f"상장주식수 {float(shares):,.0f}주")
                if close is not None:
                    parts.append(f"기준가 {float(close):,.0f}원")
                if amount is not None:
                    parts.append(f"거래대금 {_format_won_compact(amount)}")
                lines.append(" / ".join(parts))
        except Exception as e:
            lines.append(f"  [KRX] 데이터 없음 ({type(e).__name__})")
    else:
        lines.append("  [KRX] 데이터 없음 (pykrx 미설치 또는 로그인 실패)")

    # 네이버 표시값은 화면 기준 보조 확인용이다. 키 이름이 바뀌어도 텍스트에서 최대한 찾는다.
    try:
        res = requests.get(
            f"https://m.stock.naver.com/api/stock/{stock_code}/integration",
            headers=HEADERS, timeout=6)
        res.raise_for_status()
        infos = res.json().get("totalInfos", [])
        found = []
        for it in infos:
            label = str(it.get("key") or it.get("name") or it.get("title") or it.get("code") or "").strip()
            value = str(it.get("value") or "").strip()
            if not label or not value:
                continue
            if any(k in label for k in ("시가총액", "시총", "상장주식수", "상장주식")):
                found.append(f"{label} {value}")
        if found:
            lines.append("  [네이버 표시] " + " / ".join(found[:4]))
    except Exception:
        pass

    if not lines:
        return "데이터 없음"
    lines.append("  ※ 시가총액은 원자료 표시값만 사용하십시오. 지분율·주식수로 새 금액을 환산하지 마십시오.")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# 7. 원/달러 환율
# ──────────────────────────────────────────────
def get_fx():
    if yf is None:
        return "데이터 없음 (yfinance 미설치)"
    try:
        hist = yf.Ticker("KRW=X").history(period="5d")
        if hist.empty:
            return "데이터 없음"
        lines = [f"  {d.strftime('%m/%d')}: {row['Close']:,.1f}원"
                 for d, row in hist.iterrows()]
        first, last = hist["Close"].iloc[0], hist["Close"].iloc[-1]
        trend = "원화 약세(달러 강세)" if last > first else "원화 강세(달러 약세)"
        lines.append(f"  [추세] {first:,.1f} → {last:,.1f} ({trend})")
        return "\n".join(lines)
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 8. 글로벌 크로스체크 (종목 고유 문제인지, 글로벌 동반 흐름인지 판별)
# ──────────────────────────────────────────────
def get_global_peers():
    if yf is None:
        return "데이터 없음 (yfinance 미설치)"
    lines = []
    for ticker, name in GLOBAL_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if hist.empty or len(hist) < 2:
                lines.append(f"  {name}({ticker}): 데이터 없음")
                continue
            first, last = hist["Close"].iloc[0], hist["Close"].iloc[-1]
            chg = (last / first - 1) * 100
            day_chg = (last / hist["Close"].iloc[-2] - 1) * 100
            lines.append(f"  {name}: 최근 5일 {chg:+.1f}% / 직전 거래일 {day_chg:+.1f}%")
        except Exception as e:
            lines.append(f"  {name}({ticker}): 데이터 없음 ({type(e).__name__})")
    return "\n".join(lines) if lines else "데이터 없음"


# ──────────────────────────────────────────────
# 9. DART 공시 목록
# ──────────────────────────────────────────────
def _get_corp_code(stock_code):
    if not os.path.exists(_CORP_CODE_CACHE):
        url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}"
        res = requests.get(url, timeout=20)
        res.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            with open(_CORP_CODE_CACHE, "wb") as f:
                f.write(zf.read(zf.namelist()[0]))
    tree = ET.parse(_CORP_CODE_CACHE)
    for corp in tree.getroot().iter("list"):
        if (corp.findtext("stock_code") or "").strip() == stock_code:
            return corp.findtext("corp_code")
    return None


INSIDER_KEYWORDS = [
    "임원ㆍ주요주주특정증권등소유상황보고서",
    "주식등의대량보유상황보고서",
    "자기주식",
    "최대주주",
    "유상증자", "무상증자", "전환사채",
    "단일판매ㆍ공급계약",
]


def _dart_ready():
    return bool(DART_API_KEY)


def get_dart_disclosures(stock_code, days=30):
    if not _dart_ready():
        return "데이터 없음 (DART API 키 미설정: config.txt에 DART_API_KEY 입력)"
    try:
        corp_code = _get_corp_code(stock_code)
        if not corp_code:
            return "데이터 없음 (해당 종목의 DART 고유번호를 찾지 못함)"

        bgn = (TODAY - timedelta(days=days)).strftime(FMT)
        res = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={"crtfc_key": DART_API_KEY, "corp_code": corp_code,
                    "bgn_de": bgn, "end_de": END, "page_count": 100},
            timeout=10,
        )
        data = res.json()
        if data.get("status") != "000":
            if data.get("status") == "013":
                return f"  최근 {days}일 내 공시 없음"
            return f"데이터 없음 (DART 응답 오류: {data.get('message')})"

        hits = []
        for item in data.get("list", []):
            nm = item.get("report_nm", "")
            if any(k in nm for k in INSIDER_KEYWORDS):
                hits.append(f"  {item.get('rcept_dt')}: {nm.strip()} "
                            f"(제출인: {item.get('flr_nm', '?')})")
        if not hits:
            return (f"  최근 {days}일 내 내부자/지분 관련 공시 없음 "
                    f"(내부자가 조용하다는 것 자체도 하나의 정보)")
        return "\n".join(hits[:15])
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


EARNINGS_DISCLOSURE_KEYWORDS = [
    "잠정실적", "영업실적", "영업(잠정)실적", "매출액", "영업이익", "손익구조",
    "연결재무제표", "분기보고서", "반기보고서", "사업보고서", "기업설명회",
    "IR", "실적",
]


def _fetch_dart_report_key_lines(rcept_no, max_lines=6):
    """DART 원문에서 실적 관련 핵심 줄만 짧게 추출한다."""
    if not (DART_API_KEY and rcept_no):
        return []
    try:
        res = requests.get(
            "https://opendart.fss.or.kr/api/document.xml",
            params={"crtfc_key": DART_API_KEY, "rcept_no": rcept_no},
            timeout=15,
        )
        res.raise_for_status()
        blob = res.content
        chunks = []
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for name in zf.namelist()[:5]:
                    raw = zf.read(name)
                    for enc in ("utf-8", "cp949", "euc-kr"):
                        try:
                            chunks.append(raw.decode(enc, errors="ignore"))
                            break
                        except Exception:
                            continue
        except zipfile.BadZipFile:
            chunks.append(blob.decode("utf-8", errors="ignore"))

        text = "\n".join(chunks)
        text = re.sub(r"<[^>]+>", " ", text)
        text = _clean_news_text(text)
        unit_m = re.search(r"단위\s*[:：]\s*([가-힣A-Za-z0-9/()]+)", text)
        unit = unit_m.group(1) if unit_m else "원문 표 단위"

        structured = []
        for label in ("매출액", "영업이익", "당기순이익"):
            m = re.search(
                rf"{label}\s+당해실적\s+([0-9,.\-]+)\s+([0-9,.\-]+)\s+([0-9,.\-]+)\s+[-A-Za-z가-힣]*\s+([0-9,.\-]+)\s+([0-9,.\-]+)",
                text)
            if m:
                cur, prev_q, qoq, prev_y, yoy = m.groups()
                if cur.strip("-") == "":
                    continue
                structured.append(
                    f"{label}: 당기 {cur} ({unit}), 전기 {prev_q}, 전기대비 {qoq}%, 전년동기 {prev_y}, 전년동기대비 {yoy}%"
                )
        if structured:
            return structured[:max_lines]

        # 표가 한 줄로 뭉치는 경우가 많아 키워드 주변만 잘라낸다.
        keywords = ["매출액", "영업이익", "당기순이익", "순이익", "잠정실적", "전년동기", "직전분기"]
        found = []
        for kw in keywords:
            for m in re.finditer(re.escape(kw), text):
                start = max(0, m.start() - 90)
                end = min(len(text), m.end() + 220)
                snippet = _clip_news_snippet(text[start:end], limit=260)
                # 숫자가 없는 설명 조각은 우선순위 낮음
                if not re.search(r"\d|조|억|%", snippet):
                    continue
                norm = re.sub(r"\s+", "", snippet)
                if norm and all(norm not in re.sub(r"\s+", "", x) for x in found):
                    found.append(snippet)
                if len(found) >= max_lines:
                    return found
        return found[:max_lines]
    except Exception:
        return []


def get_dart_earnings_events(stock_code, days=14):
    """DART에서 실적 발표·IR 성격 공시만 따로 추린다."""
    if not _dart_ready():
        return "데이터 없음 (DART API 키 미설정)"
    try:
        corp_code = _get_corp_code(stock_code)
        if not corp_code:
            return "데이터 없음 (해당 종목의 DART 고유번호를 찾지 못함)"

        bgn = (TODAY - timedelta(days=days)).strftime(FMT)
        res = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={"crtfc_key": DART_API_KEY, "corp_code": corp_code,
                    "bgn_de": bgn, "end_de": END, "page_count": 100,
                    "sort": "date", "sort_mth": "desc"},
            timeout=10,
        )
        data = res.json()
        if data.get("status") != "000":
            if data.get("status") == "013":
                return f"  최근 {days}일 내 실적성 공시 없음"
            return f"데이터 없음 (DART 응답 오류: {data.get('message')})"

        hits = []
        for item in data.get("list", []):
            nm = str(item.get("report_nm", "")).strip()
            if any(k.lower() in nm.lower() for k in EARNINGS_DISCLOSURE_KEYWORDS):
                rcept_no = item.get("rcept_no", "-")
                line = (f"  {item.get('rcept_dt')}: {nm} "
                        f"(제출인: {item.get('flr_nm', '?')}, 접수번호: {rcept_no})")
                key_lines = _fetch_dart_report_key_lines(rcept_no, max_lines=4)
                if key_lines:
                    line += "\n    원문 핵심: " + "\n    원문 핵심: ".join(key_lines)
                hits.append(line)
        if not hits:
            return f"  최근 {days}일 내 실적성 공시 없음"
        hits.append("  ※ 실적 공시는 제목 기준 선별입니다. 세부 매출·영업이익 숫자는 공시 원문/뉴스 원문에 있는 값만 대본에 사용하십시오.")
        return "\n".join(hits[:12])
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


def _clean_news_text(text):
    s = re.sub(r"<[^>]+>", "", str(text or ""))
    s = s.replace("&quot;", "\"").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&#39;", "'").replace("&apos;", "'").replace("&nbsp;", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clip_news_snippet(text, limit=180):
    s = _clean_news_text(text)
    if not s:
        return ""
    if len(s) <= limit:
        return s
    return s[:limit].rstrip() + "…"


def _extract_google_news_original_url(google_link):
    """Google News RSS 링크는 실제 기사 링크가 아닐 수 있어 URL 후보만 보조 추출한다."""
    link = str(google_link or "").strip()
    if not link:
        return ""
    # Google RSS URL 자체를 따라가도 리다이렉트가 안 풀리는 경우가 많다. 그래도 requests가
    # 풀 수 있는 경우가 있어, 실패하면 빈 값으로 둔다.
    try:
        res = requests.get(link, headers=HEADERS, timeout=NEWS_REDIRECT_TIMEOUT, allow_redirects=True)
        final_url = getattr(res, "url", "") or ""
        if final_url and "news.google.com" not in final_url:
            return final_url
    except Exception:
        pass
    return ""


def _fetch_article_meta(url):
    """기사 링크에서 메타 정보만 짧게 읽는다. 본문 전체 복사는 하지 않는다."""
    info = {"description": "", "published": "", "keywords": ""}
    if not url:
        return info
    try:
        res = requests.get(url, headers=HEADERS, timeout=NEWS_META_TIMEOUT)
        res.raise_for_status()
        html = res.text[:300000]
        desc_patterns = [
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        ]
        for pat in desc_patterns:
            m = re.search(pat, html, flags=re.I | re.S)
            if m:
                info["description"] = _clip_news_snippet(m.group(1), limit=260)
                break
        published_patterns = [
            r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']pubdate["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']date["\'][^>]+content=["\']([^"\']+)["\']',
            r'<time[^>]+datetime=["\']([^"\']+)["\']',
        ]
        for pat in published_patterns:
            m = re.search(pat, html, flags=re.I | re.S)
            if m:
                info["published"] = _clean_news_text(m.group(1))[:40]
                break
        kw_patterns = [
            r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+property=["\']article:tag["\'][^>]+content=["\']([^"\']+)["\']',
        ]
        for pat in kw_patterns:
            m = re.search(pat, html, flags=re.I | re.S)
            if m:
                info["keywords"] = _clip_news_snippet(m.group(1), limit=120)
                break
    except Exception:
        return info
    return info


def _fetch_article_meta_snippet(url):
    """기존 호출 호환용: 기사 메타 설명만 반환한다."""
    return _fetch_article_meta(url).get("description", "")


def _is_relevant_news_title(stock_name, title):
    """종목명이 제목에 직접 들어간 뉴스만 사용해 그룹사/우선주 오염을 줄인다."""
    stock = re.sub(r"\s+", "", str(stock_name or "")).lower()
    t = re.sub(r"\s+", "", str(title or "")).lower()
    if not stock or not t:
        return False
    if stock not in t:
        return False
    if f"{stock}우" in t and "우선주" not in t:
        return False
    return True


def get_news_events(stock_name, stock_code=None, max_items=8):
    """뉴스 헤드라인 + RSS 요약 + 가능한 경우 기사 메타 요약을 수집한다."""
    # 실적발표는 별도 슬롯으로 강제하지 않는다.
    # 일반 뉴스/주가반응 안에 실적 기사가 있으면 자연스럽게 들어오게 둔다.
    query_groups = [
        ("일반", [
            f"{stock_name} 뉴스",
            f"{stock_name}",
        ], 3),
        ("주가반응", [
            f"{stock_name} 주가 반응",
            f"{stock_name} 급등 급락",
        ], 2),
        ("수급", [
            f"{stock_name} 외국인 수급",
            f"{stock_name} 기관 순매수 순매도",
        ], 2),
        ("증권가", [
            f"{stock_name} 목표가 전망",
            f"{stock_name} 증권가 리포트",
        ], 2),
        ("업황", [
            f"{stock_name} 업황",
            f"{stock_name} 섹터 전망",
        ], 2),
        ("공시", [
            f"{stock_name} 공시",
            f"{stock_name} IR",
        ], 1),
    ]
    seen = set()
    items_out = []
    group_counts = {}
    meta_fetch_count = 0
    for group, queries, group_limit in query_groups:
        group_counts.setdefault(group, 0)
        for q in queries:
            if group_counts[group] >= group_limit:
                break
            try:
                q_recent = f"{q} when:{NEWS_LOOKBACK_DAYS}d"
                url = ("https://news.google.com/rss/search?"
                       f"q={quote_plus(q_recent)}&hl=ko&gl=KR&ceid=KR:ko")
                res = requests.get(url, headers=HEADERS, timeout=NEWS_RSS_TIMEOUT)
                res.raise_for_status()
                root = ET.fromstring(res.content)
                for item in root.findall(".//item"):
                    if group_counts[group] >= group_limit:
                        break
                    title = _clean_news_text(item.findtext("title"))
                    if not title:
                        continue
                    if not _is_relevant_news_title(stock_name, title):
                        continue
                    norm = re.sub(r"\s+", "", title)
                    if norm in seen:
                        continue
                    seen.add(norm)
                    source = _clean_news_text(item.findtext("source")) or "뉴스"
                    link = item.findtext("link") or ""
                    rss_desc = _clip_news_snippet(item.findtext("description"), limit=220)
                    article_url = ""
                    meta = {"description": "", "published": "", "keywords": ""}
                    if meta_fetch_count < NEWS_META_DETAIL_LIMIT:
                        article_url = _extract_google_news_original_url(link)
                        meta = _fetch_article_meta(article_url)
                        meta_fetch_count += 1
                    meta_desc = meta.get("description", "")
                    snippet = meta_desc or rss_desc
                    pub = item.findtext("pubDate") or ""
                    sort_dt = None
                    try:
                        dt = parsedate_to_datetime(pub)
                        if dt.tzinfo:
                            dt = dt.astimezone(KST_TZ)
                        else:
                            dt = dt.replace(tzinfo=KST_TZ)
                        sort_dt = dt
                        pub_s = dt.strftime("%Y-%m-%d %H:%M KST")
                    except Exception:
                        pub_s = pub[:25]
                    line = f"  [{group}] {pub_s}: {title} (출처: {source})"
                    if meta.get("published"):
                        line += f"\n    기사시각: {meta.get('published')}"
                    if snippet and snippet not in title:
                        line += f"\n    요약: {snippet}"
                    if meta.get("keywords"):
                        line += f"\n    키워드: {meta.get('keywords')}"
                    if article_url:
                        line += f"\n    링크: {article_url}"
                    items_out.append({"dt": sort_dt, "line": line})
                    group_counts[group] += 1
            except Exception:
                continue

    # 그래도 비어 있으면 기존 방식처럼 일반 뉴스만 한 번 더 본다.
    if not items_out:
        for q in [f"{stock_name} 뉴스"]:
            try:
                q_recent = f"{q} when:{NEWS_LOOKBACK_DAYS}d"
                url = ("https://news.google.com/rss/search?"
                       f"q={quote_plus(q_recent)}&hl=ko&gl=KR&ceid=KR:ko")
                res = requests.get(url, headers=HEADERS, timeout=NEWS_RSS_TIMEOUT)
                res.raise_for_status()
                root = ET.fromstring(res.content)
                for item in root.findall(".//item"):
                    title = _clean_news_text(item.findtext("title"))
                    if not title:
                        continue
                    if not _is_relevant_news_title(stock_name, title):
                        continue
                    norm = re.sub(r"\s+", "", title)
                    if norm in seen:
                        continue
                    seen.add(norm)
                    source = _clean_news_text(item.findtext("source")) or "뉴스"
                    pub = item.findtext("pubDate") or ""
                    sort_dt = None
                    try:
                        dt = parsedate_to_datetime(pub)
                        if dt.tzinfo:
                            dt = dt.astimezone(KST_TZ)
                        else:
                            dt = dt.replace(tzinfo=KST_TZ)
                        sort_dt = dt
                        pub_s = dt.strftime("%Y-%m-%d %H:%M KST")
                    except Exception:
                        pub_s = pub[:25]
                    line = f"  [일반] {pub_s}: {title} (출처: {source})"
                    items_out.append({"dt": sort_dt, "line": line})
                    if len(items_out) >= max_items:
                        break
            except Exception:
                continue
            if items_out:
                break
    if not items_out:
        return "데이터 없음 (뉴스 RSS 조회 실패 또는 관련 뉴스 없음)"
    items_out.sort(key=lambda x: x.get("dt") or datetime.min.replace(tzinfo=KST_TZ), reverse=True)
    rows = [
        f"  [뉴스 수집 기준] {datetime.now(KST_TZ).strftime('%Y-%m-%d %H:%M')} KST / 최근 {NEWS_LOOKBACK_DAYS}일 / Google News RSS 발행시각 최신순"
    ]
    rows.extend(item["line"] for item in items_out[:max_items])
    rows.append(f"  ※ 뉴스는 최근 {NEWS_LOOKBACK_DAYS}일 기준으로 일반·주가반응·수급·증권가·업황·공시 후보를 모은 뒤 최신순으로 정렬했습니다. 실적발표는 별도 강제 수집하지 않으며, 뉴스 제목·요약에 잡힌 경우에만 대본에 사용하십시오.")
    return "\n".join(rows)


def get_market_disruption_events(max_items=10, lookback_days=3):
    """서킷브레이커·사이드카·VI·거래정지 같은 시장 운영 이벤트를 별도 수집한다."""
    queries = [
        "코스피 서킷브레이커",
        "코스닥 서킷브레이커",
        "코스피 사이드카",
        "코스닥 사이드카",
        "프로그램매매 호가효력정지",
        "변동성완화장치 VI 발동",
        "거래정지 거래재개 코스피 코스닥",
        "코스피 급락 코스닥 급락 서킷브레이커 사이드카",
    ]
    keywords = [
        "서킷브레이커", "서킷 브레이커", "사이드카", "호가효력정지",
        "프로그램매매", "프로그램 매매", "변동성완화장치", "변동성 완화장치",
        "VI", "거래정지", "거래 재개", "거래재개", "급락",
    ]
    cutoff = datetime.now() - timedelta(days=max(1, int(lookback_days or 3)))
    seen = set()
    rows = []
    for q in queries:
        if len(rows) >= max_items:
            break
        try:
            url = ("https://news.google.com/rss/search?"
                   f"q={quote_plus(q)}&hl=ko&gl=KR&ceid=KR:ko")
            res = requests.get(url, headers=HEADERS, timeout=8)
            res.raise_for_status()
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = _clean_news_text(item.findtext("title"))
                if not title:
                    continue
                snippet = _clip_news_snippet(item.findtext("description"), limit=160)
                combined = f"{title} {snippet}"
                if not any(k in combined for k in keywords):
                    continue
                pub = item.findtext("pubDate") or ""
                pub_s = pub[:25]
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt.tzinfo:
                        dt_local = dt.astimezone(KST_TZ).replace(tzinfo=None)
                    else:
                        dt_local = dt
                    if dt_local < cutoff:
                        continue
                    pub_s = dt_local.strftime("%Y-%m-%d %H:%M KST")
                except Exception:
                    pass
                norm = re.sub(r"\s+", "", title)
                if norm in seen:
                    continue
                seen.add(norm)
                source = _clean_news_text(item.findtext("source")) or "뉴스"
                link = item.findtext("link") or ""
                article_url = _extract_google_news_original_url(link)
                meta_desc = _fetch_article_meta_snippet(article_url)
                desc = meta_desc or snippet
                line = f"  {pub_s}: {title} (출처: {source})"
                if desc and desc not in title:
                    line += f"\n    요약: {desc}"
                if article_url:
                    line += f"\n    링크: {article_url}"
                rows.append(line)
                if len(rows) >= max_items:
                    break
        except Exception:
            continue
    if not rows:
        return "데이터 없음 (최근 서킷브레이커·사이드카·VI·거래정지 관련 뉴스 감지 안 됨)"
    rows.insert(0, "  ※ 아래 시각은 뉴스/RSS 발행시각입니다. 사건 발생시각이나 오늘 발동 확정값이 아닙니다.")
    rows.insert(1, "  ※ 같은 서킷브레이커·사이드카 기사가 다음 날 재송고/후속 보도될 수 있습니다. 기사 발행일만 보고 '오늘 발동'이라고 말하지 마십시오.")
    rows.insert(2, "  ※ 장중 대본에서는 수동 메모나 공식 실시간 확인 자료에 '오늘 발동'이 명시된 경우에만 오늘 발생으로 말하십시오. 그 외에는 '전일 또는 최근 시장 특이상황'으로만 처리하십시오.")
    rows.append("  ※ 시장 특이상황은 뉴스/RSS 기준 감지입니다. 대본에는 위 제목·요약에 있는 사건만 말하고, 발동 시간·단계·지수 하락률은 원문에 있는 경우에만 사용하십시오.")
    return "\n".join(rows)


# ──────────────────────────────────────────────
# 10. DART 임원·주요주주 '상세' (매수/매도 방향 + 수량 자동 판별) ★핵심★
# ──────────────────────────────────────────────
def get_insider_detail(stock_code, days=30):
    if not _dart_ready():
        return "데이터 없음 (DART API 키 미설정: config.txt에 DART_API_KEY 입력)"
    try:
        corp_code = _get_corp_code(stock_code)
        if not corp_code:
            return "데이터 없음 (corp_code 조회 실패)"

        res = requests.get(
            "https://opendart.fss.or.kr/api/elestock.json",
            params={"crtfc_key": DART_API_KEY, "corp_code": corp_code},
            timeout=10,
        )
        data = res.json()
        if data.get("status") != "000":
            if data.get("status") == "013":
                return "  최근 임원·주요주주 소유보고 없음"
            return f"데이터 없음 (DART 응답 오류: {data.get('message')})"

        cutoff = (TODAY - timedelta(days=days)).strftime(FMT)
        buys, sells, flats = 0, 0, 0
        buy_qty, sell_qty = 0, 0
        entries = []  # (날짜, 출력줄) — 최신순 정렬용
        for item in data.get("list", []):
            rdt = str(item.get("rcept_dt", "")).replace("-", "").replace(".", "")
            if rdt < cutoff:
                continue
            who = item.get("repror", "?")
            pos = item.get("isu_exctv_ofcps", "") or item.get("isu_main_shrholdr", "")
            delta = _num(item.get("sp_stock_lmp_irds_cnt"))
            hold = _num(item.get("sp_stock_lmp_cnt"))

            if delta is None:
                direction = "변동수량 확인불가"
            elif delta > 0:
                direction = f"▲취득(매수) {delta:+,}주"
                buys += 1
                buy_qty += delta
            elif delta < 0:
                direction = f"▼처분(매도) {delta:+,}주"
                sells += 1
                sell_qty += abs(delta)
            else:
                direction = "변동 없음(신고성 보고)"
                flats += 1

            line = f"  {rdt}: {who}"
            if pos and str(pos).strip() not in ("", "-"):
                line += f"({str(pos).strip()})"
            line += f" — {direction}"
            if hold is not None:
                line += f", 보고 후 보유 {hold:,}주"
            entries.append((rdt, line))

        if not entries:
            return f"  최근 {days}일 내 상세 보고 없음"

        # 최신 보고가 잘리지 않도록 날짜 내림차순 정렬 후 상위 12건
        entries.sort(key=lambda x: x[0], reverse=True)
        lines = [e[1] for e in entries[:12]]

        summary = (f"  [요약] 최근 {days}일 전체 {len(entries)}건: "
                   f"취득 {buys}건(+{buy_qty:,}주) / "
                   f"처분 {sells}건(-{sell_qty:,}주) / 변동없음 {flats}건")
        # 취득/처분 사유가 장내매매가 아닐 수 있음(스톡옵션, 상여, 상속 등) → AI에 주의 전달
        note = ("  ※ 취득에는 장내매수 외에 스톡옵션 행사·주식 보상 등이 포함될 수 있음. "
                "대본에 '임원이 샀다'로 쓰기 전 원문 공시에서 취득 사유 확인 권장")
        return "\n".join([summary] + lines + [note])
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 11. 대량보유(5%) 보고 실명 추적 ★큰손 추격의 핵심★
#     국민연금·글로벌 운용사 등이 지분을 언제/얼마나 늘리고 줄였는지 '이름 달고' 나옴
# ──────────────────────────────────────────────
def get_major_holders(stock_code, days=90):
    if not _dart_ready():
        return "데이터 없음 (DART API 키 미설정: config.txt에 DART_API_KEY 입력)"
    try:
        corp_code = _get_corp_code(stock_code)
        if not corp_code:
            return "데이터 없음 (corp_code 조회 실패)"

        res = requests.get(
            "https://opendart.fss.or.kr/api/majorstock.json",
            params={"crtfc_key": DART_API_KEY, "corp_code": corp_code},
            timeout=10,
        )
        data = res.json()
        if data.get("status") != "000":
            if data.get("status") == "013":
                return "  최근 대량보유(5%) 보고 없음"
            return f"데이터 없음 (DART 응답 오류: {data.get('message')})"

        cutoff = (TODAY - timedelta(days=days)).strftime(FMT)
        entries = []
        for item in data.get("list", []):
            rdt = str(item.get("rcept_dt", "")).replace("-", "").replace(".", "")
            if rdt < cutoff:
                continue
            who = str(item.get("repror", "?")).strip()
            try:
                rate = float(str(item.get("stkrt", "")).replace(",", ""))
            except (ValueError, TypeError):
                rate = None
            try:
                rate_chg = float(str(item.get("stkrt_irds", "")).replace(",", ""))
            except (ValueError, TypeError):
                rate_chg = None
            reason = " ".join(str(item.get("report_resn", "")).split())

            line = f"  {rdt}: {who}"
            if rate is not None:
                line += f" — 보유비율 {rate:.2f}%"
            if rate_chg is not None and rate_chg != 0:
                arrow = "▲확대" if rate_chg > 0 else "▼축소"
                line += f" ({arrow} {rate_chg:+.2f}%p)"
            if reason:
                line += f" / 사유: {reason[:40]}"
            entries.append((rdt, line))

        if not entries:
            return (f"  최근 {days}일 내 대량보유 보고 없음 "
                    f"(5% 이상 큰손의 지분 변동이 없었다는 뜻 — 그 자체도 정보)")

        entries.sort(key=lambda x: x[0], reverse=True)
        lines = [e[1] for e in entries[:10]]
        lines.append("  ※ 보고자명으로 자금 성격 구분 가능: 국민연금=국내 최대 장기자금, "
                     "글로벌 운용사(블랙록 등)=패시브 성격 가능성, "
                     "사유가 '경영참가'면 지배구조 이슈 신호")
        return "\n".join(lines)
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 12. 프로그램 매매, KOSPI 시장 전체 (네이버, 실험적)
#     차익 = 기계적 수급 / 비차익 = 종목을 보고 들어온 '진짜 돈'에 가까움
# ──────────────────────────────────────────────
def get_program_trading():
    if pd is None:
        return "데이터 없음 (pandas 미설치)"
    try:
        base = "https://finance.naver.com"
        nv_headers = {**HEADERS, "Referer": "https://finance.naver.com/"}

        res = requests.get(base + "/sise/programDeal.naver",
                           headers=nv_headers, timeout=8)
        res.encoding = "euc-kr"

        # 네이버 금융은 실제 표가 iframe 안쪽 페이지에 있는 경우가 많음
        # → 겉 페이지 + iframe 안쪽 페이지들을 모두 후보로 모아 표를 탐색
        candidates = [res.text]
        for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', res.text, re.I):
            src = m.group(1)
            if src.startswith("/"):
                src = base + src
            elif not src.startswith("http"):
                continue
            try:
                r2 = requests.get(src, headers=nv_headers, timeout=8)
                r2.encoding = "euc-kr"
                candidates.append(r2.text)
            except requests.RequestException:
                continue

        target = None
        for doc in candidates:
            try:
                tables = pd.read_html(io.StringIO(doc))
            except ValueError:  # 해당 문서에 표 없음 → 다음 후보
                continue
            for t in tables:
                if t.shape[0] >= 3 and t.shape[1] >= 5:
                    target = t
                    break
            if target is not None:
                break

        if target is None:
            return "데이터 없음 (표 구조 인식 실패 — 페이지 개편 가능성)"

        target = target.dropna(how="all").head(5)
        lines = ["  (KOSPI 시장 전체 기준, 단위는 네이버 표기 그대로)"]
        for _, row in target.iterrows():
            vals = [str(v) for v in row.tolist() if str(v) != "nan"]
            if vals:
                lines.append("  " + " | ".join(vals[:7]))
        lines.append("  ※ 실험적 수집: 값이 이상하면 finance.naver.com/sise/programDeal 에서 직접 확인")
        return "\n".join(lines)
    except Exception as e:
        return f"데이터 없음 (실험적 기능 실패: {type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 13. 신용융자 잔고, 시장 전체 (금융투자협회, 실험적)
#     빚투 규모. 급증 상태에서의 하락 = 반대매매 연쇄 리스크
# ──────────────────────────────────────────────
def get_margin_loan():
    try:
        url = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
        payload = {
            "dmSearch": {
                "tmpV40": "1000000",
                "tmpV41": "1",
                "tmpV1": "D",
                "tmpV45": (TODAY - timedelta(days=14)).strftime(FMT),
                "tmpV46": END,
                "OBJ_NM": "STATSCU0100000060BO",
            }
        }
        res = requests.post(url, json=payload, timeout=10, headers={
            "User-Agent": HEADERS["User-Agent"],
            "Referer": "https://freesis.kofia.or.kr/",
            "Content-Type": "application/json",
        })
        rows = res.json().get("ds1", [])
        if not rows:
            return "데이터 없음 (조회 결과 없음)"

        lines = ["  (시장 전체 신용융자 잔고, 단위: 백만원 추정 — 최초 1회 금투협 사이트와 대조 권장)"]
        for row in rows[:5]:
            d = row.get("TMPV1", "?")
            total = row.get("TMPV2", "?")
            lines.append(f"  {d}: 융자잔고 {total}")
        try:
            first = float(str(rows[-1].get("TMPV2", "0")).replace(",", ""))
            last = float(str(rows[0].get("TMPV2", "0")).replace(",", ""))
            if first > 0:
                lines.append(f"  [추세] 기간 내 {(last/first-1)*100:+.1f}% "
                             f"({'빚투 증가' if last > first else '빚투 축소'})")
        except (ValueError, ZeroDivisionError):
            pass
        lines.append("  ※ 실험적 수집: 값이 이상하면 freesis.kofia.or.kr 에서 직접 확인")
        return "\n".join(lines)
    except Exception as e:
        return f"데이터 없음 (실험적 기능 실패: {type(e).__name__}: {e})"


# ──────────────────────────────────────────────
# 시장 스캐너: '오늘의 소재' 발굴용
# 종목을 정하기 전에, 시장 전체에서 이상 신호가 뜬 곳을 찾는다
# ──────────────────────────────────────────────
def scan_market_candidates():
    lines = [f"[소재 스캔] {TODAY.strftime('%Y-%m-%d %H:%M')} 기준",
             "아래에서 이상 신호가 보이는 종목을 골라 '자료 수집'을 돌리세요.", ""]

    # (1) 투자자별 순매수 랭킹 (직전 영업일)
    if krx is None or not _krx_ready():
        lines.append("● 수급 랭킹: KRX 로그인 미설정으로 스캔 불가")
    else:
        try:
            # 당일 수급은 저녁에 확정되므로, 항상 '직전 영업일'의 확정치를 조회
            base = (TODAY - timedelta(days=1)).strftime(FMT)
            day = krx.get_nearest_business_day_in_a_week(base)
            lines.append(f"(수급 랭킹 기준일: {day} 직전 영업일 확정치)")
            lines.append("")
            for inv in ["연기금", "외국인", "기관합계"]:
                try:
                    df = krx.get_market_net_purchases_of_equities_by_ticker(
                        day, day, "KOSPI", inv)
                    if df is None or df.empty:
                        lines.append(f"● {inv}: 데이터 없음")
                        continue
                    col = ("순매수거래대금" if "순매수거래대금" in df.columns
                           else df.columns[-1])
                    lines.append(f"● {inv} 순매수 TOP5 ({day})")
                    for t, row in df.sort_values(col, ascending=False).head(5).iterrows():
                        nm = row.get("종목명", t)
                        lines.append(f"    {nm}({t}): {row[col]/1e8:+,.0f}억")
                    lines.append(f"● {inv} 순매도 TOP3")
                    for t, row in df.sort_values(col).head(3).iterrows():
                        nm = row.get("종목명", t)
                        lines.append(f"    {nm}({t}): {row[col]/1e8:+,.0f}억")
                    lines.append("")
                except Exception as e:
                    lines.append(f"● {inv}: 스캔 실패 ({type(e).__name__})")
        except Exception as e:
            lines.append(f"● 수급 랭킹 스캔 실패: {type(e).__name__}: {e}")

    # (2) 최근 3일, 전 시장 내부자/대량보유 공시 (유가증권시장)
    if not _dart_ready():
        lines.append("● 공시 스캔: DART 키 미설정")
    else:
        try:
            bgn = (TODAY - timedelta(days=3)).strftime(FMT)
            res = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={"crtfc_key": DART_API_KEY, "bgn_de": bgn, "end_de": END,
                        "corp_cls": "Y", "page_count": 100,
                        "sort": "date", "sort_mth": "desc"},
                timeout=15,
            )
            data = res.json()
            hits = []
            if data.get("status") == "000":
                for item in data.get("list", []):
                    nm = item.get("report_nm", "")
                    if ("임원ㆍ주요주주" in nm) or ("대량보유" in nm):
                        kind = "내부자" if "임원" in nm else "대량보유(5%)"
                        hits.append((str(item.get("rcept_dt", "")),
                                     f"    {item.get('rcept_dt')}: "
                                     f"{item.get('corp_name', '?')} — {kind} "
                                     f"(제출: {item.get('flr_nm', '?')})"))
            if hits:
                hits.sort(key=lambda x: x[0], reverse=True)  # 최신 공시가 잘리지 않게
                lines.append(f"● 최근 3일 내부자/대량보유 공시가 뜬 회사 ({len(hits)}건 중 최대 15건, 최신순)")
                lines.extend(h[1] for h in hits[:15])
            else:
                lines.append("● 최근 3일 내부자/대량보유 공시: 없음 또는 조회 실패")
        except Exception as e:
            lines.append(f"● 공시 스캔 실패: {type(e).__name__}: {e}")

    lines.append("")
    lines.append("[소재 고르는 법] 반직관 신호를 찾으세요: 폭락장에 연기금 매수 상위에 뜬 종목, "
                 "급등한 종목의 순매도 상위 등장, 공시가 몰린 회사. "
                 "그게 오늘의 '이면'입니다.")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# 텔레그램 알림: 수집 데이터의 핵심 신호만 추려 전송
# ──────────────────────────────────────────────
def make_telegram_digest(stock_name, stock_code, raw_data):
    """수집 데이터에서 알림용 핵심 라인만 추출 (팩트만, 해석·타점 없음)"""
    secs, cur = {}, None
    for line in raw_data.splitlines():
        m = re.match(r"■ (\d+)\.", line.strip())
        if m:
            cur = int(m.group(1))
            secs[cur] = []
        elif cur is not None:
            secs[cur].append(line)

    def pick(n, marker=None):
        for l in secs.get(n, []):
            ls = l.strip()
            if not ls or ls.startswith("데이터 없음"):
                continue
            if marker is None or marker in ls:
                return ls
        return None

    parts = [f"📊 {stock_name}({stock_code}) 데이터 알림",
             TODAY.strftime("%m/%d %H:%M") + " 기준", ""]
    for label, val in [
        ("가격", pick(1)),
        ("수급(5일 누적)", pick(3, "[5일 누적]")),
        ("공매도 잔고", pick(4, "[잔고]")),
        ("외국인 지분율", pick(5, "[추세]")),
        ("내부자(30일)", pick(10, "[요약]")),
        ("대량보유 최신", pick(11)),
    ]:
        if val:
            # 알림에선 긴 부연설명 꼬리표 제거
            val = val.split("—")[0].split("※")[0].strip()
            parts.append(f"• {label}: {val}")

    parts.append("")
    parts.append("※ 데이터 기록 알림입니다. 매매 추천이 아니며, 투자 판단과 책임은 본인에게 있습니다.")
    return "\n".join(parts)


def send_telegram(text):
    """텔레그램으로 텍스트 전송. (성공여부, 메시지) 반환"""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False, ("텔레그램 미설정: config.txt에 TELEGRAM_BOT_TOKEN과 "
                       "TELEGRAM_CHAT_ID를 입력하세요 (설정법은 config.txt 주석 참고)")
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
        body = res.json()
        if body.get("ok"):
            return True, "텔레그램 전송 완료"
        return False, f"전송 실패: {body.get('description', res.text[:120])}"
    except Exception as e:
        return False, f"전송 실패: {type(e).__name__}: {e}"


# ──────────────────────────────────────────────
# 13. 전체 조립
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# 미국 주식 수집 (티커가 영문이면 자동으로 이 경로 사용)
# 데이터 소스: yfinance (가격/공매도 잔량/Form 4 내부자/13F 기관 보유)
# ──────────────────────────────────────────────
def _is_us_ticker(code):
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z.\-]{0,6}", str(code).strip()))


def _us_price_block(t):
    try:
        hist = t.history(period="10d")
        if hist is None or len(hist) < 2:
            return "데이터 없음", "데이터 없음"
        last = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2]
        chg = (last / prev - 1) * 100
        head = f"종가 {last:,.2f}달러 / 직전 거래일 대비 {chg:+.2f}% (미국 동부시간 기준 최근 마감)"

        rows = []
        tail = hist.tail(5)
        for d, row in tail.iterrows():
            rows.append(f"  {d.strftime('%m/%d')}: 종가 {row['Close']:,.2f}달러 / "
                        f"거래량 {row['Volume']:,.0f}주")
        if len(tail) >= 2 and tail["Volume"].iloc[:-1].mean() > 0:
            ratio = tail["Volume"].iloc[-1] / tail["Volume"].iloc[:-1].mean() * 100
            rows.append(f"  [거래량] 최근일이 직전 4일 평균의 {ratio:.0f}% 수준")
        try:
            h1y = t.history(period="1y")
            hi, lo = h1y["High"].max(), h1y["Low"].min()
            cur = h1y["Close"].iloc[-1]
            pos = (cur - lo) / (hi - lo) * 100 if hi > lo else 0
            rows.append(f"  [52주] 최고 {hi:,.2f} / 최저 {lo:,.2f} / "
                        f"밴드 하단에서 {pos:.0f}% 위치 (고점대비 {(cur/hi-1)*100:+.1f}%)")
        except Exception:
            pass
        return head, "\n".join(rows)
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__})", "데이터 없음"


def _us_short_block(info):
    try:
        parts = []
        ss = info.get("sharesShort")
        if ss:
            parts.append(f"공매도 잔량 {ss:,.0f}주")
        spf = info.get("shortPercentOfFloat")
        if spf:
            parts.append(f"유통주식 대비 {spf*100:.2f}%")
        sr = info.get("shortRatio")
        if sr:
            parts.append(f"커버까지 {sr:.1f}일치 거래량 (숏 비율)")
        if not parts:
            return "데이터 없음"
        return ("  " + " / ".join(parts) +
                "\n  ※ 숏 인터레스트는 격주 집계라 1~2주 지연됨. "
                "잔량은 반드시 되사야 하는 물량(숏스퀴즈 연료)")
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__})"


def _format_usd_compact(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000_000_000:
        return f"{sign}${v / 1_000_000_000_000:.2f}T"
    if v >= 1_000_000_000:
        return f"{sign}${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.2f}M"
    return f"{sign}${v:,.0f}"


def _us_market_scale_block(info):
    lines = []
    market_cap = info.get("marketCap")
    shares = info.get("sharesOutstanding")
    float_shares = info.get("floatShares")
    enterprise_value = info.get("enterpriseValue")
    currency = info.get("currency") or "USD"
    if market_cap:
        lines.append(f"시가총액 {_format_usd_compact(market_cap)} ({currency}, yfinance 표시 기준)")
    if enterprise_value:
        lines.append(f"기업가치(EV) {_format_usd_compact(enterprise_value)} ({currency}, yfinance 표시 기준)")
    if shares:
        lines.append(f"발행주식수 {float(shares):,.0f}주")
    if float_shares:
        lines.append(f"유통주식수 {float(float_shares):,.0f}주")
    if not lines:
        return "데이터 없음"
    lines.append("※ 미국 종목 시총은 yfinance 표시값 기준입니다. 환율 환산 원화 금액은 원자료에 없으면 만들지 마십시오.")
    return "\n".join("  " + line for line in lines)


def _us_insider_block(t):
    """Form 4 내부자 거래 — 미국판 DART. 임원 매매가 2영업일 내 공시됨"""
    try:
        df = t.insider_transactions
        if df is None or len(df) == 0:
            return "  최근 내부자 거래 보고 없음"
        buys = sells = 0
        lines = []
        for _, row in df.head(12).iterrows():
            who = str(row.get("Insider", "?")).strip()
            pos = str(row.get("Position", "") or "").strip()
            txt = str(row.get("Transaction", "") or row.get("Text", "") or "").strip()
            shares = row.get("Shares", None)
            date = row.get("Start Date", "")
            date = date.strftime("%m/%d") if hasattr(date, "strftime") else str(date)[:10]
            low = txt.lower()
            if "purchase" in low or "buy" in low:
                tag = "▲매수"
                buys += 1
            elif "sale" in low or "sell" in low:
                tag = "▼매도"
                sells += 1
            else:
                tag = "기타(옵션행사/증여 등)"
            line = f"  {date}: {who}"
            if pos:
                line += f"({pos})"
            line += f" — {tag}"
            if shares is not None:
                try:
                    line += f" {float(shares):,.0f}주"
                except (ValueError, TypeError):
                    pass
            lines.append(line)
        summary = f"  [요약] 최근 보고 중 매수 {buys}건 / 매도 {sells}건 (표시된 {len(lines)}건 기준)"
        note = ("  ※ Form 4 기준. '기타'에는 스톡옵션 행사·증여가 섞이므로 "
                "매수/매도로 단정하기 전 유형 확인 권장")
        return "\n".join([summary] + lines + [note])
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__}: {e})"


def _us_holders_block(t):
    try:
        df = t.institutional_holders
        if df is None or len(df) == 0:
            return "데이터 없음"
        lines = []
        for _, row in df.head(5).iterrows():
            holder = str(row.get("Holder", "?")).strip()
            pct = row.get("pctHeld", row.get("% Out", None))
            shares = row.get("Shares", None)
            line = f"  {holder}"
            if pct is not None:
                try:
                    line += f" — 지분 {float(pct)*100:.2f}%"
                except (ValueError, TypeError):
                    pass
            if shares is not None:
                try:
                    line += f" ({float(shares):,.0f}주)"
                except (ValueError, TypeError):
                    pass
            lines.append(line)
        lines.append("  ※ 13F 기준 — 분기 보고라 최대 45일+ 지연. 현재 보유와 다를 수 있음")
        return "\n".join(lines)
    except Exception as e:
        return f"데이터 없음 ({type(e).__name__})"


def build_raw_data_us(stock_name, ticker):
    ticker = str(ticker).strip().upper()
    if yf is None:
        return f"[종목] {stock_name} ({ticker})\n데이터 없음 (yfinance 미설치: pip install yfinance)"

    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    price_head, price_trend = _us_price_block(t)

    per = info.get("trailingPE")
    pbr = info.get("priceToBook")
    valuation = []
    if per:
        valuation.append(f"PER {per:.1f}배")
    if pbr:
        valuation.append(f"PBR {pbr:.2f}배")
    fper = info.get("forwardPE")
    if fper:
        valuation.append(f"선행 PER {fper:.1f}배")
    valuation = " / ".join(valuation) if valuation else "데이터 없음"

    return f"""
[수집 시각] {TODAY.strftime('%Y-%m-%d %H:%M')} KST
[종목] {stock_name} ({ticker}) — 미국 상장
[중요] 미국 종목에는 한국식 투자자별 일별 수급(연기금/외국인/개인)과 DART 공시가 존재하지 않는다.
대본에서 해당 데이터를 아는 것처럼 언급하지 말 것. 대신 내부자 거래(Form 4)·공매도 잔량·기관 보유(13F)를 활용할 것.

■ 1. 현재가/등락 (yfinance)
{price_head}

■ 2. 가격·거래량 추이 및 52주 위치
{price_trend}

■ 2-1. 시가총액·주식수 규모
{_us_market_scale_block(info)}

■ 4. 공매도 잔량 (Short Interest)
{_us_short_block(info)}

■ 6. 밸류에이션
{valuation}

■ 7. 원/달러 환율 최근 5일
{get_fx()}

■ 8. 글로벌 크로스체크
{get_global_peers()}

■ 9. 주요 뉴스·실적 이슈 (헤드라인)
{get_news_events(stock_name, ticker)}

■ 10. 내부자 거래 (SEC Form 4) — 미국판 임원 지분변동
{_us_insider_block(t)}

■ 11. 주요 기관 보유 (13F)
{_us_holders_block(t)}

■ 13. 수동 조사 메모
{MANUAL_NOTES.strip()}
"""


# ──────────────────────────────────────────────
# 시스템 진단: 키·계정·라이브러리·네트워크 한 번에 점검
# ──────────────────────────────────────────────
def run_diagnostics():
    lines = [f"[시스템 진단] {TODAY.strftime('%Y-%m-%d %H:%M')}", ""]

    def mark(ok, name, detail=""):
        icon = "✓" if ok else ("✗" if ok is False else "•")
        lines.append(f"  {icon} {name}" + (f" — {detail}" if detail else ""))

    lines.append("● 라이브러리")
    mark(krx is not None, "pykrx", "" if krx else "pip install pykrx")
    mark(yf is not None, "yfinance", "" if yf else "pip install yfinance")
    mark(pd is not None, "pandas", "" if pd else "pip install pandas")
    try:
        import openai  # noqa: F401
        mark(True, "openai")
    except ImportError:
        mark(False, "openai", "pip install openai")

    lines.append("")
    lines.append("● 인증 정보 (config.txt)")
    mark(bool(KRX_ID and KRX_PW), "KRX 아이디/비밀번호",
         "" if (KRX_ID and KRX_PW) else "data.krx.co.kr 가입 후 입력")
    mark(bool(DART_API_KEY), "DART API 키",
         f"길이 {len(DART_API_KEY)}자" if DART_API_KEY else "opendart.fss.or.kr 발급")
    mark(bool(OPENAI_API_KEY), "OpenAI API 키", "")
    mark(bool(OPENAI_TEXT_MODEL), "OpenAI 텍스트 모델", OPENAI_TEXT_MODEL or "")
    mark(bool(OPENAI_IMAGE_MODEL), "OpenAI 이미지 모델", OPENAI_IMAGE_MODEL or "")
    mark(bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID), "텔레그램", "선택사항")

    lines.append("")
    lines.append("● 실제 연결 테스트")
    # 네이버 시세
    try:
        r = requests.get(
            "https://polling.finance.naver.com/api/realtime/domestic/stock/005930",
            headers=HEADERS, timeout=6)
        ok = r.status_code == 200 and "datas" in r.text
        mark(ok, "네이버 시세 API", f"HTTP {r.status_code}")
    except Exception as e:
        mark(False, "네이버 시세 API", type(e).__name__)
    # DART
    if DART_API_KEY:
        try:
            r = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={"crtfc_key": DART_API_KEY, "corp_code": "00126380",
                        "bgn_de": (TODAY - timedelta(days=7)).strftime(FMT),
                        "end_de": END, "page_count": 1},
                timeout=8)
            st = r.json().get("status", "?")
            if st in ("000", "013"):
                mark(True, "DART API", f"응답 코드 {st}")
            else:
                mark(False, "DART API",
                     f"응답 {st}: {r.json().get('message', '')} (020=키 무효)")
        except Exception as e:
            mark(False, "DART API", type(e).__name__)
    # 텔레그램
    if TELEGRAM_BOT_TOKEN:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=8)
            ok = r.json().get("ok", False)
            mark(ok, "텔레그램 봇",
                 r.json().get("result", {}).get("username", "") if ok else "토큰 무효")
        except Exception as e:
            mark(False, "텔레그램 봇", type(e).__name__)
    # KRX (라이브: 삼성전자 하루치 시세 — 로그인까지 실검증)
    if krx is not None and _krx_ready():
        try:
            base = (TODAY - timedelta(days=1)).strftime(FMT)
            day = krx.get_nearest_business_day_in_a_week(base)
            df = krx.get_market_ohlcv_by_date(day, day, "005930")
            mark(df is not None and not df.empty, "KRX 데이터 조회(로그인)",
                 f"{day} 기준")
        except Exception as e:
            mark(False, "KRX 데이터 조회(로그인)",
                 f"{type(e).__name__} — 아이디/비번 확인")

    lines.append("")
    lines.append("✗ 항목을 해결한 뒤 다시 진단하세요. 전부 ✓면 정상입니다.")
    return "\n".join(lines)


_RAW_CACHE = {}  # {종목코드: (수집시각, raw_data)} — 같은 종목 반복 수집 시 API 절약


def build_raw_data(stock_name, stock_code, force=False):
    """수집 데이터 생성. force=True면 캐시를 무시하고 실시간으로 다시 조회한다."""
    # 오래 켜 둔 UI에서도 조회 시각과 기간은 호출 순간을 기준으로 다시 잡는다.
    # force=True인 UI 수집은 아래 캐시 검사도 반드시 우회한다.
    _refresh_time_context()
    try:
        ttl_min = float(_cfg.get("CACHE_MIN", 10))
    except (ValueError, TypeError):
        ttl_min = 10
    key = str(stock_code).strip().upper()
    hit = _RAW_CACHE.get(key)
    if (not force and ttl_min > 0 and hit
            and (datetime.now() - hit[0]).total_seconds() < ttl_min * 60):
        return hit[1]

    if _is_us_ticker(stock_code):
        data = build_raw_data_us(stock_name, stock_code)
        _RAW_CACHE[key] = (datetime.now(), data)
        return data
    data = f"""
[수집 시각] {TODAY.strftime('%Y-%m-%d %H:%M')} KST
{get_market_phase_guidance()}
[종목] {stock_name} ({stock_code})

■ 1. 현재가/등락 (네이버 실시간)
{get_price(stock_code)}

■ 1-1. 시간대별 가격 흐름 (네이버 차트 분봉, 실험적)
{get_intraday_price_timeline(stock_code)}

■ 2. 가격·거래대금 추이 및 52주 위치 (KRX)
{get_price_volume_trend(stock_code)}

■ 2-1. 시가총액·상장주식수 (KRX 공식)
{get_market_scale(stock_code)}

■ 3. 투자자별 세부 수급, 최근 5영업일 (KRX 공식, 순매수 금액)
{get_investor_flows(stock_code)}

■ 4. 공매도 거래 및 잔고 (KRX 공식)
{get_short_selling(stock_code)}

■ 5. 외국인 지분율 추이 (KRX 공식)
{get_foreign_ownership(stock_code)}

■ 6. 밸류에이션
{get_fundamentals(stock_code)}

■ 7. 원/달러 환율 최근 5일
{get_fx()}

■ 8. 글로벌 크로스체크 (동반 흐름 vs 종목 고유 문제 판별)
{get_global_peers()}

■ 8-1. 시장 특이상황 (서킷브레이커·사이드카·VI·거래정지)
{get_market_disruption_events()}

■ 9. DART 주요 공시 목록, 최근 30일
{get_dart_disclosures(stock_code)}

■ 9-1. 주요 뉴스 헤드라인
{get_news_events(stock_name, stock_code)}

■ 10. DART 임원·주요주주 상세 (매수/매도 방향·수량)
{get_insider_detail(stock_code)}

■ 11. 대량보유(5%) 보고 실명 추적, 최근 90일 (국민연금·글로벌 운용사 등 큰손)
{get_major_holders(stock_code)}

■ 12. 신용융자 잔고 (시장 전체, 실험적)
{get_margin_loan()}

■ 13. 수동 조사 메모
{MANUAL_NOTES.strip()}
"""
    _RAW_CACHE[key] = (datetime.now(), data)
    return data


def get_weekly_news_headlines(stock_name, stock_code=None, max_items=10):
    """주말 사전작성용: 종목/업종 주간 뉴스 헤드라인만 수집한다."""
    queries = [
        f"{stock_name} 이번 주",
        f"{stock_name} 실적 공시 뉴스",
        f"{stock_name} 외국인 수급 뉴스",
        f"{stock_name} 다음 주 일정",
    ]
    if stock_code:
        queries.append(f"{stock_name} {stock_code} 뉴스")
    seen = set()
    entries = []
    for q in queries:
        try:
            q_recent = f"{q} when:14d"
            url = ("https://news.google.com/rss/search?"
                   f"q={quote_plus(q_recent)}&hl=ko&gl=KR&ceid=KR:ko")
            res = requests.get(url, headers=HEADERS, timeout=NEWS_RSS_TIMEOUT)
            res.raise_for_status()
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = _clean_news_text(item.findtext("title"))
                if not title:
                    continue
                norm = re.sub(r"\s+", "", title)
                if norm in seen:
                    continue
                seen.add(norm)
                source = _clean_news_text(item.findtext("source")) or "뉴스"
                pub = item.findtext("pubDate") or ""
                sort_dt = None
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt.tzinfo:
                        dt = dt.astimezone(KST_TZ)
                    sort_dt = dt
                    pub_s = dt.strftime("%Y-%m-%d %H:%M KST")
                except Exception:
                    pub_s = pub[:25]
                snippet = _clip_news_snippet(item.findtext("description"), limit=150)
                line = f"  {pub_s}: {title} (출처: {source})"
                if snippet and snippet not in title:
                    line += f"\n    요약: {snippet}"
                entries.append((sort_dt, line))
        except Exception:
            continue
    if not entries:
        return "데이터 없음 (주간 뉴스 헤드라인 조회 실패 또는 관련 뉴스 없음)"
    entries.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=KST_TZ), reverse=True)
    rows = [line for _, line in entries[:max_items]]
    rows.append("  ※ 주간 뉴스는 최근 14일 헤드라인 기준 큰 주제 후보입니다. 기사에 없는 숫자·결론을 만들지 마십시오.")
    return "\n".join(rows)


def get_next_week_schedule(stock_name=None, stock_code=None, max_items=10):
    """주말 사전작성용: 다음 주 일정/실적/경제지표 후보를 헤드라인 기준으로 수집한다."""
    queries = [
        "다음 주 경제지표 일정 한국 미국 FOMC CPI PPI 고용",
        "다음 주 증시 일정 실적 발표",
        f"{stock_name} 다음 주 실적 발표 일정" if stock_name else "",
        f"{stock_name} IR 일정 공시" if stock_name else "",
    ]
    seen = set()
    rows = []
    cutoff = datetime.now() - timedelta(days=30)
    for q in [x for x in queries if x]:
        if len(rows) >= max_items:
            break
        try:
            q_recent = f"{q} when:30d"
            url = ("https://news.google.com/rss/search?"
                   f"q={quote_plus(q_recent)}&hl=ko&gl=KR&ceid=KR:ko")
            res = requests.get(url, headers=HEADERS, timeout=NEWS_RSS_TIMEOUT)
            res.raise_for_status()
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = _clean_news_text(item.findtext("title"))
                if not title:
                    continue
                norm = re.sub(r"\s+", "", title)
                if norm in seen:
                    continue
                seen.add(norm)
                source = _clean_news_text(item.findtext("source")) or "뉴스"
                pub = item.findtext("pubDate") or ""
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt.tzinfo:
                        dt = dt.astimezone(KST_TZ)
                    if dt.replace(tzinfo=None) < cutoff:
                        continue
                    pub_s = dt.strftime("%Y-%m-%d %H:%M KST")
                except Exception:
                    pub_s = pub[:25]
                rows.append(f"  {pub_s}: {title} (출처: {source})")
                if len(rows) >= max_items:
                    break
        except Exception:
            continue
    if not rows:
        return "데이터 없음 (다음 주 일정/경제지표 헤드라인 조회 실패)"
    rows.append("  ※ 다음 주 일정은 헤드라인 기준 후보입니다. 날짜·시간·수치가 제목에 없으면 대본에서 만들지 마십시오.")
    return "\n".join(rows)


def build_weekend_raw_data(stock_name, stock_code, force=False):
    """주말 사전작성 전용 자료. 당일 시세·분봉·당일 수급을 빼고 큰 주제 후보만 모은다."""
    _refresh_time_context()
    stock_code = str(stock_code or "").strip().upper()
    dart_block = get_dart_earnings_events(stock_code) if stock_code.isdigit() else "미국/티커 종목은 DART 대상 아님"
    return f"""
[수집 시각] {TODAY.strftime('%Y-%m-%d %H:%M')} KST
[시장 단계] 주말용 사전작성 자료 수집
[자료 성격] 주말 대본용 큰그림 자료 — 당일 시세 브리핑용 아님
[중요]
- 이 자료는 화요일·수요일·목요일에 주말 대본을 미리 쓰는 상황을 전제로 한다.
- 현재 수집 시점의 가격·수급은 주말 기준 최신 확정 자료가 아니므로 일부러 제외했다.
- 주말 기준 최신 시장 자료는 금요일 확정 데이터다. 금요일 확정 전에는 이번 주 결론을 낸 것처럼 쓰지 마라.
- 이 자료의 목적은 주간 뉴스 헤드라인, 다음 주 일정/실적발표/경제지표, 그리고 "이번 주가 남긴 질문"을 잡는 것이다.
- 대본에서는 오늘 장중 흐름, 오늘 현재가, 오늘 수급, 오늘 종가를 아는 것처럼 말하지 마라.

[종목] {stock_name} ({stock_code})

■ 1. 주간 뉴스 헤드라인 / 큰 주제 후보
{get_weekly_news_headlines(stock_name, stock_code)}

■ 2. 다음 주 일정·실적발표·경제지표 후보
{get_next_week_schedule(stock_name, stock_code)}

■ 3. DART 실적·IR·공시 후보
{dart_block}

■ 4. 시장 전체 특이상황 후보
{get_market_disruption_events(max_items=6, lookback_days=7)}

■ 5. 글로벌 크로스체크 후보
{get_global_peers()}

■ 6. 환율 큰 흐름 후보
{get_fx()}

■ 7. 이번 주가 남긴 질문
  - 주간 뉴스 헤드라인과 다음 주 일정에서 반복되는 큰 질문만 뽑으십시오.
  - 예: 실적 기대가 이미 주가에 반영됐는가, 외국인 자금이 왜 움직이는가, 환율이 반도체 대형주에 부담인지 완화 요인인지, 다음 주 일정이 돈의 방향을 바꿀 수 있는가.
  - 대본은 확정되지 않은 주간 결론이나 당일 시세 복기가 아니라, 주말에 볼 큰 질문 중심으로 쓰십시오.

■ 8. 수동 조사 메모
{MANUAL_NOTES.strip()}
"""


# ──────────────────────────────────────────────
# AI 리포트 생성용 프롬프트
# ──────────────────────────────────────────────

def get_information_news_headlines(stock_name, stock_code=None, max_items=12):
    """숨은정보형 전용: 날짜·시황보다 사업/산업/전략 이슈 중심 뉴스 후보를 수집한다."""
    queries = [
        f"{stock_name} 사업 전략",
        f"{stock_name} 실적 사업부",
        f"{stock_name} 투자 신사업",
        f"{stock_name} IR 공시",
        f"{stock_name} 산업 전망",
    ]
    if stock_code:
        queries.append(f"{stock_name} {stock_code} 기업 분석")
    seen = set()
    entries = []
    for q in queries:
        try:
            q_recent = f"{q} when:90d"
            url = ("https://news.google.com/rss/search?"
                   f"q={quote_plus(q_recent)}&hl=ko&gl=KR&ceid=KR:ko")
            res = requests.get(url, headers=HEADERS, timeout=NEWS_RSS_TIMEOUT)
            res.raise_for_status()
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = _clean_news_text(item.findtext("title"))
                desc = _clip_news_snippet(item.findtext("description"), limit=180)
                if not title:
                    continue
                combined = f"{title} {desc}"
                if stock_name and stock_name not in combined and (not stock_code or stock_code not in combined):
                    continue
                if re.search(r"(급등|급락|상승|하락|마감|장중|현재가|종가|코스피|코스닥|순매수|순매도|목표가|투자의견)", title):
                    continue
                norm = re.sub(r"\s+", "", title)
                if norm in seen:
                    continue
                seen.add(norm)
                source = _clean_news_text(item.findtext("source")) or "뉴스"
                pub = item.findtext("pubDate") or ""
                sort_dt = None
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt.tzinfo:
                        dt = dt.astimezone(KST_TZ)
                    sort_dt = dt
                    pub_s = dt.strftime("%Y-%m-%d %H:%M KST")
                except Exception:
                    pub_s = pub[:25]
                snippet = desc
                line = f"  {pub_s}: {title} (출처: {source})"
                if snippet and snippet not in title:
                    line += f"\n    요약: {snippet}"
                entries.append((sort_dt, line))
        except Exception:
            continue
    if not entries:
        return "데이터 없음 (숨은정보형 뉴스 헤드라인 조회 실패 또는 관련 뉴스 없음)"
    entries.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=KST_TZ), reverse=True)
    rows = [line for _, line in entries[:max_items]]
    rows.append("  ※ 숨은정보형 뉴스는 최근 90일 헤드라인 기준입니다. 제목·요약에 없는 세부 수치나 결론을 만들지 마십시오.")
    return "\n".join(rows)


def _info_scale_block(stock_code):
    """숨은정보형에서 가격/거래대금은 빼고 규모 정보만 남긴다."""
    raw = get_market_scale(stock_code)
    rows = []
    for line in str(raw or "").splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(" / ")]
        kept = [p for p in parts if not re.search(r"(기준가|거래대금|종가|현재가|시가|고가|저가)", p)]
        if kept:
            rows.append(" / ".join(kept))
    return "\n".join(rows) if rows else "데이터 없음"


def build_information_raw_data(stock_name, stock_code, force=False):
    """날짜 무관 숨은정보형 전용 자료. 가격·시황 브리핑 재료를 빼고 기업/산업/공시 중심으로 모은다."""
    _refresh_time_context()
    stock_code = str(stock_code or "").strip().upper()
    is_kr = stock_code.isdigit()
    dart_block = get_dart_earnings_events(stock_code, days=180) if is_kr else "미국/티커 종목은 DART 대상 아님"
    disclosure_block = get_dart_disclosures(stock_code, days=180) if is_kr else "미국/티커 종목은 DART 대상 아님"
    scale_block = _info_scale_block(stock_code) if is_kr else "국내 KRX 규모 정보 대상 아님"
    valuation_block = get_fundamentals(stock_code) if is_kr else "미국/티커 종목은 별도 기업정보 자료를 참고하십시오."
    return f"""
[수집 시각] {TODAY.strftime('%Y-%m-%d %H:%M')} KST
[시장 단계] 숨은정보형/정보전달형 날짜무관 자료 수집
[자료 성격] 미리 제작 가능한 숨은정보형 자료 — 시황·가격 브리핑용 아님
[중요]
- 이 자료는 날짜와 상관없이 미리 뽑아둘 수 있는 숨은정보형 대본용입니다.
- 대본에서는 오늘, 어제, 이번 주말, 다음 장, 장중, 장마감 같은 시간대 중심 전개를 하지 마십시오.
- 현재가, 종가, 등락률, 시가, 고가, 저가, 거래량, 당일 수급을 대본의 중심 소재로 쓰지 마십시오.
- 가격 예측이나 매수·매도 판단이 아니라, 사람들이 겉으로 보는 숫자 뒤에 어떤 사업 구조와 변화가 숨어 있는지 설명하십시오.
- 뉴스와 공시는 제목·요약·원문 핵심에 있는 내용만 사용하십시오. 원자료에 없는 숫자와 결론을 만들지 마십시오.

[종목] {stock_name} ({stock_code})

■ 1. 숨은정보형 핵심 뉴스 / 사업·산업 이슈 후보
{get_information_news_headlines(stock_name, stock_code)}

■ 2. DART 실적·IR·사업보고서 핵심 후보
{dart_block}

■ 3. DART 주요 공시 / 지배구조·계약·내부자 참고 후보
{disclosure_block}

■ 4. 기업 규모 참고 자료 (가격 브리핑 금지)
{scale_block}

■ 5. 밸류에이션 참고 자료
{valuation_block}

■ 6. 대본에서 풀어야 할 정보 질문
  - 이 회사는 어떤 사업에서 돈을 버는가.
  - 최근 뉴스와 공시에서 반복되는 사업 변화는 무엇인가.
  - 실적 숫자가 있다면 어느 부문이나 비용 구조와 연결되는가.
  - 투자자가 가격보다 먼저 이해해야 할 리스크와 기회는 무엇인가.
  - 지금 당장 매수·매도 판단이 아니라, 계좌에서 이 종목을 볼 때 어떤 기준을 세워야 하는가.

■ 7. 수동 조사 메모
{MANUAL_NOTES.strip()}
"""

REPORT_PROMPT_TEMPLATE = """당신은 기관 출신 시니어 애널리스트다. 아래 [수집 데이터]만을 근거로 유튜브 대본용 수급 분석 리포트를 작성하라.

## 절대 규칙 (위반 시 리포트 무효)
1. [수집 데이터]에 없는 수치를 절대 만들어내지 마라. 수급 금액, 환율, 주가, 비율 등 모든 숫자는 데이터에 있는 것만 인용한다.
2. "데이터 없음"으로 표기된 항목은 분석에서 제외하고, 언급이 필요하면 "해당 데이터는 확인되지 않음"이라고 쓴다.
3. 팩트와 해석을 구분하라. 데이터에 있는 내용은 [팩트], 거기서 도출한 추론은 반드시 [해석] 또는 "~로 추정된다/가능성이 있다"로 표기한다.
4. 특정 매수/매도 지시("사라", "팔라", 목표가 제시)는 하지 않는다. 시나리오와 체크포인트 제시까지만 한다.
5. "실험적" 표시가 있는 데이터(신용융자 등)는 참고 수준으로만 쓰고, 핵심 논거로 삼지 마라.

## 리포트 구조
1. **팩트**: 데이터에 나타난 현상을 숫자 그대로 요약 (누가, 얼마나, 어느 방향으로)
2. **이면(Why)**: 세부 수급 주체의 성격을 근거로 "왜 샀는지/팔았는지" 추론
   - 연기금 매수 = 장기자금 성격 / 금융투자 매수 = 단기·헤지 성격 가능성
   - 공매도 급감 + 주가 급등 = 숏커버링 가능성 / 공매도 증가 = 하락 베팅 존재
   - 공매도 '잔고' = 반드시 되사야 하는 예약된 매수 물량 (잔고 비중이 높을수록 숏스퀴즈 연료)
   - 외국인 지분율 추세 + 환율 방향을 교차 해석 (지분율 하락 + 원화 약세 = 환리스크 회피성 이탈 가능성)
   - 글로벌 크로스체크: 동종 지수와 같은 방향 = 매크로/업황 요인, 반대 방향 = 종목 고유 요인
   - 신용융자 잔고 급증 후 주가 하락 = 반대매매 연쇄 하락 리스크
   - DART 내부자 상세: 취득(매수)이 다수면 내부자의 자신감 신호일 수 있으나, 스톡옵션·보상 취득 가능성이 명시돼 있으면 단정하지 말 것. 처분(매도)이 다수면 그 반대
   - 대량보유(5%) 실명 데이터: 보고자의 '이름'으로 자금의 성격을 해석하라. 국민연금 확대 = 국내 최대 장기자금의 베팅, 글로벌 운용사 변동 = 패시브 리밸런싱 가능성, 보고사유 '경영참가' = 지배구조 이벤트 신호. 일별 수급(익명 합계)과 실명 보고를 교차하면 "연기금이 샀다"를 "누가 샀다"로 좁힐 수 있다
   - 거래량: 급등락에 거래량이 실렸는지로 흐름의 '진정성' 판단
   - 각 추론마다 어떤 데이터가 근거인지 명시할 것
3. **파생 흐름(So What)**: 이 수급 구조가 이어질 때/꺾일 때 각각의 시나리오
4. **체크포인트(Action)**: 시청자가 직접 확인해야 할 지표와 그 기준선 (매매 지시가 아닌 관찰 포인트)

[수집 데이터]
{raw_data}
"""


# ──────────────────────────────────────────────
# 유튜브 대본용 프롬프트 (실제 AI 음성 낭독용 완성 대본 스타일)
# ──────────────────────────────────────────────
SCRIPT_PROMPT_TEMPLATE = """당신은 {stock_name} 중심 한국 주식 유튜브 채널의 메인 작가다. 아래 [수집 데이터]만을 근거로, 시청자가 그대로 들을 수 있는 약 12,000자 분량의 실제 음성 대본을 작성하라.

## 출력 형식 절대 규칙
1. 완성 대본만 출력한다. 설명, 제목, 목차, 해설, 콘티, 메타 문장을 절대 쓰지 마라.
1-1. 제작용 문장, 코너명, 분석 방법 설명을 대본 안에 절대 쓰지 마라. 실제 방송 문장처럼 바로 숫자와 의문으로 들어가라.
2. 마크다운 금지: **굵게**, # 제목, 번호목록, 대괄호 소제목, 표를 쓰지 마라.
3. 화면 지시 금지: [화면:], [훅], [표면], [이면], [마무리], (카메라), (인트로 음악), (아웃트로 음악), 자막 지시를 절대 쓰지 마라.
4. 섹션 구분은 반드시 정확히 이 구분자만 단독 줄로 사용한다: ---<
5. --- 또는 --- < 또는 다른 구분자는 쓰지 마라. 반드시 ---< 만 쓴다.
6. 첫 줄부터 바로 후킹으로 시작한다. "네", "반갑습니다", "시장의 이면입니다", "오늘은" 같은 채널 인사·자기소개로 시작하지 마라.
6-1. 첫 문장은 반드시 궁금증을 유발해야 한다. 단순 사실 나열이 아니라, 서로 안 맞는 두 흐름을 부딪혀서 "왜 그렇지?"라는 질문이 생기게 써라.
6-2. 첫 블록은 3~5문장으로 쓴다. 좋은 숫자 하나와 이상한 가격·수급 흐름 하나를 부딪힌 뒤, 마지막 문장은 반드시 의문문으로 끝낸다.
6-3. 첫 블록에서 결론을 설명하지 마라. "이 상황입니다", "흐름입니다"로 닫으면 실패다. 시청자가 다음 블록을 보게 만드는 미해결 질문으로 끝내라.
6-4. 좋은 첫 블록의 형태는 "실적/호재 숫자 → 그런데 가격이나 돈의 방향은 반대 → 이게 단순 차익실현인지, 큰돈의 방향 전환인지 질문"이다.
6-5. 첫 블록 첫 문장 안에는 반드시 종목명 "{stock_name}"을 자연스럽게 넣어라. 주어 없이 실적 숫자만 던지면 시청자가 무슨 종목 이야기인지 놓친다.
6-6. 첫 3개 블록은 회사 소개나 사업부 구조 설명으로 들어가지 마라. 먼저 "{stock_name}의 오늘 가격·거래량·실적 숫자·수급 충돌"을 잡아야 한다.
6-7. "사업 구조를 보면", "공시된 사업보고서를 보면", "본업이 탄탄하다" 같은 기업 소개형 문장은 초반 3블록 안에 쓰지 마라. 필요하면 중반 보조근거로만 짧게 쓴다.
7. 한 문단은 보통 2~4문장으로 짧게 끊고, 각 문단 사이에 ---< 를 넣어라. 강조 숫자나 반전 해석이 나오는 앞뒤는 1~2문장 블록으로 끊어도 된다.
8. 출력에서 ---< 구분자 위와 아래는 반드시 한 줄씩 비워라. 즉, 항상 빈 줄 → ---< → 빈 줄 형태로 출력한다.
8-1. ---< 구분자는 반드시 완성된 문장 뒤에만 넣는다. 조사, 연결어, 보조용언, "기다릴 필요"처럼 문장이 끝나기 전에는 절대 넣지 마라.
8-2. 특히 CTA 고정 문구는 문장 중간에 ---< 를 끼워 넣지 마라.
9. 수집 데이터의 [수집 시각] 날짜를 기준으로, 대본 초반부에 오늘 날짜나 요일을 자연스럽게 한 번만 반영하라. 단, "2026년 7월 3일 대본" 같은 제목형 날짜 라벨은 쓰지 마라.

## 자율 앵글 판단 규칙
1. 정해진 유형표에 끼워 맞추지 마라. [수집 데이터] 전체를 먼저 읽고, 그날 데이터에서 가장 말이 안 맞는 지점, 가장 강하게 튀는 숫자, 또는 시장 상식과 충돌하는 흐름을 스스로 찾아라.
1-1. 이 포맷은 겉으로 보이는 등락 설명으로 끝내지 말고, 그 뒤에 있는 자금의 방향, 주체별 성격, 시장이 오해하기 쉬운 지점을 파고들어라. 단, 포맷명이나 작업 과정을 대본에 쓰지 마라.
2. 대본의 출발점은 "분류"가 아니라 "의문"이어야 한다. 예: 왜 주가는 빠졌는데 특정 주체는 샀는가, 왜 매도 금액은 큰데 지분율 변화는 작거나 큰가, 왜 글로벌 지표와 {stock_name}의 반응이 엇갈리는가처럼 데이터 내부의 긴장을 잡아라.
3. 수급·환율·글로벌·공매도·내부자·거래대금은 모두 재료일 뿐이다. 모든 항목을 순서대로 넣으려 하지 말고, 메인 의문을 설명하는 데 필요한 데이터만 앞쪽에 배치하고 나머지는 보조 근거로만 써라.
4. 같은 데이터라도 매번 같은 순서로 전개하지 마라. 오늘의 이상 신호가 어디서 시작됐는지에 따라 후킹, 전개, 반론, 체크포인트 순서를 자연스럽게 바꿔라.
5. 고정 코너처럼 "먼저/두 번째/세 번째"를 반복하지 마라. 필요한 곳에서만 자연스럽게 쓴다.
6. 체크포인트는 항상 3개일 필요 없다. 데이터가 요구하는 만큼 2개, 3개, 또는 4개로 바꿔라.
7. CTA로 넘어가기 전 문장도 매번 바꿔라. "오늘 큰 흐름과 시나리오는 여기까지"를 반복하지 마라.

## 내용 규칙
1. [수집 데이터]에 없는 수치는 절대 만들어내지 마라. 모든 숫자는 데이터에 있는 것만 쓴다.
1-1. 시가총액 기준 환산, 지분율 환산 금액, 평균 계산 등 원자료에 없는 계산을 새로 만들지 마라.
2. "데이터 없음" 항목은 대본에서 언급하지 마라.
3. "실험적" 표시 데이터는 보조 언급까지만 쓰고 핵심 논거로 삼지 마라.
4. 매수/매도 지시, 목표가 제시, 확률 수치 금지. 대신 상승·하락·횡보 중 지금 더 유력한 흐름을 조건부로 말하고, 틀렸을 때 확인할 반대 신호를 제시하라.
5. 팩트는 단정하고, 추론은 유보 화법으로 말하라. 다만 같은 문구를 반복하지 말고 자연스럽게 표현하라.
6. 데이터 중 가장 비정상적이거나 튀는 핵심 숫자 하나를 메인 앵글로 잡아라. 단, 사전에 정해진 분류명에 맞추지 말고 데이터 자체가 던지는 의문을 중심으로 잡아라. 단순히 수급·환율·공매도·내부자를 순서대로 나열하지 마라.
7. {stock_name} 투자자 관점으로 풀어라. 다른 종목과 글로벌 지표는 {stock_name}을 판단하기 위한 비교 변수로만 사용하라.

## 문체 규칙
1. 보고서 말투와 제작용 말투를 모두 금지한다. 글쓴이가 구조를 설명하는 문장을 쓰지 마라.
1-1. 이면추적 평일 대본은 개인채널 상담 말투가 아니다. 더 날카롭고 차분한 추적 톤으로 간다.
1-2. "같이 볼게요", "한번 짚어볼게요", "살펴보겠습니다", "쪼개어 짚어보겠습니다" 같은 진행자식 안내 문장을 남발하지 마라. 데이터가 바로 다음 문장을 끌고 가게 써라.
2. 자연스러운 한국어 방송 구어체로 쓴다. 설명은 "해요", "죠", "거예요", "거죠", "거든요", "잖아요"로 풀고, 꼭 필요한 판단은 짧은 "~다" 문장으로 눌러 말한다.
2-1. 한 블록은 보통 "말문 1문장 → 숫자 1~2문장 → 해석 1~2문장 → 짧은 판단 1문장"으로 구성한다. 숫자만 읽거나 해석만 길게 늘어놓지 마라.
2-2. 좋은 구어체는 전환어가 아니라 호흡이다. 긴 문장을 잘라서 말하고, 시청자가 머릿속에서 바로 그림을 그리게 해라.
2-3. "이 말은 뭐냐면요", "쉽게 말하면", "여기서 봐야 할 건", "이러면" 같은 말문을 쓰되, 한 블록에 하나 이상 반복하지 마라.
2-4. 유튜브 편집 컷이 들어갈 수 있도록 말덩어리를 분리하라. 숫자를 던지는 블록, 그 숫자를 해석하는 블록, 다음 기준으로 넘어가는 블록을 한 덩어리에 몰아넣지 마라.
3. 특정 연결어를 반복해서 구어체처럼 보이게 만들지 마라. 실제 사람이 말하듯 문장 길이와 호흡을 자연스럽게 섞어라.
4. "문제는 이겁니다", "핵심은 이겁니다", "제가 보는 건 이겁니다", "오늘은 좋다 나쁘다 아닙니다" 같은 고정 문구는 쓰지 마라.
4. 전문용어는 길게 설명하지 말고, 시청자가 바로 이해할 수 있게 한 문장으로 풀어라.
5. AI 음성 낭독용이므로 괄호 설명, 영어 약어 남발, 과한 문어체를 피하라.
6. 과장 비유 금지. "온몸으로 막았다", "큰 파도", "험난한 과정", "매물 폭탄", "거칠게 던졌다", "와르르 무너졌다", "돈이 말라붙었다", "찐바닥" 같은 표현을 쓰지 마라. 숫자가 강하면 문장은 더 담백해야 한다.
7. 같은 결론을 반복하지 마라. "외국인이 돌아와야 한다"는 결론은 한 번 강하게 말하고, 다음에는 거래량·환율·글로벌 지표로 역할을 바꿔라.
8. 첫 문장은 리포트형 사건 설명보다 숫자 충돌로 시작한다. "팔십구조원 실적이 나왔는데, 외국인은 오조원 넘게 팔았습니다."처럼 바로 모순을 보여줘라.
8-1. 첫 블록 마지막 문장은 반드시 "왜", "무엇", "어느 쪽", "진짜 이유"가 들어간 궁금증 문장으로 닫아라. 첫 블록에서 답을 말하지 마라.

## 권장 흐름
- 3~6줄 후킹: 가장 강한 숫자나 충돌 지점으로 시작
- 첫 후킹은 "무슨 일이 있었다"가 아니라 "좋은 숫자와 나쁜 돈의 방향이 동시에 나왔다"는 충돌로 시작
- 첫 블록 끝은 반드시 열린 질문으로 닫는다. 예: "그렇다면 지금 이 윗꼬리는 단순 차익실현일까요, 아니면 큰돈이 빠져나가는 신호일까요?"
- 첫 3블록 안에서는 시청자가 지금 무엇을 보고 있는지 헷갈리지 않게 종목명과 현재 가격 흐름을 분명히 말한다.
- 사업부·공시·회사 구조 설명은 초반 몰입을 끊으므로 첫 3블록 안에 넣지 않는다.
- 왜 시장이 흔들렸는지 한 문장으로 연결
- 메인 앵글 하나를 중심으로 수급·환율·글로벌 반도체·공매도·내부자 데이터를 필요한 만큼만 배치
- {stock_name} 주주가 오늘 또는 다음 장에서 봐야 할 기준 제시. 무조건 3개로 맞추지 말고 데이터가 요구하는 만큼만 쓴다.
- 반대 시그널과 리스크 제시
- CTA로 자연스럽게 연결
- CTA 이후에는 다시 종목 분석으로 돌아가지 말고, CTA 흐름을 해치지 않는 짧은 방송 마무리만 붙인다.

[수집 데이터]
{raw_data}
"""


# ──────────────────────────────────────────────
# 주말용 프롬프트 (사전작성 가능한 주말 큰그림 스타일)
# ──────────────────────────────────────────────
WEEKLY_PROMPT_TEMPLATE = """당신은 {stock_name} 중심 한국 주식 유튜브 채널의 메인 작가다. 아래 [수집 데이터]만을 근거로, 주말에 올려도 어색하지 않은 큰그림형 주식 유튜브 대본을 작성하라.

이 포맷은 화요일·수요일·목요일에 미리 뽑을 수 있다. 그래서 제작 당일 시세를 주인공으로 삼으면 실패다.
주간 뉴스 헤드라인, 다음 주 일정·실적발표·경제지표, 그리고 "이번 주가 남긴 질문"을 중심으로 구성하라.

## 출력 형식 절대 규칙
1. 완성 대본만 출력한다. 제목, 목차, 해설, 콘티, 화면 지시, 마크다운을 쓰지 마라.
1-1. 포맷명·코너명·작가 지시 문장을 대본 안에 쓰지 마라. 실제 방송 문장처럼 바로 숫자와 의문으로 들어가라.
1-2. "주말 대본", "당일 등락률", "이 포맷", "자료 성격", "수집 데이터", "에이치티에스에서 맞춰보셔야" 같은 작가용 표현을 완성 대본에 절대 쓰지 마라.
2. 섹션 구분은 반드시 정확히 이 구분자만 단독 줄로 사용한다: ---<
3. 첫 줄부터 바로 후킹으로 시작한다. "네", "반갑습니다", "시장의 이면입니다"로 시작하지 마라.
4. [화면:], [오프닝], [이슈 정리], (인트로 음악) 같은 제작 지시를 절대 쓰지 마라.
5. 출력에서 ---< 구분자 위와 아래는 반드시 한 줄씩 비워라.
6. 수집 시각 날짜를 주말 날짜처럼 말하지 마라. "오늘", "이번 주 확정", "금요일까지 확인됐다"를 확정적으로 쓰지 마라.
7. 정해진 체크리스트형 대본으로 만들지 말고, 큰 질문 하나를 잡아 차분하게 풀어라.

## 내용 규칙
1. [수집 데이터]에 없는 수치는 절대 만들어내지 마라.
2. "데이터 없음" 항목은 언급하지 마라.
3. 금요일 확정 데이터가 [수집 데이터]에 직접 있지 않으면 금요일 종가, 주간 고점·저점, 주간 수익률, 최근 5영업일 흐름을 말하지 마라.
4. 수동 조사 메모나 헤드라인에 없는 뉴스·이슈를 아는 것처럼 쓰지 마라.
5. 매수/매도 지시, 목표가, 확률 수치 금지. 예측보다 질문과 기준을 제시하라.
6. {stock_name} 투자자 관점으로 쓰되, 종목 하나에 갇히지 말고 시장 전체의 돈이 어디로 움직일 수 있는지 큰 그림으로 설명하라.
7. "월요일 장이 열리면 반드시", "다음 주 첫 거래일에 꼭", "확인해야 할 세 가지"처럼 숙제 검사식 표현을 남발하지 마라.
8. 서킷브레이커, 실적발표, 경제지표, 글로벌 반도체 같은 큰 이슈는 헤드라인 근거가 있을 때만 다룬다.

## 주말 큰그림 전개 지침
1. 주말 대본은 당일 복기가 아니다. "주간 뉴스 헤드라인 → 다음 주 일정·실적발표·경제지표 → 이번 주가 남긴 질문" 순서로 큰 주제를 만든다.
2. 아직 주말이 오기 전이라면 "이번 주는 이렇게 끝났다"가 아니라 "주말에 이 이슈를 다시 봐야 하는 이유"로 말한다.
3. 가격 예측보다 질문을 선명하게 만들어라. 예: 실적이 좋아도 돈이 바로 붙을 수 있는가, 글로벌 반도체 자금이 메모리에 남아 있는가, 환율과 미국 지표가 외국인 판단을 바꿀 수 있는가.
4. 감성은 한두 문장만 쓴다. "계좌를 열어보기 무겁다", "걱정되는 마음" 같은 문장을 반복하지 마라.
5. 공포 유튜버처럼 몰아가지 마라. 서킷브레이커나 급락을 말해도 "무섭다"가 아니라 "이 이벤트가 돈의 방향을 어떻게 바꿀 수 있는가"로 해석한다.

## 문체 규칙
1. 보고서 말투 금지. 자연스러운 방송 구어체로 쓴다.
2. "해요", "죠", "거예요", "거죠", "거든요", "잖아요"를 섞어 쓴다.
3. "문제는 이겁니다", "핵심은 이겁니다", "제가 보는 건 이겁니다" 같은 반복 문구 금지.
4. "무거운 한 주", "묵직합니다", "퍼즐을 맞춘다", "거대한 하락", "방아쇠", "돈줄을 쥐고 있다", "공포감과 무게감" 같은 드라마 문장을 쓰지 마라.
5. 숫자가 강할수록 문장은 담백하게 쓴다. 강한 형용사 대신 근거와 순서를 보여줘라.
6. 영어 사업부명과 원재료명은 그대로 쓰지 말고 한국어로 풀어라. Home appliance Solution은 생활가전, Eco Solution과 HVAC는 공조, Steel은 철강재, Resin은 수지, Copper는 구리, B2B는 기업 간 거래로 쓴다.

## 권장 흐름
- 주말에 이 종목/섹터를 다시 봐야 하는 큰 질문으로 후킹
- 주간 뉴스 헤드라인에서 반복되는 쟁점 정리
- 다음 주 일정·실적발표·경제지표가 왜 중요한지 설명
- 이번 주가 남긴 질문을 2~3개로 압축
- 시청자가 당장 예측보다 무엇을 차분히 구분해야 하는지 정리
- CTA는 짧게 연결하고, 마지막은 본문 질문으로 담백하게 닫기

[수집 데이터]
{raw_data}
"""



# ──────────────────────────────────────────────
# 정프로용 개인채널 프롬프트 (친근형, 인사 포함)
# ──────────────────────────────────────────────
JUNGPRO_PROMPT_TEMPLATE = """당신은 '정프로의 투자관점'이라는 개인 주식 유튜브 채널의 메인 작가다. 아래 [수집 데이터]만을 근거로, 정프로가 시청자에게 직접 말하듯이 들리는 약 12,000자 분량의 실제 음성 대본을 작성하라.

## 정프로용 출력 형식 절대 규칙
1. 완성 대본만 출력한다. 제목, 목차, 해설, 콘티, 화면 지시, 마크다운을 쓰지 마라.
2. 화면 지시 금지: [화면:], [훅], [표면], [이면], [마무리], (카메라), (인트로 음악), (아웃트로 음악), 자막 지시를 절대 쓰지 마라.
3. 섹션 구분은 반드시 정확히 이 구분자만 단독 줄로 사용한다: ---<
4. 구분자 위와 아래는 반드시 한 줄씩 비워라. 항상 빈 줄 → ---< → 빈 줄 형태로 출력한다.
5. 첫 블록은 반드시 아래 4단계 흐름으로 시작한다. 첫 블록에는 인사를 넣지 마라.
   ① 한줄요약 또는 궁금증 유발 후킹 1문장.
   ② 디테일을 살짝 챙기는 문장 1~2개. 단, 길게 설명하지 말고 데이터에서 가장 이상한 숫자·수급·가격 괴리·흐름만 살짝 보여줘라.
   ③ 오늘 무엇을 봐야 하는지 말하되, 결론은 말하지 마라.
   ④ 마지막 문장은 반드시 물음표가 붙는 미해결 질문으로 닫아라. 끝까지 보라는 요청문으로 닫지 마라.
6. 첫 블록은 전체 3~5문장 안에서 끝낸다. 짧지만 한줄요약, 디테일, 시청 이유, 미해결 질문이 모두 살아야 한다.
7. 첫 블록의 문장 자체를 고정 복붙하지 마라. 구조만 지키고, 표현은 [수집 데이터]의 가장 이상한 지점에 맞춰 매번 새로 써라.
8. 첫 블록 바로 다음에는 구분자를 넣고, 두 번째 블록에서 친근한 개인채널 인사를 시작한다.
9. 두 번째 블록 인사는 반드시 정프로가 직접 말하듯 자연스럽게 시작한다. 딱딱한 뉴스 앵커처럼 시작하지 마라.
10. 두 번째 블록 첫 문장은 반드시 "반갑습니다, 정프로입니다." 또는 "안녕하세요, 정프로입니다."로 시작한다. 이 문장 앞에 다른 문장을 붙이지 마라.
10-1. 정프로 대본의 구조는 무조건 "첫 블록 궁금증 유발 → ---< → 두 번째 블록 정프로 인사"다. 첫 줄에 인사가 나오면 실패다.
10-2. 인사 뒤에는 오늘 시청자가 느꼈을 감정과 오늘 같이 볼 질문을 붙인다.
10-3. 첫 블록 마지막 문장이 평서문이면 실패다. 반드시 "왜", "무엇", "어느 쪽", "진짜 이유"가 들어간 궁금증 문장으로 끝내라.
11. 수집 데이터의 [수집 시각] 날짜를 기준으로, 초반부에 오늘 날짜나 요일을 자연스럽게 한 번만 반영하라. 제목형 날짜 라벨은 쓰지 마라.

## 정프로 채널 톤
1. 리포트형 분석가가 아니라, 자기 채널에서 시청자와 오래 이야기해온 사람처럼 말한다.
2. 말투는 친근하지만 가볍게 날리지 않는다. 시청자를 겁주거나 혼내지 말고, 옆에서 기준을 같이 잡아주는 느낌으로 쓴다.
3. "여러분", "주주분들", "우리 시청자분들", "이거 그냥 지나치면 안 됩니다", "제가 이 부분은 꼭 같이 보자고 말씀드리고 싶어요" 같은 개인채널식 표현을 자연스럽게 섞어라.
4. 너무 기관 리포트처럼 쓰지 말고, 분석 절차를 설명하는 문장을 반복하지 마라.
5. 너무 과격한 공포팔이 말투도 금지한다. "큰일납니다"는 썸네일용 느낌이 강하므로 본문에서는 남발하지 마라.
6. 자연스러운 구어체로 쓴다. "해요", "죠", "거예요", "거죠", "거든요", "잖아요"를 섞어 사용하라.
7. 정프로 개인의 관점처럼 들리게 하되, 매수·매도 지시처럼 들리면 안 된다. "저라면 무조건" 같은 표현은 쓰지 마라.

## 자율 앵글 판단 규칙
1. 정해진 유형표에 끼워 맞추지 마라. [수집 데이터] 전체를 먼저 읽고, 그날 데이터에서 가장 이상한 숫자, 서로 안 맞는 흐름, 시장 상식과 충돌하는 지점을 스스로 찾아라.
1-1. 겉으로 보이는 등락만 말하지 말고, 그 뒤에 있는 돈의 방향과 주체별 의도를 친근하게 풀어라. 정프로용이어도 "왜 이게 그냥 상승/하락으로 끝나는 이야기가 아닌지"를 반드시 보여줘라.
2. 대본의 출발점은 분류가 아니라 시청자가 실제로 헷갈리는 질문이어야 한다.
3. 수급·환율·글로벌·공매도·내부자·거래대금은 모두 재료일 뿐이다. 모든 항목을 순서대로 넣지 말고, 오늘 가장 중요한 의문을 설명하는 데 필요한 순서대로 재배치하라.
4. 같은 데이터라도 매번 같은 순서로 전개하지 마라. 오늘의 이상 신호가 어디서 시작됐는지에 따라 인사 뒤 연결, 전개, 반론, 체크포인트 순서를 자연스럽게 바꿔라.
5. 체크포인트는 항상 3개일 필요 없다. 데이터가 요구하는 만큼 2개, 3개, 또는 4개로 바꿔라.
6. CTA로 넘어가기 전 문장도 매번 바꿔라. "오늘 큰 흐름과 시나리오는 여기까지"를 반복하지 마라.

## 내용 규칙
1. [수집 데이터]에 없는 수치를 절대 만들어내지 마라. 모든 숫자는 데이터에 있는 것만 쓴다.
2. "데이터 없음" 항목은 대본에서 언급하지 마라.
3. "실험적" 표시 데이터는 보조 언급까지만 쓰고 핵심 논거로 삼지 마라.
4. 매수/매도 지시, 목표가 제시, 확률 수치 금지. 대신 지금 더 유력한 흐름과 반대 신호를 조건부로 말하라.
5. 팩트는 단정하고, 추론은 "가능성이 큽니다", "그쪽에 가깝습니다", "이렇게 볼 수 있습니다"처럼 유보 화법으로 말하라.
6. {stock_name} 투자자 관점으로 풀어라. 다른 종목과 글로벌 지표는 {stock_name}을 판단하기 위한 비교 변수로만 사용하라.
7. 시청자가 바로 써먹을 수 있게 마지막에는 다음 장에서 볼 기준을 분명하게 남겨라.

## 정프로용 권장 흐름
- 첫 블록: 한줄요약/궁금증 후킹 → 디테일 살짝 → 그래서 오늘은 이걸 먼저 봐야 한다는 시청 이유 → 영상 끝까지 시청 요청. 인사 금지.
- 첫 블록은 예시 문장 복사가 아니라 [수집 데이터]를 보고 매번 새로 써라. 특히 "{stock_name}, 오늘은 하락률보다 이 숫자를 먼저 봐야 합니다"를 고정문처럼 반복하지 마라.
- 두 번째 블록: 친근한 인사 2~4문장. 정프로 등장 + 오늘 시청자가 느꼈을 감정 + 오늘 같이 볼 질문.
- 데이터가 던지는 가장 이상한 지점 하나를 이야기의 중심으로 잡기.
- 그 지점이 왜 {stock_name} 주주에게 중요한지 쉽게 풀기.
- 필요한 데이터만 순서 바꿔 배치하기.
- 중간중간 시청자의 반문을 대신 던지고 답해주기.
- 다음 장에서 확인할 기준 제시.
- 정프로식 CTA로 자연스럽게 연결.
- CTA 이후에는 종목 분석으로 다시 돌아가지 말고, CTA 흐름을 해치지 않는 짧고 친근한 방송 마무리만 둔다.

## 금지 문장
- 문제는 이겁니다
- 핵심은 이겁니다
- 제가 보는 건 이겁니다
- 오늘은 좋다 나쁘다 아닙니다
- 나침반 삼아
- 돌아오겠습니다

[수집 데이터]
{raw_data}
"""


# ──────────────────────────────────────────────
# 공통 CTA 템플릿
# ──────────────────────────────────────────────
BLENDING_CTA = """
## CTA 삽입 규칙
아래 CTA 본문은 고정 문구다. 문장, 순서, 혜택, 표현을 새로 쓰거나 줄이거나 늘리지 마라.
모델이 새로 써도 되는 부분은 CTA 직전 연결 문단 1개뿐이다.
단, [CTA], [마무리 CTA], [광고] 같은 소제목은 출력하지 않는다.

## 연결 원칙
1. CTA 직전 연결 문단은 2~4문장만 쓴다.
2. 연결 문단은 본문에서 다룬 핵심 문제를 받아서 "내 계좌에서도 무엇부터 봐야 하는가"로 자연스럽게 넘어간다.
3. 연결 문단에서만 종목·시장 흐름을 언급하고, CTA 본문 안에서는 종목 내용을 억지로 섞지 마라.
4. 수익 보장, 매수·매도 지시, 유료 리딩방 오해를 부르는 표현은 연결 문단에도 쓰지 않는다.
5. 아래 고정 CTA 본문은 그대로 사용한다.

주식하다 보면 제일 답답한 순간이 있습니다.

분명 종목은 여러 개 들고 있는데, 뭐부터 봐야 할지 모르겠고, 손실 난 종목을 더 버텨야 할지, 물을 타야 할지, 먼저 줄여야 할지 판단이 안 되는 순간이 오거든요.

그래서 제가 이번에 하나 제대로 만들어놨습니다.

여러분이 들고 있는 종목을 캡처해서 올리면, 증권 에이아이가 내 계좌의 비중 쏠림, 손실 구간, 그리고 지금 무엇부터 점검해야 하는지까지 정리해 주는 무료 에이아이 포트폴리오 리포트입니다.

수익률만 보고 판단하면 오히려 더 헷갈릴 수 있습니다.

진짜 먼저 봐야 하는 건 내 계좌 안에서 어떤 종목에 비중이 몰려 있는지, 손실이 어디에 쌓여 있는지, 그리고 지금 무엇부터 정리해야 하는지 이 순서입니다.

---<

자, 지금 화면에 보이는 건 저희 구독자분께 제가 직접 요청드려서 받아본 실제 사용 화면입니다.

개인정보나 계좌번호 같은 민감한 부분은 전부 가린 상태고요.

보시면 사용 방법도 어렵지 않습니다.

사이트에 들어가서 내가 들고 있는 보유종목 화면만 캡처해서 올리면 됩니다. 토스증권이든, 키움이든, 삼성증권이든 상관없습니다. 종목명, 평가금액, 수익률 정도만 보이게 올려주시면 되고요. 이름이나 계좌번호는 꼭 가리셔도 됩니다.

이렇게 내 종목을 캡처한 이미지를 올려놓고 삼십 초 정도만 기다리면, 증권 에이아이가 화면 안에 있는 종목들을 읽고 바로 분석해 줍니다.

이런 식으로요.

---<

보시면 단순히 종목명만 읽어주는 게 아닙니다.

내 계좌가 어느 종목에 많이 쏠려 있는지, 손실 구간은 어디에 몰려 있는지, 지금 어떤 종목부터 먼저 점검해야 하는지까지 순서대로 정리해 줍니다.

점수와 등급도 나오고, 인식된 종목 수, 최대 비중, 판독률도 같이 확인할 수 있습니다.

도넛 그래프와 비중 바도 있어서 내 계좌가 어디에 몰려 있는지 한눈에 볼 수 있고요.

여기서 중요한 건, 이게 매수해라 매도해라 찍어주는 도구가 아니라는 겁니다.

내 계좌 구조를 객관적으로 펼쳐놓고, 어디부터 점검해야 하는지 기준을 잡아주는 무료 리포트라고 보시면 됩니다.

주식하다 보면 손실 난 종목을 보면서 계속 고민하게 되잖아요.

이걸 더 버텨야 하나, 물을 타야 하나, 아니면 먼저 줄여야 하나.

그런데 그 판단을 하기 전에 먼저 봐야 하는 게 있습니다. 내 계좌에서 어떤 종목이 가장 큰 비중을 차지하고 있는지, 손실이 어디에 몰려 있는지, 그리고 지금 당장 정리해야 할 우선순위가 뭔지부터 봐야 합니다.

잔고 캡처 한 장만 올려도, 내 계좌에서 무엇부터 점검해야 하는지 바로 확인하실 수 있게 만들어놨습니다.

그리고 에이아이 리포트로 부족하다 싶은 분들은 그 안에서 전문가 점검까지 이어서 신청하실 수 있습니다.

종목 쏠림, 손실 구간 정리 순서, 현금 비중과 리밸런싱 기준까지 이어서 확인해 보실 수 있습니다.

---<

여기에 기존에 준비해둔 자료들도 같이 보실 수 있게 정리해놨습니다.

먼저 주가가 조정을 받으면서 흔들릴 때, 세력과 기관의 평균 단가를 역산해서 기준 가격대를 잡아볼 수 있는 세력단가 지지선 계산기가 준비되어 있고요.

대장주가 잠시 숨 고르기에 들어갈 때, 빠져나간 자금이 어느 후방 소부장 섹터나 전장 밸류체인 쪽으로 이동하고 있는지 흐름을 확인할 수 있는 글로벌 에이아이 자금 순환 맵도 같이 보실 수 있습니다.

여기에 차트를 볼 때 복잡한 지표 때문에 헷갈리지 않도록 정리한 에이아이 주식 보조지표 템플릿, 계좌 리스크를 관리하고 매매 기준을 세우는 데 도움이 되는 실전 매매 전략노트도 같이 준비해놨습니다.

그리고 시장의 순환매 흐름과 주도 섹터 변화를 장중에 바로 확인할 수 있도록 주도주 알림센터 입장까지 같이 열어놨습니다.

주식 시장의 큰 판을 읽는 데 도움이 되는 시크릿 책자 3권도 준비했습니다.

첫 번째는 개미가 놓친 돈의 지도, 두 번째는 자본법칙 돈의 시선, 세 번째는 코스피 이만의 시대 돈의 흐름을 읽는 법입니다.

단순히 내 계좌만 보는 게 아니라, 시장 전체에서 돈이 어디서 빠지고 어디로 이동하는지까지 같이 보실 수 있게 만든 구성입니다.

---<

자, 이제 신청 방법도 어렵지 않습니다.

지금 화면에 보이는 신청 페이지에서 간단하게 신청만 해주시면 됩니다.

이건 다른 곳처럼 신청해놓고 며칠 뒤에 보내드립니다, 순차적으로 자료 보내드립니다, 이런 방식이 절대 아닙니다.

이 영상에서 보시는 것처럼 신청만 하시면 바로 무료 에이아이 포트폴리오 리포트 사이트로 연결되게 만들어놨습니다.

그러니까 기다릴 필요 없이, 신청하고 바로 들어가서 내 계좌를 점검해 보실 수 있는 구조입니다.

지금 내 종목, 이대로 괜찮은지 궁금하신 분들은 어렵게 생각하지 마시고 잔고 캡처 한 장만 준비하시면 됩니다.

수익률보다 먼저 봐야 할 건 비중입니다.

손실 종목에 물타기 하기 전에, 내 계좌에서 무엇부터 정리해야 하는지 순서부터 확인해 보셔야 합니다.

지금 바로 화면 아래 구독과 좋아요 눌러주시고, 댓글창에 구독완료 딱 네 글자만 남겨주세요.

그리고 영상 설명란이나 고정 댓글에 있는 링크로 들어가셔서 신청만 해주시면, 기다릴 필요 없이 바로 확인해 보실 수 있습니다.
"""

# 모든 포맷이 공유하는 안전 규칙
# 모든 포맷이 공유하는 안전 규칙
_COMMON_RULES = """## 절대 규칙 (위반 시 대본 무효)
1. 완성 대본만 출력한다. 제목, 목차, 마크다운, 화면 지시, 괄호 연출 지시를 쓰지 마라.
2. 섹션 구분은 반드시 정확히 ---< 만 단독 줄로 사용한다. 구분자 위와 아래는 반드시 한 줄씩 비운다.
3. 첫 줄부터 후킹으로 시작한다. "네", "반갑습니다", "시장의 이면입니다"로 시작하지 마라.
4. [수집 데이터]에 없는 수치를 절대 만들어내지 마라.
5. "데이터 없음" 항목은 언급하지 마라. "실험적" 표시 데이터는 보조 언급까지만 쓴다.
6. 매수/매도 지시, 목표가, 진입·청산 타점 제시 금지. "오를 확률 O%" 같은 확률 수치도 금지한다.
7. 음모론적 단정 금지. 데이터로 확인되는 행동과 합리적 해석까지만 말한다.
8. 팩트는 단정하고, 추론은 유보 화법으로 말한다.
9. 나열식 브리핑 금지. 가장 튀는 핵심 데이터 하나를 중심 질문으로 잡고 끝까지 이어가라. 단, 각도 후보의 표현이나 제목을 문장으로 복사하지 마라.
10. 보고서 말투 금지. 자연스러운 방송 구어체로 쓴다. "해요/죠/거예요/거죠/거든요/잖아요"로 풀어주되, 핵심 판단은 짧은 "~다" 문장으로 박아라. 전부 "~요"로만 끝내도 안 되고, 전부 "~습니다"로만 끝내도 안 된다.
11. "문제는 이겁니다", "핵심은 이겁니다", "제가 보는 건 이겁니다", "오늘은 좋다 나쁘다 아닙니다" 같은 고정 문구 금지.
12. 매번 같은 구조를 반복하지 마라. 미리 정한 유형명이나 분류표에 맞추지 말고, 데이터 안에서 가장 이상한 숫자·모순·괴리를 스스로 찾아 그 지점을 중심으로 자연스럽게 구성하라.
13. 체크포인트 개수와 CTA 연결 문장은 매번 데이터 흐름에 맞게 달리하라.
14. CTA로 넘어갈 때는 본문 마지막 핵심 데이터와 반드시 이어라. 예를 들어 거래량·외국인·환율을 다뤘다면 CTA 첫 문장에 그 기준을 자연스럽게 받아서 말하라. CTA가 본문과 분리된 광고처럼 보이면 안 된다.
15. 공포 결론으로 몰아가지 마라. 불리한 수급·거래량·환율을 말해도 "그렇다고 반등이 의미 없다는 뜻은 아니다"처럼 반대 가능성을 짚고, 결국 확인 기준으로 마무리하라.
16. 장중 가격과 확정 수급을 반드시 구분하라. [시장 단계]가 장전/장중이면 종가·오늘 종가·오늘 마감·장마감·마감했습니다·장을 마쳤습니다·마감 기준 표현은 절대 쓰지 마라. 투자자별 수급, 외국인 지분율, 공매도는 최근 확정 데이터 기준으로만 말하고, 오늘 수급은 "장 마감 뒤 확인해야 합니다"라고만 밝혀라.
17. 매매 지시처럼 들리는 문장을 피하라. "절대 안 됩니다"보다 "확인이 필요합니다", "보고 판단해도 늦지 않습니다"로 말하라.
18. 첫 문장은 앞 문장이 있는 것처럼 시작하지 마라. "그런데", "그렇다면", "하지만", "여기서"로 대본을 시작하지 말고, 현재 가격·거래량·시장 단계 중 하나로 바로 시작하라.
19. 과장된 대결·재난 비유를 쓰지 마라. "격전", "맹렬하게", "공포에 질려", "집어던졌다", "처참했다", "무자비하다", "거대한 매도 세력", "정면으로 충돌", "필사적으로 방어", "치열한 힘겨루기" 같은 표현은 금지한다.
20. [시간대별 가격 흐름]이 있으면 단순 등락률만 말하지 말고, 어느 시간대에 고점·저점·회복·이탈이 나왔는지 한두 장면만 골라 가격 흐름을 구체화하라. 단, 실험적 데이터이므로 수집 데이터에 없는 거래대금 환산은 하지 마라.
"""


# ──────────────────────────────────────────────
# 장마감 브리핑

# ──────────────────────────────────────────────
# 장마감 브리핑 (평일 저녁용, 4~5분)
# ──────────────────────────────────────────────
CLOSING_PROMPT_TEMPLATE = """당신은 '시장의 이면'을 추적하는 유튜브 경제 채널의 작가다. 오늘 장이 막 끝났다. 아래 [수집 데이터]만을 근거로 약 12,000자 분량의 장마감 브리핑 대본을 작성하라. 뉴스 리포트가 아니라, 시청자에게 직접 설명하는 방송 구어체로 써라.

""" + _COMMON_RULES + """
## 장마감 전개 지침
1. 첫 블록은 숫자 나열로 시작하지 마라. "오늘 ○○ 종가는… 거래를 마쳤습니다", "전일 대비… 상승한 수치입니다" 같은 뉴스 문장으로 시작하면 실패다.
2. 첫 블록은 시청자가 바로 느낄 질문으로 시작한다. 예: "오늘 오른 건 맞아요. 그런데 이 반등이 진짜 단단했는지는 따로 봐야 합니다."처럼 가격과 거래량·수급의 엇박자를 먼저 잡아라.
3. 종가, 등락률, 거래량은 첫 블록 안에 넣어도 되지만 한 문장에 몰아넣지 마라. 짧게 나누고, 숫자 뒤에는 반드시 "그래서 이게 왜 이상한지"를 붙여라.
4. 장마감 브리핑의 핵심은 "오늘 얼마나 올랐나/빠졌나"가 아니라 "오늘 종가가 당일 흐름 안에서 어떤 위치에 남았나"다. 고점권 마감인지, 저점권 마감인지, 중간권 마감인지가 내일 시초가의 출발 심리를 만든다는 식으로 풀어라.
5. 당일 투자자별 수급이 아직 확정되지 않았으면 단정하지 마라. 확정 데이터만 말하고, 미확정 수급은 "확정 뒤 확인해야 합니다"로 처리하라.
6. 오늘의 한 장면은 하나만 고른다. 예: 거래량 없는 반등, 막판 종가 방어, 외국인 매도 누적과 가격 반등의 충돌, 고점권 마감인데 수급은 약한 괴리. [시간대별 가격 흐름]이 있으면 고점·저점이 나온 시각과 마지막 체결권 가격을 함께 보면서 이 한 장면을 깊게 풀어라.
7. 내일 체크포인트는 시초가, 첫 한 시간 거래량, 전일 고가·저가 재돌파 여부, 외국인 매도 강도 중 오늘 메인 의문을 검증할 지표로 잡아라.

## 문체 지침
- 퇴근길에 듣는 브리핑. 짧은 문장, 빠른 호흡, 군더더기 제로.
- "수치입니다", "기록했습니다", "해당합니다", "시사합니다", "증거입니다", "대목입니다", "형국입니다" 같은 리포트 문장을 쓰지 마라.
- 구어체 예시:
  나쁜 문장: 오늘 삼성전자 종가는 삼십일만 팔천 원에 거래를 마쳤습니다.
  좋은 문장: 오늘 삼성전자, 종가만 보면 분명히 올랐어요. 그런데 문제는 이게 얼마나 단단한 반등이었느냐입니다.
  나쁜 문장: 직전 사 일 평균 대비 칠십육 퍼센트 수준에 불과합니다.
  좋은 문장: 가격은 올랐는데 거래량은 평소보다 덜 붙었어요. 이러면 반등을 무조건 믿기 어렵습니다.
- 제작 지시 없이 실제 말하는 문장만 출력한다.

[수집 데이터]
{raw_data}
"""


# ──────────────────────────────────────────────
# 장중 속보 (장중 긴급용, 2~3분)
# ──────────────────────────────────────────────
INTRADAY_PROMPT_TEMPLATE = """당신은 '시장의 이면'을 추적하는 유튜브 경제 채널의 작가다. 지금 장중이고, 이 종목의 현재 가격과 거래량을 실시간으로 점검하는 상황이다. 아래 [수집 데이터]만을 근거로 약 12,000자 분량의 장중 속보 대본을 작성하라. 속도감 있는 구어체.

""" + _COMMON_RULES + """6. 중요: 수급·공매도 데이터는 전일까지만 확정된 것이다. 대본에서는 반드시 "오늘 수급은 장 마감 뒤 확인해야 합니다"라고만 말하고, 오늘의 매매 주체를 아는 것처럼 말하지 마라. "오늘 밤", "오늘 저녁", "오늘 장이 모두 끝나고", "오늘 장을 마치고" 같은 표현으로 바꾸지 마라.

## 장중 속보 전개 지침
1. 첫 블록은 지금 가격과 등락, 거래량 흐름으로 바로 시작한다. 장중 속보는 느린 배경 설명으로 시작하면 안 된다.
2. 바로 다음에는 "오늘 수급은 장 마감 뒤 확인해야 합니다"라고 선을 긋고, 최근 확정 수급·공매도·외국인 지분율을 오늘 움직임의 복선으로만 사용하라.
3. 장중 속보의 이면은 "방금 오른 이유"를 단정하는 게 아니다. 가격은 움직였는데 거래량이 따라오는지, 전일 고점·저점과 어떤 관계인지, 최근 확정 수급과 같은 방향인지 충돌하는지를 봐야 한다.
4. 시청자가 지금 화면에서 바로 볼 수 있는 기준을 중심으로 말하라. 예: 현재 상승폭 유지, 고점 재돌파, 거래량이 평균 대비 유지되는지, 특정 가격대 위에서 버티는지. [시간대별 가격 흐름]이 있으면 어느 시각에 고점·저점이 나왔고 현재가가 그 사이 어디에 있는지 짧게 짚어라.
5. 장중 포맷은 짧은 문장과 빠른 호흡을 유지하되, 전체 분량 기준은 지킨다. 속도감 있게 깊게 풀어라.
6. 어제 흐름은 좋게 들어가도 된다. 다만 어제 실적·급락·외국인 매도·서킷브레이커 복기는 최대 2블록, 길어도 3블록 안에서 끝내라.
7. 첫 5블록 안에 반드시 오늘 장중 현재가, 오늘 고점·저점 또는 시간대별 흐름, 거래량 강도, 오늘 수급 미확정 원칙이 모두 들어가야 한다.
8. 어제 이야기를 한 뒤에는 반드시 "그래서 오늘 이 반등 또는 하락을 어떻게 확인할 것인가"로 넘어가라. 어제 자체를 다시 해설하는 대본이 되면 실패다.
9. [시장 특이상황] 뉴스는 기사 발행시각 기준이다. 수동 메모에 오늘 발동이라고 적혀 있지 않으면 오늘 서킷브레이커·사이드카가 걸렸다고 말하지 마라.
10. 전일 서킷브레이커는 오늘 장중 흐름의 배경으로만 짧게 사용한다. 표현은 "어제 충격이 아직 남아 있다", "전일 시장 충격 이후 오늘 가격을 보는 중이다" 정도로 낮춰라.
11. 첫 문장은 리포트처럼 "발표했습니다/기록했습니다/시장 반응은"으로 시작하지 마라. 실제 말투로 "지금 삼성전자, 가격만 보면..."처럼 시작하라.
12. "방금 전", "방금", "조금 전"은 현재가·장중 가격 변동에만 쓴다. 실적 발표, 공시, 뉴스, 전일 수급처럼 날짜가 찍힌 과거 자료에는 절대 쓰지 마라.
13. [수집 데이터]의 날짜가 [수집 시각] 날짜보다 이전이면 반드시 "어제", "전일", "직전 거래일", "최근 확정 데이터"로 말한다. 과거 공시나 뉴스를 현재 발생처럼 말하지 마라.

## 문체 지침
- 긴급 속보 톤. 문장 최대한 짧게. 15초 안에 핵심 도달.
- "발표했습니다", "기록했습니다", "나타났습니다", "확인해 보겠습니다"로 문장을 이어가지 마라.
- "지금 보면요", "자, 여기서 봐야 할 건", "이게요", "그러니까", "쉽게 말하면" 같은 실제 말문을 섞어라.
- "방금 전 발표", "방금 공시", "조금 전 나온 뉴스"처럼 쓰지 마라. 날짜가 과거면 "어제 나온 공시", "전일 뉴스", "최근 확인된 자료"라고 말한다.
- 제작 지시 없이 실제 말하는 문장만 출력한다.

[수집 데이터]
{raw_data}
"""


# ──────────────────────────────────────────────
# 월요일장 프리뷰 (주말 제작용, 5~6분)
# ──────────────────────────────────────────────
MONDAY_PROMPT_TEMPLATE = """당신은 '시장의 이면'을 추적하는 유튜브 경제 채널의 작가다. 지금은 주말이고, 시청자는 월요일 개장을 준비하고 있다. 아래 [수집 데이터](금요일 마감 기준)만을 근거로 약 12,000자 분량의 월요일장 프리뷰 대본을 작성하라. 구어체.

""" + _COMMON_RULES + """6. 가격은 반드시 '금요일 종가' 기준으로 표현하라. "실시간·현재가" 표현 금지.
7. 월요일을 단정 예측하지 마라. 이 코너의 형식은 "금요일에 시장이 남긴 상태 → 월요일에 확인해야 할 갈림길"이다.

## 월요일장 프리뷰 전개 지침
1. 첫 블록은 금요일 장이 남긴 가장 중요한 숫자 하나로 시작한다. 금요일 종가, 고점·저점 대비 위치, 거래량, 외국인 수급 중 월요일 시초가 심리를 가장 잘 설명하는 숫자를 골라라.
2. 주말 대본이므로 "지금 현재가"처럼 말하지 마라. 금요일에 시장이 어떤 상태로 주말에 들어갔는지, 그 상태가 월요일 첫 한 시간에 어떤 압력으로 나타날 수 있는지 설명하라.
3. 주말 사이 새 뉴스는 수동 조사 메모에 있는 것만 다룬다. 메모가 비어 있으면 모르는 뉴스를 만들지 말고, 환율·글로벌 반도체·선물 흐름 같은 데이터 변수만 언급하라.
4. 월요일 시나리오는 두 갈래로 만든다. 갭상승·갭하락을 단정하지 말고, 시초가 이후 거래량과 전일 고가·저가, 외국인 매도 강도, 코스피 대형주 동조를 기준으로 어느 쪽 힘이 커지는지 말하라.
5. 마지막은 "월요일 아침에 뭘 먼저 볼지"로 닫는다. 체크포인트는 이름만 나열하지 말고, 어떤 흐름이면 긍정이고 어떤 흐름이면 조심인지까지 말하라.

## 문체 지침
- 주말의 차분함 + 월요일 준비의 긴장감.
- 제작 지시 없이 실제 말하는 문장만 출력한다.

[수집 데이터]
{raw_data}
"""


# ──────────────────────────────────────────────
# 소재 선정 프롬프트 (스캔 결과를 AI에게 넘겨 오늘의 소재를 고르게 함)
# ──────────────────────────────────────────────
TOPIC_PICK_PROMPT = """당신은 '시장의 이면'을 추적하는 유튜브 경제 채널의 콘텐츠 기획자다. 아래 [스캔 결과]에서 오늘 영상 소재가 될 후보 3개를 골라라.

## 선정 기준 (반직관 신호 우선)
- 시장 분위기와 반대로 움직인 큰손 (예: 하락장에 연기금 매수 상위)
- 랭킹 단골(초대형주)이 아닌 낯선 종목의 등장
- 같은 종목이 한쪽에선 매수 상위, 다른 쪽에선 매도 상위 (큰손끼리 반대 베팅)
- 한 회사에 같은 날 공시가 몰림 / 동일인이 내부자 + 대량보유를 동시 보고 (큰 폭 변동 신호)
- 사모펀드·지주사 등 '이름 있는 주체'의 보고

## 각 후보마다 작성할 것
1) 무엇이 이상한가 (한 줄)
2) 가능한 훅 (시청자의 상식과 충돌하는 한 문장)
3) 심층 수집에서 반드시 확인할 것 (취득/처분 방향, 주가 방향과의 관계, 수급 교차 등)

## 주의
- 같은 날 여러 임원이 동시에 보고했다면 스톡옵션·보상 일괄 지급일 가능성이 있으니 "내부자 매수 러시"로 단정하지 마라. 확인 항목으로만 제시하라.
- [스캔 결과]에 없는 사실(주가, 업종 상황 등)을 아는 것처럼 쓰지 마라. 모르는 부분은 "심층 수집에서 확인"으로 넘겨라.

마지막에 오늘의 1순위 소재 하나를 추천하고 그 이유를 두 문장으로 적어라.

그리고 응답의 맨 마지막 줄에는 반드시 아래 형식의 한 줄만 출력하라 (자동 대본 생성 연결용):
[1순위]=종목명|종목코드
- 종목코드는 [스캔 결과]에 6자리 코드가 보이면 그 코드를 쓰고, 안 보이면 '코드미상'이라고 써라.
- 예: [1순위]=솔루엠|248070  또는  [1순위]=남양유업|코드미상

[스캔 결과]
{scan}
"""


# ──────────────────────────────────────────────
# 전체 영상 대본 공통 길이/말투 규칙 (모든 대본 포맷에 자동 주입)
# ──────────────────────────────────────────────
GLOBAL_LONG_TALK_RULES = """
## 전체 대본 공통 분량·구성 규칙
1. 최종 대본은 CTA 포함 메모장 기준 10,000자~12,000자, 목표는 11,000자 안팎이다.
2. ---< 구분자는 34개~50개 사이를 목표로 사용한다. 최소 30개 아래로 내려가면 실패다. 각 구분자는 단독 줄로 쓰고 위아래 한 줄씩 비운다.
3. 본문은 최소 24개 이상의 분석 블록을 만든 뒤 CTA로 넘어간다.
4. 한 블록은 보통 2~4문장으로 쓴다. 숫자 공개, 반전 해석, 다음 기준 전환은 별도 블록으로 끊어도 된다. 중반 분석도 한 블록에 너무 많은 역할을 몰아넣지 않는다.
5. 같은 숫자나 같은 주장을 반복해 분량을 채우지 마라. 다시 언급할 때는 원인, 반론, 확인 기준처럼 다른 역할을 줘라.
6. 숫자를 말하면 바로 넘기지 말고, 그 숫자가 무엇인지, 왜 평소와 다른지, {stock_name} 주주가 어떻게 봐야 하는지를 차례로 풀어라.
7. CTA 뒤에는 종목 분석을 다시 이어 붙이지 않는다. 당일 핵심 질문과 다음 장 확인 기준은 CTA 직전 또는 짧은 마무리에서만 정리한다.
8. 영문 약어와 숫자는 AI 음성 낭독이 자연스럽도록 가능한 한 한글 독음으로 풀어 쓴다.
9. 전일·어제 이슈는 오늘 흐름을 이해시키는 배경으로만 사용한다. 어제 복기가 본문 초반을 길게 차지하면 실패다.
10. 어제 실적·급락·수급·뉴스를 모두 다루더라도 2~3블록 안에서 압축하고, 바로 오늘의 가격·거래량·수급 확인 기준으로 넘어가라.
11. 첫 블록은 절대 설명으로 닫지 않는다. 좋은 숫자와 이상한 시장 반응을 부딪힌 뒤, 마지막 문장을 의문문으로 끝내서 시청 이유를 만든다.
12. 첫 블록에서 "바로 부딪히고 있는 상황입니다/흐름입니다"처럼 정리하지 마라. 첫 블록은 답이 아니라 질문이다.
13. 첫 블록 첫 문장에는 반드시 {stock_name}을 넣는다. 종목명이 빠진 채 실적·시장 충격부터 말하면 실패다.
14. 첫 3블록은 {stock_name}의 가격·거래량·실적·수급 충돌을 설명하는 데만 쓴다. 사업부 구조, 사업보고서, 제품군, 회사 소개는 초반에 넣지 마라.
15. 사업부·공시·회사 구조는 중반 이후 필요한 경우에만 한두 블록 보조근거로 쓴다. 초반 몰입을 끊으면 실패다.
"""



GLOBAL_BEHIND_TRACKING_RULES = """
## 전체 대본 공통 심층 흐름 규칙
1. 대본 전체는 하나의 큰 질문을 풀어가는 흐름이어야 한다.
2. 수급, 환율, 거래량, 글로벌 지표, 공매도, 내부자 데이터는 각각 따로 설명하지 말고 큰 질문을 검증하는 근거로만 사용한다.
3. 겉으로 보이는 가격 흐름과 실제 자금 흐름이 다를 때, 그 차이가 왜 중요한지 시청자 눈높이로 설명한다.
4. 외국인, 개인, 금융투자, 연기금은 자금 성격이 다르다는 점을 풀되, 단정하지 말고 데이터에 맞는 범위에서 말한다.
5. 중간중간 시청자가 가질 법한 반문을 자연스럽게 받아주고, 바로 숫자로 답한다.
6. 체크포인트는 본문에서 던진 질문을 다음 장에서 확인하는 기준이어야 한다.
"""


GLOBAL_BALANCED_TONE_RULES = """
## 전체 대본 공통 균형·신뢰도 규칙
1. 과장어와 재난 비유를 남발하지 마라. 숫자 자체가 힘을 갖게 두고 문장은 차분하게 쓴다.
2. 불리한 데이터를 말할 때는 반대 가능성이나 완화 해석을 함께 제시한다. 다만 최종 판단은 확인 기준으로 분명히 연결한다.
3. 비유는 한 편 전체에서 최대 2~3개만 쓴다. 비유보다 숫자와 해석을 우선한다.
4. 매수·매도 지시처럼 들리는 문장은 금지한다. 비중 확대·축소는 데이터 확인 뒤 판단하는 톤으로 말한다.
5. 장중 가격과 확정 수급을 구분한다. 투자자별 수급, 외국인 지분율, 공매도, DART는 최근 확정 데이터 기준으로만 말한다.
6. 데이터에 없는 환산 금액, 시가총액 계산, 임의 비율 계산을 새로 만들지 마라.
7. 공포로 닫지 말고 기준으로 닫는다. 시청자가 다음 장에서 무엇을 볼지 갖고 끝나야 한다.
"""


GLOBAL_FORMAT_IDENTITY_RULES = """
## 전체 대본 공통 포맷별 정체성 규칙
1. 모든 포맷을 같은 구조로 쓰지 마라. 선택된 포맷의 목적을 먼저 살린다.
2. 심층형은 가격과 자금 흐름의 불일치를 깊게 파고든다.
3. 정프로용은 개인채널 말맛을 살린다. 첫 블록 후킹 뒤 두 번째 블록에서 인사하고, 시청자 감정을 받아준다.
4. 장마감은 정규장 마감 위치가 주인공이다. 시가·고가·저가·종가, 종가 위치, 거래량, 막판 힘을 먼저 본다.
5. 장중은 현재 가격과 거래량 유지 여부가 주인공이다. 오늘 수급은 확정된 것처럼 말하지 않는다.
6. 월요일 프리뷰는 금요일이 남긴 상태와 월요일 첫 30분~1시간의 갈림길이 중심이다.
7. 주간 결산은 이번 주가 다음 주에 남긴 숙제를 중심으로 묶는다.
8. 숨은정보형은 날짜·시황·가격 브리핑이 아니라 사람들이 겉만 보고 놓치는 기업의 돈 버는 구조, 산업 변화, 실적의 질, 공시·IR의 반복 신호를 설명한다.
"""

GLOBAL_SCRIPT_META_BAN_RULES = """
## 전체 대본 공통 메타 문구 금지 규칙
1. 포맷명, 코너명, 작가용 설명, 작업 과정을 말하지 않는다.
2. 분석 방법을 설명하지 말고 바로 방송 문장으로 들어간다.
3. 전환문을 고정 후렴구처럼 반복하지 않는다.
4. 프롬프트의 규칙 문장이나 예시 문장을 완성 대본에 복사하지 않는다.
5. 자막, 화면, 카메라, 오프닝, 마무리 같은 제작 지시는 출력하지 않는다.
"""

GLOBAL_RETENTION_FLOW_RULES = """
## 전체 대본 공통 유지율·흐름 규칙
1. 첫 문장은 숫자와 흐름의 충돌로 궁금증을 만든다.
2. 첫 블록의 큰 질문은 중반에서 더 깊어지고 마지막에서 확인 기준으로 회수되어야 한다.
3. 각 블록은 앞 블록의 결론을 자연스럽게 받아 다음 블록의 이유로 이어진다.
4. 흐름을 위해 새 연결문을 반복하지 말고, 내용 자체가 이어지게 쓴다.
5. 중반에 데이터 나열로 퍼지면 안 된다. 모든 데이터는 첫 질문에 답하는 역할을 가져야 한다.
"""


GLOBAL_BROADCAST_MIXED_TONE_RULES = """
## 전체 대본 공통 방송 구어체 리듬 규칙
1. 구어체는 어미만 바꾸는 것이 아니다. 긴 문장을 나누고, 설명과 짧은 판단을 섞어 말하듯 쓴다.
2. 설명은 편하게 풀고, 중요한 판단은 짧게 눌러 말한다.
3. 전부 '~요'로만 끝내지 말고, 필요한 곳에서는 '~다'와 '~봐야 합니다'를 섞는다.
4. '입니다/합니다'가 이어지면 딱딱해진다. 다만 억지로 바꾸지 말고 말로 읽었을 때 자연스러운 호흡을 우선한다.
5. 특정 말맛 문구를 반복하지 않는다. 같은 표현이 두세 번 보이면 다른 말로 풀어라.
6. 문장을 명사형 조각으로 끊지 마라. 모든 문장은 실제 방송에서 읽을 수 있게 완성된 서술어로 끝내라.
7. 과장된 대결 비유로 흐름을 만들지 마라. 매도와 매수의 충돌은 숫자로 차분하게 설명하라.
8. 감탄형 강조어를 남발하지 마라. 숫자가 크면 숫자 자체를 차분히 해석하라.
9. 문단 끝을 명사형 조각문으로 끊지 마라. 방송 대본은 모든 문장이 완성된 서술어로 끝나야 한다.
10. 리포트식 종결어를 반복하지 마라. "수치입니다", "기록했습니다", "해당합니다", "시사합니다", "증거입니다", "대목입니다", "형국입니다", "흐름입니다"가 이어지면 실패다.
11. 숫자를 말한 뒤에는 바로 해석하지 말고, 한 번 끊어라. 예: "삼십일만 팔천 원입니다. 그런데 여기서 봐야 할 건 가격이 아니라 거래량이에요."처럼 호흡을 만든다.
12. 시청자가 옆에서 듣는 느낌으로 써라. "보시면", "여기서", "이러면", "그런데", "다만"을 자연스럽게 쓰되 같은 연결어를 반복하지 마라.
"""

def _inject_global_long_talk_rules(template):
    """영상 대본 포맷에 12,000자·30~50블록·구어체·균형 규칙을 자동 주입한다."""
    combined_rules = (GLOBAL_LONG_TALK_RULES.rstrip() + "\n" +
                      GLOBAL_BEHIND_TRACKING_RULES.rstrip() + "\n" +
                      GLOBAL_BALANCED_TONE_RULES.rstrip() + "\n" +
                      GLOBAL_FORMAT_IDENTITY_RULES.rstrip() + "\n" +
                      GLOBAL_SCRIPT_META_BAN_RULES.rstrip() + "\n" +
                      GLOBAL_RETENTION_FLOW_RULES.rstrip() + "\n" +
                      GLOBAL_BROADCAST_MIXED_TONE_RULES.rstrip())
    key = "\n[수집 데이터]\n{raw_data}"

    if "## 전체 대본 공통 분량·구어체 규칙" in template:
        needs_behind = "## 전체 대본 공통 심층 흐름 강화 규칙" not in template
        needs_balanced = "## 전체 대본 공통 균형·신뢰도·구어체 보강 규칙" not in template
        needs_identity = "## 전체 대본 공통 포맷별 정체성 규칙" not in template
        needs_retention = "## 전체 대본 공통 유지율·반복금지·흐름 규칙" not in template
        needs_meta_ban = "## 전체 대본 공통 제작용 문구 금지 규칙" not in template
        needs_mixed_tone = "## 전체 대본 공통 방송 구어체 리듬 규칙" not in template
        if not needs_behind and not needs_balanced and not needs_identity and not needs_retention and not needs_meta_ban and not needs_mixed_tone:
            return template
        extra_parts = []
        if needs_behind:
            extra_parts.append(GLOBAL_BEHIND_TRACKING_RULES.rstrip())
        if needs_balanced:
            extra_parts.append(GLOBAL_BALANCED_TONE_RULES.rstrip())
        if needs_identity:
            extra_parts.append(GLOBAL_FORMAT_IDENTITY_RULES.rstrip() + "\n" +
                      GLOBAL_SCRIPT_META_BAN_RULES.rstrip() + "\n" +
                      GLOBAL_RETENTION_FLOW_RULES.rstrip())
        if needs_mixed_tone:
            extra_parts.append(GLOBAL_BROADCAST_MIXED_TONE_RULES.rstrip())
        extra = "\n".join(extra_parts)
        if key in template:
            return template.replace(key, "\n" + extra + "\n\n[수집 데이터]\n{raw_data}")
        return template.rstrip() + "\n\n" + extra

    if key in template:
        return template.replace(key, "\n" + combined_rules + "\n\n[수집 데이터]\n{raw_data}")
    return template.rstrip() + "\n\n" + combined_rules

# ──────────────────────────────────────────────
# 포맷 선택용 딕셔너리 + OpenAI 자동 생성 도우미
# ──────────────────────────────────────────────
def _append_blending_cta(template):
    """프롬프트 끝에 블렌딩 CTA를 한 번만 붙인다."""
    if "무료 에이아이 포트폴리오 리포트" in template or "## CTA 삽입 규칙" in template:
        return template
    return template.rstrip() + "\n\n" + BLENDING_CTA.lstrip()


# 모든 영상 대본 포맷은 기본적으로 12,000자 내외 / 구분자 30~50개 / 강한 구어체를 목표로 한다.
SCRIPT_PROMPT_TEMPLATE = _inject_global_long_talk_rules(SCRIPT_PROMPT_TEMPLATE)
JUNGPRO_PROMPT_TEMPLATE = _inject_global_long_talk_rules(JUNGPRO_PROMPT_TEMPLATE)
CLOSING_PROMPT_TEMPLATE = _inject_global_long_talk_rules(CLOSING_PROMPT_TEMPLATE)
INTRADAY_PROMPT_TEMPLATE = _inject_global_long_talk_rules(INTRADAY_PROMPT_TEMPLATE)
MONDAY_PROMPT_TEMPLATE = _inject_global_long_talk_rules(MONDAY_PROMPT_TEMPLATE)
WEEKLY_PROMPT_TEMPLATE = _inject_global_long_talk_rules(WEEKLY_PROMPT_TEMPLATE)

# 긴급 장중 속보를 포함한 영상 대본류는 사용 목적상 모두 장문 대본 기준으로 맞춘다.
# 영상 목표가 전환이므로 장중 속보에도 동일한 전환 CTA를 결합한다.
SCRIPT_PROMPT_TEMPLATE = _append_blending_cta(SCRIPT_PROMPT_TEMPLATE)
JUNGPRO_PROMPT_TEMPLATE = _append_blending_cta(JUNGPRO_PROMPT_TEMPLATE)
CLOSING_PROMPT_TEMPLATE = _append_blending_cta(CLOSING_PROMPT_TEMPLATE)
INTRADAY_PROMPT_TEMPLATE = _append_blending_cta(INTRADAY_PROMPT_TEMPLATE)
MONDAY_PROMPT_TEMPLATE = _append_blending_cta(MONDAY_PROMPT_TEMPLATE)
WEEKLY_PROMPT_TEMPLATE = _append_blending_cta(WEEKLY_PROMPT_TEMPLATE)

SCRIPT_FORMATS = {
    "이면추적 (평일 심층, 약 12,000자)": SCRIPT_PROMPT_TEMPLATE,
    "정프로용 개인채널 (친근형, 약 12,000자)": JUNGPRO_PROMPT_TEMPLATE,
    "장마감 브리핑 (장문형, 약 12,000자)": CLOSING_PROMPT_TEMPLATE,
    "장중 속보 (장문형, 약 12,000자)": INTRADAY_PROMPT_TEMPLATE,
    "월요일장 프리뷰 (주말, 약 12,000자)": MONDAY_PROMPT_TEMPLATE,
    "주간 결산 (주말, 약 12,000자)": WEEKLY_PROMPT_TEMPLATE,
    "숨은정보형 (날짜무관, 약 12,000자)": SCRIPT_PROMPT_TEMPLATE,
}

SCRIPT_FORMAT_ALIASES = {
    "이면추적 (평일 심층, 8~10분)": "이면추적 (평일 심층, 약 12,000자)",
    "정프로용 개인채널 (친근형, 8~10분)": "정프로용 개인채널 (친근형, 약 12,000자)",
    "장마감 브리핑 (평일 저녁, 4~5분)": "장마감 브리핑 (장문형, 약 12,000자)",
    "장중 속보 (장중 긴급, 2~3분)": "장중 속보 (장문형, 약 12,000자)",
    "월요일장 프리뷰 (주말, 5~6분)": "월요일장 프리뷰 (주말, 약 12,000자)",
    "주간 결산 (주말, 8~10분)": "주간 결산 (주말, 약 12,000자)",
    "숨은정보형 (날짜무관, 8~10분)": "숨은정보형 (날짜무관, 약 12,000자)",
    "정보전달형 (날짜무관, 약 12,000자)": "숨은정보형 (날짜무관, 약 12,000자)",
    "정보전달형 (날짜무관, 8~10분)": "숨은정보형 (날짜무관, 약 12,000자)",
    "정보 전달형 (날짜무관, 약 12,000자)": "숨은정보형 (날짜무관, 약 12,000자)",
}


# ──────────────────────────────────────────────
# OpenAI API 직접 호출 + TXT 저장
# ──────────────────────────────────────────────
def _safe_filename_part(value):
    """윈도우 파일명에 위험한 문자를 제거한다."""
    value = str(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    return value or "result"


FINAL_SELF_CHECK = """

## 제출 전 최종 자기검증
1. [수집 데이터]에 없는 숫자, 환산 금액, 임의 계산을 쓰지 않았는지 확인하라.
2. [시장 단계]가 장전/장중이면 '종가', '오늘 종가', '오늘 마감', '장마감', '마감했습니다', '장을 마쳤습니다', '마감 기준'이 한 번도 없는지 확인하라. 현재 가격은 '자료 수집 시점에 확인된 가격', '장중 확인 가격', '현재 확인 가격'처럼 자연스럽게 말하라. '이 시각 기준 현재가' 같은 표현은 쓰지 마라.
2-1. 장전/장중의 KRX 마지막 일봉은 '최근 확정 거래일 기준'이라고만 말하고 오늘 가격처럼 취급하지 마라. 수급·외국인 지분율·공매도는 최근 확정 데이터 기준으로만 말하며, 오늘 수급은 '장 마감 뒤 확인해야 합니다'라고만 설명하라.
3. 첫 문장이 단순 사실이 아니라 서로 안 맞는 흐름에서 생기는 의문인지 확인하라.
4. 대본 전체가 하나의 질문으로 이어지고, 마지막에서 그 질문을 확인 기준으로 회수하는지 확인하라.
5. 같은 숫자와 같은 주장을 반복하지 않았는지 확인하라.
6. 구어체가 어미 치환에 그치지 않고, 긴 문장을 나눠 말하듯 읽히는지 확인하라.
6-1. "수치입니다", "기록했습니다", "해당합니다", "시사합니다", "증거입니다", "대목입니다", "형국입니다" 같은 리포트체가 반복되지 않는지 확인하라.
7. 포맷명, 제작 과정, 화면 지시, 작가용 문구가 한 줄도 없는지 확인하라.
8. CTA 뒤에 본문 핵심을 받아 마무리하는 방송 문단이 있는지 확인하라.
9. 폭포수, 총성 없는 전쟁, 방패, 창, 혈투, 피를 말리는, 역사적인, 매도 폭탄, 온몸으로 막았다, 거대한 파도, 외나무다리 승부 같은 재난·대결 비유를 쓰지 않았는지 확인하라.
10. 모든 문장을 완성형으로 끝내라. 셈, 뜻, 현장, 상황, 신호, 관건, 때문 같은 체언으로 문장을 끊지 마라.
11. 긴 리포트 문장에 '~요'만 붙이지 마라. 설명을 짧게 나누고 판단은 별도 문장으로 분리하라.
"""




def _sanitize_angle_line(text):
    """각도 후보 문장을 최종 프롬프트에 넣기 전 중립화한다.

    최종 대본에는 각도 후보의 문장, 비유, 제목을 복사하지 않는다.
    여기서는 데이터 섹션·수치·주체 정도만 남기는 용도다.
    """
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return ""
    # 기획 라벨 제거
    t = re.sub(r"^[-•\s]*(주인공 데이터|중심 데이터|핵심 질문|보조 데이터|제외할 데이터|전개|차별점)\s*:\s*", "", t)
    # 과장형 말맛 제거: 출력용 문장이 아니라 내부 메모라서 과감히 중립화한다.
    banned_phrases = [
        "그야말로", "말 그대로", "상상하기조차 힘든", "숨 막히는", "처절한", "위험천만한",
        "역사적인", "폭포수처럼", "폭포수 같은", "총성 없는 전쟁", "창과 방패", "거대한 방패",
        "거대한 창", "매도 폭탄", "모든 것을 던지고", "모든 것을 받아내고", "속절없이",
        "미래를 보는 두 개의 시선", "정면으로 충돌", "혈투", "외나무다리 승부",
    ]
    for b in banned_phrases:
        t = t.replace(b, "")
    t = re.sub(r"\s+", " ", t).strip(" -:.,")
    return t[:260]


def _angle_to_internal_brief(angle):
    """각도/흐름 후보는 최종 대본의 문장 재료가 아니라 데이터 우선순위로만 쓴다.

    이전 버전은 후보 제목·핵심 질문을 프롬프트에 넘기면서 AI가 그 문체를 따라 해
    전쟁/폭포수/방패 같은 과장 프레임이 대본에 섞였다. 이제는 중심 데이터와 보조
    데이터만 짧게 남기고, 후보 문장은 복사하지 않는다.
    """
    raw = str(angle or "").strip()
    if not raw:
        return ""

    center = ""
    support = ""
    exclude = ""
    for line in raw.splitlines():
        t = line.strip().strip('-').strip()
        if not t:
            continue
        # 제목과 핵심 질문은 문체가 강해서 최종 대본으로 새기 쉬우므로 사용하지 않는다.
        if re.match(r"^\[(?:각도|흐름)\d+\]", t):
            continue
        if t.startswith("핵심 질문") or t.startswith("전개") or t.startswith("차별점"):
            continue
        if t.startswith("중심 데이터:") or t.startswith("주인공 데이터:"):
            center = center or _sanitize_angle_line(t.split(":", 1)[1])
        elif t.startswith("보조 데이터:"):
            support = support or _sanitize_angle_line(t.split(":", 1)[1])
        elif t.startswith("제외할 데이터:"):
            exclude = exclude or _sanitize_angle_line(t.split(":", 1)[1])

    lines = []
    if center:
        lines.append("우선 확인할 데이터: " + center)
    if support:
        lines.append("보조 확인 데이터: " + support)
    if exclude:
        lines.append("억지로 넣지 않을 데이터: " + exclude)

    # 정해진 데이터 라벨이 없는 입력은 제목·훅·콘셉트일 가능성이 있으므로 전달하지 않는다.
    return "\n".join(lines[:2]).strip()


def _extract_market_phase(raw_data):
    m = re.search(r"\[시장 단계\]\s*([^\n]+)", str(raw_data or ""))
    label = m.group(1).strip() if m else ""
    if "장전" in label or "아침" in label:
        return "PREOPEN"
    if "장중" in label:
        return "INTRADAY"
    if "종료 직후" in label:
        return "AFTER_CLOSE_PENDING"
    if "장마감 이후" in label:
        return "AFTER_CLOSE"
    if "주말" in label or "휴장" in label:
        return "WEEKEND"
    return "UNKNOWN"


def _format_is_closing(format_name):
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    return "장마감" in canonical


def _effective_script_format(format_name, raw_data=None):
    """실제 시장 단계가 포맷보다 우선한다.

    장중/장전에는 사용자가 장마감 브리핑을 골라도 장마감 템플릿과 보정 규칙을 쓰지 않는다.
    """
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    if not canonical:
        canonical = next(iter(SCRIPT_FORMATS))
    phase = _extract_market_phase(raw_data)
    if _format_is_closing(canonical) and phase == "INTRADAY":
        return "장중 속보 (장문형, 약 12,000자)"
    if _format_is_closing(canonical) and phase == "PREOPEN":
        return "이면추적 (평일 심층, 약 12,000자)"
    return canonical


def _format_is_weekend(format_name):
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    return ("주말" in canonical) or ("주간" in canonical) or ("월요일장" in canonical)


def is_weekend_script_request(format_name=None, custom_topic=None):
    """주말/주간/월요일 프리뷰처럼 당일 시세 중심 자료를 쓰면 안 되는 요청인지 판단한다."""
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    topic = str(custom_topic or "")
    return (
        _format_is_weekend(canonical)
        or bool(re.search(r"(주말용|주말\s*용|주말\s*대본|주간\s*결산|휴장일|월요일\s*프리뷰)", topic))
    )



def is_information_script_request(format_name=None, custom_topic=None):
    """날짜 무관 숨은정보형처럼 가격·시황 중심 자료를 쓰면 안 되는 요청인지 판단한다."""
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    topic = str(custom_topic or "")
    return (
        "정보전달형" in canonical
        or "숨은정보형" in canonical
        or bool(re.search(r"(숨은\s*정보형|숨겨진\s*정보|겉만\s*보고|정보\s*전달형|정보형|날짜\s*무관|미리\s*뽑|교육형)", topic))
    )

def _weekend_big_picture_lock_rules():
    return """
## 주말용 큰그림 잠금 규칙
- 이 대본은 당일 장 브리핑이 아니다. 화요일에 미리 뽑아도 화요일 얘기가 주인공이 되면 실패다.
- 수집 데이터의 [수집 시각]과 당일 가격·뉴스는 주말 기준 최신 자료가 아니다. 금요일 확정 전까지의 임시 스냅샷으로만 취급하라.
- 화요일·수요일·목요일에 미리 만든 주말 대본이라면, 그날 데이터로 이번 주 결론을 낸 것처럼 쓰지 마라. 주말 기준 최신 자료는 금요일 확정 데이터라는 점을 전제로 둔다.
- "오늘", "오늘 하루", "오늘 장", "오늘 장중", "오늘 현재가", "오늘 수급", "오늘 종가", "오늘 마감" 표현을 남발하지 마라. 필요하면 "주간 뉴스에서 반복된 질문", "다음 주 일정", "직전 확정 거래일 기준"으로 낮춰 말하라.
- 구성 비중은 주간 뉴스 헤드라인 35%, 다음 주 일정·실적발표·경제지표 35%, 이번 주가 남긴 질문 30% 정도로 잡는다.
- 첫 블록은 당일 등락률이나 당일 수급으로 시작하지 마라. 주말에 시청자가 정리해야 할 큰 질문이나, 금요일 확정 뒤 다시 봐야 할 핵심 변수로 시작하라.
- 날짜별 시세를 길게 읽지 마라. 현재 확보된 임시 데이터는 '이 주제를 주말에 왜 정리할지'를 잡는 예비 힌트로만 쓴다.
- 아직 오지 않은 주말 날짜에 그날의 주가·뉴스·수급이 확인된 것처럼 말하지 마라.
- "반드시 확인해야 할 세 가지", "월요일 장이 열리기 전에", "다음 주 첫 거래일에 꼭" 같은 숙제형 문장을 반복하지 마라.
- 주말용 감성은 짧게만 쓴다. 불안하다, 무겁다, 걱정된다를 여러 번 반복하면 실패다.
""".strip()


def _market_phase_prompt_rules(raw_data, format_name=None):
    phase = _extract_market_phase(raw_data)
    # 포맷보다 실제 수집 시각이 우선이다. 장중에 장마감 포맷을 선택해도 잠금을 풀지 않는다.
    if phase in ("PREOPEN", "INTRADAY"):
        return """
## 시장 단계 잠금 규칙 — 장전/장중 대본
- 지금 대본은 장마감 브리핑이 아니다.
- 사용자가 장마감 브리핑 포맷을 골랐더라도, 실제 수집 단계가 장전/장중이면 '장중 임시 브리핑'으로 작성하라.
- 장마감 브리핑 특유의 종가 위치, 마감 심리, 오늘 장을 마쳤다는 전개를 쓰지 마라.
- 종가, 오늘 종가, 오늘 마감, 장마감, 마감했습니다, 장을 마쳤습니다, 마감 기준이라는 표현을 한 번도 쓰지 마라.
- KRX 일봉의 마지막 값은 '최근 확정 거래일 기준' 또는 '직전 거래일 기준'으로만 말하라. '종가'라는 단어도 쓰지 마라.
- 현재가 데이터는 '자료 수집 시점에 확인된 가격', '장중 확인 가격', '지금 확인되는 가격'처럼 자연스럽게 말하라.
- '이 시각 기준 현재가', '이 시각 기준 가격'처럼 어색한 표현은 쓰지 마라.
- 투자자별 수급·외국인 지분율·공매도는 '최근 확정 데이터 기준'으로만 말하라.
- 오늘 수급을 언급해야 한다면 반드시 '장 마감 뒤 확인해야 합니다'라고만 말하라.
- 장중 대본에서 결론을 '오늘 장을 마쳤다'처럼 쓰면 대본 무효다.
"""
    if phase == "WEEKEND":
        return """
## 시장 단계 잠금 규칙 — 주말/휴장 대본
- 실시간·장중인 것처럼 말하지 마라.
- 가격과 수급은 최근 확정 거래일 기준임을 분명히 하라.
- 오늘 장이 열렸거나 끝났다는 표현을 쓰지 마라.
""" + "\n" + _weekend_big_picture_lock_rules()
    if _format_is_closing(format_name):
        return """
## 시장 단계 잠금 규칙
- [시장 단계]가 장마감 이후일 때만 오늘 정규장 종가·마감 위치를 중심으로 쓸 수 있다.
- 당일 수급이 원자료에서 확정되지 않았으면 최근 확정 데이터와 구분하라.
"""
    if phase == "AFTER_CLOSE_PENDING":
        return """
## 시장 단계 잠금 규칙 — 정규장 종료 직후
- 현재가가 시간외/대체거래소 가격일 수 있으므로 정규장 종가와 현재가를 섞지 마라.
- 장마감 브리핑 포맷이 아니면 종가 위치만으로 시작하지 말고, 최근 확정 수급과 가격 흐름의 충돌을 중심으로 써라.
"""
    return """
## 시장 단계 잠금 규칙
- [시장 단계]와 맞지 않는 시제 표현을 쓰지 마라.
- 장전·장중이면 종가/장마감 확정 표현 금지, 장마감 이후이면 현재가와 정규장 종가를 구분하라.
"""


def _clean_user_topic_memo(topic):
    """사용자가 직접 넣은 대본 주제/메모를 프롬프트용으로 정리한다."""
    s = str(topic or "").strip()
    if not s:
        return ""
    # 화면 지시·마크다운 제목처럼 새기 쉬운 형식만 걷어내고, 사용자가 쓴 사실/숫자는 보존한다.
    s = re.sub(r"[\r\n]+", "\n", s)
    s = re.sub(r"^\s*#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s[:2000].strip()


def _topic_context_rules(topic_memo):
    """사용자 메모 속 날짜/주말 의도를 규칙으로 바꾼다. 최종 대본 문장은 만들지 않는다."""
    s = str(topic_memo or "")
    if not s:
        return ""

    rules = []
    is_weekend_request = bool(re.search(r"(주말용|주말\s*용|주말\s*대본|주간\s*결산|휴장일)", s))

    m = re.search(r"대본\s*기준\s*날짜\s*:\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:\s*\(([^)]+)\))?", s)
    if m:
        y, mo, d = map(int, m.group(1, 2, 3))
        weekday_label = m.group(4) or ""
        try:
            from datetime import date
            target = date(y, mo, d)
            if target.weekday() >= 5:
                is_weekend_request = True
                weekday_label = weekday_label or ("토요일" if target.weekday() == 5 else "일요일")
        except Exception:
            pass
        if weekday_label:
            rules.append(f"- 대본 기준 날짜는 {m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} {weekday_label}이다.")

    if is_weekend_request:
        rules.append(_weekend_big_picture_lock_rules())

    if not rules:
        return ""
    return "## 사용자 날짜·주말 의도 잠금\n" + "\n".join(rules)


def _compact_format_rules(format_name):
    """포맷별 핵심 전개만 짧게 반환한다. 긴 규칙 나열로 모델을 흐리지 않는다."""
    name = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    if "정보전달형" in name or "숨은정보형" in name:
        return """
## 이번 포맷
- 숨은정보형 대본이다. 날짜와 상관없이 미리 뽑아도 어색하지 않아야 한다.
- 장중 속보, 장마감 브리핑, 주말 프리뷰, 주간 결산처럼 쓰지 마라.
- 현재가, 종가, 등락률, 시가, 고가, 저가, 거래량, 당일 수급, 공매도 일별 흐름을 중심으로 전개하지 마라.
- "오늘", "어제", "이번 주말", "다음 장", "장중", "장마감", "마감했습니다" 같은 시간대 중심 표현을 쓰지 마라.
- 첫 블록은 가격 질문이 아니라 숨은 구조 질문으로 시작한다. 예: "사람들은 보통 실적 숫자만 보는데요. 그런데 진짜 봐야 할 건 이 회사가 어디서 돈을 벌고, 어디서 막히고 있느냐입니다."처럼 연다.
- 핵심은 사람들이 겉만 보고 놓치는 부분이다. 사업 구조, 실적의 질, 산업 변화, 공시·IR에서 반복되는 방향, 투자자가 오해하기 쉬운 지점을 쉽게 설명한다.
- 시황 해설보다 숨은 구조를 풀어주는 설명에 가깝게 쓴다. 다만 강의처럼 딱딱하게 말하지 말고 실제 유튜브 구어체로 풀어라.
- 자료에 있는 실적 숫자는 사용할 수 있지만, 주가 반응이나 당일 가격 해석으로 연결하지 마라.
- 시가총액·밸류에이션은 원자료 표시값만 참고로 말한다. 새 계산이나 환산 금액을 만들지 마라.
- 체크포인트는 "다음 장 가격"이 아니라 "이 종목을 이해할 때 계속 봐야 할 정보 기준"으로 제시한다.
- CTA 직전에는 "내 계좌에서도 겉으로 보이는 수익률보다 먼저 봐야 할 구조가 있다. 비중과 손실 구간부터 확인해야 한다"는 흐름으로 자연스럽게 연결한다.
"""
    if "정프로용" in name:
        return """
## 이번 포맷
- 정프로 개인채널 포맷이다. 다른 포맷과 같은 시작이면 실패다.
- 첫 블록은 후킹만 한다. 첫 블록에는 "안녕하세요", "반갑습니다", "정프로입니다" 같은 인사를 절대 넣지 않는다.
- 첫 블록 바로 다음에 ---< 구분자를 넣고, 두 번째 블록 첫 문장에서 반드시 정프로 인사를 한다.
- 두 번째 블록 첫 문장은 "자, 반갑습니다, 정프로입니다.", "반갑습니다, 정프로입니다." 또는 "안녕하세요, 정프로입니다."로 시작한다. 앞에 다른 설명 문장을 붙이면 실패다.
- 정프로 대본은 항상 궁금증 유발 후 인사다. 인사 후 궁금증 유발 순서로 쓰지 마라.
- 인사 뒤에는 바로 오늘 시청자가 느꼈을 감정과 오늘 같이 볼 질문을 붙인다. 예: "반갑습니다, 정프로입니다. 오늘 숫자만 보면 오른 것 같은데, 마음이 편하지 않은 분들 많으셨을 거예요."
- 인사는 한 번만 한다. 세 번째 블록부터는 바로 가격·거래량·수급 해석으로 들어간다.
- 첫 블록은 3~5문장이다. 숫자 충돌, 시청자가 느낄 헷갈림, 끝까지 봐야 할 이유를 짧게 넣는다.
- 정프로 첫 블록은 숫자 브리핑이 아니다. 핵심 숫자 1~2개만 쓰고, 나머지는 "왜 오른 날인데 편하지 않은지"를 말로 걸어라.
- 첫 블록은 시청자 감정을 세게 붙잡되 과장하지 않는다. "계좌는 빨간데 왜 마음이 불안한지"처럼 실제 고민을 숫자와 연결하라.
- 첫 블록 첫 문장에는 종목명을 반드시 넣는다.
- 첫 블록 마지막은 반드시 질문으로 끝낸다. 첫 블록에서 답을 설명하지 말고 "이 윗꼬리가 단순 차익실현인지, 아니면 큰돈이 빠지는 신호인지"처럼 다음 블록을 보게 만들어라.
- 첫 블록이 "실적은 좋았습니다. 그런데..." 같은 설명문으로 바로 들어가면 실패다. 정프로는 먼저 궁금증을 만들고, 그다음 인사한다.
- 시청자가 헷갈릴 지점을 먼저 받아주고, 숫자는 짧게 나눠서 설명한다.
- 사업부·사업보고서·회사 구조 설명으로 초반을 열지 마라.
- 어제 얘기는 시청자 공감과 오늘 흐름의 배경으로만 짧게 쓴다. 어제 실적·급락·서킷브레이커·외국인 매도를 길게 다시 해설하지 마라.
- 인사 뒤 3블록 안에 오늘의 현재 흐름과 오늘 확인 기준으로 넘어가라.
- 리포트 낭독 금지. 옆에서 기준 잡아주는 말투로 쓴다.
"""
    if "장마감" in name:
        return """
## 이번 포맷
- 장마감 브리핑이다. 단, 뉴스처럼 종가부터 줄줄 읽지 마라.
- 장마감이어도 방송 구어체다. 앵커 멘트, 증권사 리포트, 뉴스 기사 문장처럼 쓰면 실패다.
- 종가·등락률을 읽을 때도 "수치입니다"가 아니라 "이렇게 보면 됩니다", "여기서 봐야 할 건 이겁니다"처럼 말로 풀어라.
- 첫 블록은 "오늘 오른 숫자"와 "그 반등을 의심하게 만드는 숫자"를 바로 충돌시킨다.
- 첫 블록 첫 문장에는 종목명을 반드시 넣는다.
- 장마감 후킹은 내일 장을 보게 만드는 힘이 있어야 한다. 마지막 문장은 "내일 첫 한 시간에 무엇을 확인할지"로 이어질 기대감을 만들어라.
- 사업부·사업보고서·회사 구조 설명은 초반 3블록 안에 넣지 않는다.
- 종가·등락률·거래량·시간대별 고점/저점은 한 장면을 설명하는 재료로만 쓴다.
- 고점·저점·첫 체결권·마지막 체결권을 한 문단에 모두 넣지 마라. 한 블록에는 가격 장면 하나만 잡고, 다음 블록에서 해석한다.
- 오늘 수급이 원자료에서 확정되어 있으면 말해도 되지만, 확정/미확정을 반드시 구분한다.
"""
    if "장중" in name:
        return """
## 이번 포맷
- 장중 속보다. 현재가와 거래량 유지 여부가 중심이다.
- 장중 속보도 구어체다. 급보 기사처럼 쓰지 말고, 시청자 옆에서 화면을 같이 보며 말하듯 써라.
- 문장은 더 짧게 쓴다. 한 문장에 가격·거래량·수급 판단을 몰아넣지 마라.
- 첫 블록은 "지금 오르는 중인데 따라붙어도 되는가"라는 긴장감을 만든다.
- 첫 블록 첫 문장에는 종목명을 반드시 넣는다.
- 사업부·사업보고서·회사 구조 설명은 초반 3블록 안에 넣지 않는다. 장중 속보의 초반 주인공은 현재가, 장중 고점·저점, 거래량, 오늘 수급 미확정 원칙이다.
- 어제 실적·급락·외국인 매도 이야기는 배경으로 좋지만 2~3블록 안에서 끝낸다.
- 첫 5블록 안에 오늘 가격, 오늘 장중 고점·저점 또는 시간대별 흐름, 거래량, 오늘 수급 미확정 원칙을 넣는다.
- 시장 특이상황 뉴스는 발행시각과 사건일을 구분한다. 오늘 발동 확정 근거가 없으면 오늘 서킷브레이커가 걸렸다고 쓰지 않는다.
- 첫 문장은 리포트체가 아니라 말로 시작한다. 예: "지금 삼성전자, 가격만 보면 아직 마음 놓기 어렵습니다."
- 오늘 수급은 확정된 것처럼 말하지 말고 "장 마감 뒤 확인해야 합니다"라고만 말한다.
- 종가·오늘 마감·장을 마쳤다는 표현은 쓰지 않는다.
"""
    if "월요일" in name:
        return """
## 이번 포맷
- 월요일장 프리뷰다. 금요일이 남긴 상태와 월요일 첫 30분~1시간의 갈림길을 설명한다.
- 월요일 프리뷰도 구어체다. 주말 리포트처럼 "전망됩니다/예상됩니다"를 반복하지 마라.
- 시청자가 월요일 아침에 실제로 뭘 먼저 볼지 알려주는 말투로 쓴다.
- 첫 블록은 월요일 아침에 바로 봐야 할 가격·거래량·수급 기준을 궁금하게 만들되, 화요일 등 제작일 당일 얘기로 시작하지 마라.
- 주말 사이 새 뉴스는 수동 메모에 있는 것만 다룬다.
- 실시간·현재가처럼 말하지 않는다.
""" + "\n" + _weekend_big_picture_lock_rules()
    if "주간" in name:
        return """
## 이번 포맷
- 주말 큰그림 대본이다. 날짜별 사건 나열이나 당일 시세 복기가 아니다.
- 화요일·수요일·목요일에 미리 뽑아도 어색하지 않아야 한다. 제작 당일 얘기가 주인공이면 실패다.
- 주간 뉴스 헤드라인, 다음 주 일정·실적발표·경제지표, 이번 주가 남긴 질문을 중심으로 묶어라.
- "이번 주 시장은 끝났다"처럼 확정 결산하지 말고, 주말에 다시 봐야 할 큰 질문을 세운다.
- 첫 블록은 가격 예측이 아니라 "왜 이 이슈를 주말에 다시 봐야 하는가"로 시작한다.
""" + "\n" + _weekend_big_picture_lock_rules()
    return """
## 이번 포맷
- 심층형 대본이다. 겉으로 보이는 등락보다 가격·거래량·수급·환율·글로벌 흐름의 엇박자를 파고든다.
- 모든 데이터를 순서대로 읽지 말고, 하나의 큰 질문에 필요한 것만 골라 배치한다.
- 개인채널 상담 톤이나 인사말 없이, 이면을 추적하는 톤으로 쓴다. 친절하되 날카로워야 한다.
- 개인채널식 인사말이나 진행자 이름 인사는 이 포맷에서 절대 쓰지 않는다.
- "같이 볼게요/짚어보겠습니다/살펴보겠습니다" 같은 진행 멘트보다 바로 데이터와 해석을 붙여라.
- 이면추적은 공포몰이가 아니다. "무섭다/기형적이다/패대기친다"보다 "어떤 돈이 빠지고 어떤 돈이 받쳤는지"를 차분하게 추적한다.
- 첫 질문은 강하게 던지되, 본문은 단정하지 말고 반대 가능성도 짧게 짚는다.
- 외국인·개인·금융투자를 악당처럼 묘사하지 마라. 주체별 자금 성격과 지속 가능성으로 설명하라.
- "온몸으로 막았다", "큰 파도", "험난한 과정", "와르르 무너졌다", "매물 폭탄", "찐바닥" 같은 표현은 쓰지 않는다.
"""


def _build_compact_script_prompt(stock_name, raw_data, format_name, angle=None, custom_topic=None):
    """실제 대본 생성용 압축 프롬프트를 만든다."""
    stock_label = stock_name or "해당 종목"
    phase_rules = _market_phase_prompt_rules(raw_data, format_name).strip()
    format_rules = _compact_format_rules(format_name).strip()
    topic_memo = _clean_user_topic_memo(custom_topic)
    topic_rules = _topic_context_rules(topic_memo).strip()
    brief = _angle_to_internal_brief(angle)

    parts = [f"""
당신은 한국 주식 유튜브 채널의 메인 작가다.
아래 [수집 데이터]만 근거로 {stock_label} 대본을 작성하라.
완성 대본만 출력한다. 제목, 목차, 해설, 마크다운, 화면 지시는 쓰지 않는다.

## 최우선 목표
- 시청자가 그대로 들을 수 있는 방송 구어체 대본.
- 리포트가 아니라 말이다. 숫자 낭독이 아니라 흐름 설명이다.
- 모든 포맷은 예외 없이 구어체다. 이면추적, 장마감, 장중, 월요일장, 주간 결산 모두 실제로 입으로 읽는 말이어야 한다.
- 최종 분량은 10,000~12,000자, ---< 구분자는 30~50개.
- 첫 10초는 자극이 아니라 숫자의 충돌로 붙잡는다. 겁주지 말고, "왜 따로 확인해야 하는지"만 또렷하게 말한다.
- 말투 우선순위는 항상 1순위다. 숫자가 많아도 보고서 문장으로 쓰면 실패다.
- 한 블록은 보통 "말문 → 숫자 → 쉬운 뜻 → 짧은 판단" 순서로 쓴다.
- 문장 끝은 반드시 섞는다. "~요/~죠/~거든요/~잖아요"가 실제로 들어가야 하고, 짧은 "~다/합니다"는 판단을 박을 때만 쓴다.
- "오늘 삼성전자는 ... 수치입니다"처럼 시작하지 마라. "자, 삼성전자 이 숫자는 그냥 가격만 보면 안 돼요."처럼 사람 말로 열어라.

## 전체 포맷 공통 톤: 담백한 구어체
- 말투는 친한 사람이 옆에서 차분히 설명하는 느낌이다. 흥분한 진행자, 공포 유튜버, 드라마 내레이션처럼 쓰면 실패다.
- 후킹은 세게 쓰되 오버하지 않는다. 강한 말 대신 정확한 숫자와 주체를 앞에 둔다.
- "함정", "유혹", "빨간불", "큰손들이 짐을 싼다", "피를 말렸다", "눈앞이 캄캄했다", "가슴이 철렁했다", "멱살 잡고 끌어올렸다", "샴페인을 터뜨리면 안 된다", "잔인하게 보여준다", "입이 떡 벌어진다", "구명조끼를 입고 뛰어내린다" 같은 감정 과잉 표현은 쓰지 마라.
- "무조건", "절대", "너무나", "정말", "충격", "공포", "아슬아슬", "외줄 타기", "모래 위의 성", "중력", "역주행", "초라한", "매몰차게", "내다 버렸다", "던졌다"처럼 판단을 과장하는 단어를 피한다.
- "독야청청", "귀추가 주목됩니다", "향방", "초미의 관심사", "이목이 쏠립니다", "촉각을 곤두세우고", "관건입니다", "분수령입니다" 같은 뉴스·칼럼식 관용어를 쓰지 마라. 쉬운 말로 풀어라.
- 외국인·기관·개인이 사고팔았다는 말은 "순매도했다", "팔았다", "줄였다", "순매수했다", "샀다", "늘렸다" 정도로만 말한다.
- 시청자를 겁주거나 혼내지 마라. "안심하면 안 됩니다", "큰일납니다"보다 "아직 확인할 게 남아 있습니다"라고 말한다.
- 감성 터치는 조금 넣어도 된다. 다만 한 블록에 한 문장 이하로만 쓰고, 바로 숫자나 확인 기준으로 돌아와라. 감정 문장으로 오프닝을 길게 끌지 마라.
- 감정 문장은 한 블록에 0~1문장만 쓴다. 시청자 공감은 살리되, 공포·분노·드라마로 밀지 마라.
- 이 대본의 힘은 숫자, 순서, 해석의 선명함이고, 감성은 그 사이에 숨 쉴 틈을 주는 정도다.

## 모든 포맷 공통 구어체 잠금
- 기사체, 리포트체, 애널리스트 보고서체, 공시 요약체 금지.
- "~한 수치입니다", "~로 나타났습니다", "~를 기록했습니다", "~로 분석됩니다", "~로 판단됩니다"를 반복하면 실패다.
- 아래 종결 표현은 대본에 쓰지 마라:
  수치입니다, 기록했습니다, 나타났습니다, 해당합니다, 시사합니다, 분석됩니다, 판단됩니다, 전망됩니다, 확인됩니다, 관측됩니다, 풀이됩니다.
- 숫자는 읽고 끝내지 말고, 바로 쉬운 말로 뜻을 풀어라.
- 긴 문장에 "~요"만 붙이지 마라. 긴 문장을 둘로 쪼개고, 설명과 판단을 나눠라.
- 시청자에게 직접 말하라. "여기서 봐야 할 건", "쉽게 말하면", "이러면", "그래서 다음 장에서는" 같은 실제 말의 호흡을 써라.
- 단, 억지 예능 말투나 과한 반말은 쓰지 마라. 친근하지만 믿을 수 있는 설명이어야 한다.

## 실제 말맛 강제 규칙
- 대본 전체가 "~습니다" 설명문으로 흐르면 실패다. 각 블록마다 말로 여는 문장과 짧은 판단 문장을 섞어라.
- 블록 전환에서 "자,", "그러니까", "이게요", "여기서 중요한 건", "쉽게 말하면", "다시 말해서", "그럼 이걸 어떻게 봐야 하냐면" 같은 자연스러운 말문을 적당히 섞어라.
- 종결은 반드시 섞는다. "~요", "~예요/~에요", "~거든요", "~잖아요", "~죠", 짧은 "~다/합니다"를 함께 써라.
- 한 블록 안에서도 리듬을 바꿔라. 예: 설명은 "~요/거든요", 기준 제시는 짧게 "~다/합니다", 시청자에게 붙이는 말은 "~죠/잖아요"로 섞는다.
- 단, 같은 전환어를 매 블록 반복하지 마라. "자,"는 중요한 전환에서만 쓰고, "그러니까"는 해석을 정리할 때만 쓴다.
- "자, 여기서 중요한 건 거래량이에요."처럼 말문을 열고, 다음 문장에서 숫자를 붙이고, 마지막 문장에서 짧게 판단하라.
- "이게 왜 중요하냐면요." 같은 문장으로 숫자와 해석 사이에 숨 쉴 틈을 만들어라.
- 너무 공손한 방송 리포트처럼 "확인해 보겠습니다/살펴보겠습니다/짚어보겠습니다"만 반복하지 마라. "같이 볼게요", "여기부터 봐야 해요", "이건 따로 봐야 합니다"처럼 섞어라.
- 블록마다 최소 한 문장은 실제 입말이어야 한다. 예: "이러면 바로 믿기 어렵죠.", "여기서 방향이 갈립니다.", "이 숫자는 그냥 넘기면 안 돼요."
- 하지만 모든 문장을 "~요"로 끝내지 마라. 설명은 부드럽게, 판단은 짧게 끊는다.
- 숫자를 말한 뒤에는 반드시 시청자식 번역을 붙인다. 예: "오조 원을 팔았다"에서 끝내지 말고, "이건 하루짜리 변덕이 아니라 비중을 줄이는 흐름에 가깝습니다."처럼 풀어라.
- 한 문장에 숫자 두 개 이상을 몰아넣지 마라. 숫자 하나, 뜻 하나, 판단 하나로 나눈다.
- 첫 문장은 특히 숫자 과밀 금지다. 종가, 등락폭, 시가, 고가, 저가, 첫 체결권, 마지막 체결권을 한 문장에 몰아넣지 마라. 첫 문장은 숫자 1~2개와 이상한 흐름만 남기고, 나머지 숫자는 다음 블록으로 넘겨라.
- 소수점은 AI 음성 낭독 안정성을 위해 "쩜"으로 쓴다. 예: "삼쩜팔퍼센트", "사십육쩜칠오퍼센트". 단, 소수점 숫자 자체를 너무 많이 쓰지 말고 필요한 핵심 숫자만 남겨라.
- "굉장히/엄청/거대한/강력한/완전히/확실히" 같은 강조 부사를 반복하면 실패다. 강조는 숫자가 하게 둔다.

## 반드시 지킬 규칙
1. [수집 데이터]와 사용자 메모에 없는 숫자·금액·날짜·뉴스를 만들지 마라.
2. "데이터 없음" 항목은 언급하지 마라. "실험적" 데이터는 보조로만 써라.
3. 매수·매도 지시, 목표가, 확률 수치 금지. 다음 장 확인 기준으로 말하라.
3-1. 시가총액·상장주식수는 [수집 데이터]에 있는 원자료 표시값만 사용하라. 종목의 규모감과 시장 영향력을 설명하는 용도로만 쓰고, 지분율·주식수·현재가를 곱해서 새 금액을 만들지 마라.
3-1-1. 시가총액·상장주식수·거래대금을 한 블록에 통째로 낭독하지 마라. 필요한 경우 "워낙 큰 종목이라 시장 체감에 영향을 준다"처럼 의미만 짧게 풀고, 숫자는 핵심 하나만 써라.
3-2. 뉴스 제목·요약은 이슈 감지용 근거다. 수집 데이터의 뉴스 제목·요약에 없는 매출·영업이익·컨센서스 숫자나 세부 내용을 만들지 마라. 실적 숫자는 DART/뉴스 제목·요약에 들어온 경우에만 말한다.
3-3. [시장 특이상황]에 서킷브레이커, 사이드카, VI, 거래정지, 거래재개, 급락 뉴스가 있으면 단순 종목 이슈보다 시장 전체 충격을 먼저 분리해서 설명하라. 단, 발동 시간·단계·지수 하락률·거래정지 사유는 수집 데이터에 있는 표현만 사용하고 새로 만들지 마라.
3-3-1. [시장 특이상황]의 시각은 기사 발행시각일 수 있다. 장중 대본에서는 수동 메모나 공식 실시간 자료에 오늘 발동이 명시되지 않으면 "오늘 서킷브레이커가 걸렸다", "오늘 사이드카가 발동됐다"라고 말하지 마라.
3-3-2. 같은 사건이 다음 날 기사로 다시 뜬 경우에는 "전일 시장 특이상황", "최근 시장 충격"으로만 말한다. 기사 발행일을 사건 발생일처럼 바꾸지 마라.
4. 첫 블록은 단순 질문 금지. 담백한 후킹 3단 구조로 시작하라.
   ① 반전 숫자: 겉으로 좋아 보이는 숫자와 안 좋은 숫자를 바로 부딪혀라.
   ② 확인 이유: "이게 왜 따로 확인해야 하는지"를 한 문장으로 걸어라.
   ③ 시청 약속: 끝까지 보면 무엇을 판단할 수 있는지 약속하라.
5. "오늘 종가는… 거래를 마쳤습니다", "상승한 수치입니다" 같은 뉴스 문장 금지.
6. "수치입니다/기록했습니다/나타났습니다/해당합니다/시사합니다/분석됩니다/판단됩니다/전망됩니다/확인됩니다" 같은 리포트체 금지. 이런 표현이 필요해 보이면 짧은 말로 다시 풀어라.
7. 문장은 짧게 나눈다. 설명은 편하게, 판단은 한 문장으로 분리한다.
8. 숫자가 강하면 문장은 차분하게 쓴다. 재난·전쟁·혈투 비유를 쓰지 마라.
9. CTA는 본문 마지막 핵심 데이터와 자연스럽게 이어라. 단, CTA 본문 문구 자체는 바꾸지 말고 직전 연결 문단만 자연스럽게 써라.
10. 어제·전일 이야기는 좋은 배경이지만 길게 복기하지 마라. 실적, 급락, 서킷브레이커, 외국인 매도 같은 전일 요소는 오늘 흐름을 설명하는 데 필요한 만큼만 짧게 쓴다.
11. 오늘 장중/장마감 대본에서는 전일 배경보다 오늘의 현재 가격, 거래량, 마감 위치, 확정 수급 변화가 주인공이어야 한다.
12. 장중 대본에서 [시장 특이상황]을 말할 때는 "오늘 현재 새로 발동됐다"가 아니라 "전일 또는 최근 시장 충격이 남아 있다" 정도로 낮춰 말하라. 단, 수동 메모에 오늘 발동이 명시되어 있으면 그때만 오늘 발생으로 말한다.
13. "방금 전", "방금", "조금 전"은 현재 장중 가격·거래량 움직임에만 쓴다. 실적 발표, DART 공시, 뉴스, 전일 수급, 시장 특이상황에는 쓰지 마라.
14. 날짜가 수집 시각보다 과거인 자료는 "어제", "전일", "직전 거래일", "최근 확정 데이터"로 말한다. 과거 자료를 현재 발생처럼 바꾸면 안 된다.
15. [수집 시각], [단계 기준 시각], 조회시각 같은 시간 정보는 대본에서 직접 읽지 마라. "현재시간 두시이십분 기준"처럼 말하지 말고, 가격은 "지금 확인되는 가격" 또는 "장중 확인 가격"으로만 자연스럽게 말하라.

## 첫 블록 후킹 규칙
- 첫 블록은 3~5문장으로 쓴다.
- 첫 블록은 숫자 목록이 아니다. 첫 문장은 최대 숫자 2개까지만 허용하고, 바로 "왜 이상한지"를 붙여라.
- 첫 블록 전체에도 숫자 표현은 최대 3개까지만 허용한다. 종가, 고가, 저가, 외국인 하루 수급, 누적 수급을 한 블록에 모두 넣으면 실패다.
- 첫 블록에서는 숫자를 다 설명하지 말고 모순 하나만 던져라. 자세한 가격·고점·저점·수급 숫자는 두 번째 블록 이후에 나눠서 말한다.
- "플러스로 끝났습니다", "플러스 영쩜일팔퍼센트입니다"처럼 등락률을 기사처럼 말하지 마라. 소폭 상승, 거의 보합권, 겉으로는 오른 날처럼 쉬운 말로 먼저 풀어라.
- "첫 체결권", "마지막 체결권"이라는 데이터 라벨을 반복하지 마라. 대본에서는 "장 초반 가격", "마감 무렵 가격"처럼 사람이 말하는 표현으로 바꿔라.
- 첫 블록은 영상 전체의 입구다. 설명을 아끼고, 숫자 충돌과 약속만 남긴다.
- 첫 문장은 "어제/오늘 어떤 일이 있었습니다" 식의 사건 설명으로 시작하지 마라. 숫자 두 개를 바로 부딪혀라.
- 가장 좋은 첫 문장 구조는 "좋은 숫자 하나 + 나쁜 돈의 방향 하나"다.
- 첫 문장은 "왜 찝찝할까요?"처럼 약한 감정 질문으로 끝내지 마라.
- 첫 문장 안에 구체 숫자 또는 구체 주체를 넣어라. 단, 주말용 큰그림 대본은 숫자 대신 뉴스 헤드라인, 실적발표, 경제지표 일정 같은 구체 이슈로 시작해도 된다.
- 첫 블록 안에서 대본 전체의 메인 질문을 박아라. 이 질문은 마지막 확인 기준에서 회수되어야 한다.
- 좋은 후킹은 시청자가 "이건 가격만 보면 안 되겠네"라고 느끼게 만든다.
- 첫 블록에서 배경 설명, 지난 며칠 복기, 긴 시장 설명을 시작하지 마라. 그건 두 번째 블록 이후에 한다.
- 첫 블록 마지막 문장은 시청 약속이어야 한다. 예: "오늘은 이 반등을 믿어도 되는지, 거래량·외국인·마감 위치 세 숫자로 확인해보겠습니다."
- 나쁜 후킹: 주가는 이틀 연속 올랐는데 왜 찝찝할까요?
- 좋은 후킹: 팔십구조원짜리 실적이 나왔는데, 외국인은 오조원 넘게 팔았습니다. 가격만 보면 저점 같아 보여도, 돈의 방향은 아직 반대로 움직이고 있어요. 오늘은 이 엇박자가 단순한 흔들림인지, 더 확인이 필요한 신호인지 세 숫자로 나눠보겠습니다.
- 더 담백한 후킹: 실적은 좋았습니다. 그런데 외국인은 팔고, 거래량은 반쪽입니다. 지금 봐야 할 건 가격이 싸졌다는 느낌이 아니라, 돈이 다시 들어오고 있는지입니다.

## 문장 호흡 규칙
- 한 문장이 2줄 이상 길어질 것 같으면 무조건 둘로 나눈다.
- "왜냐하면", "그렇기 때문에", "결국"이 한 문장 안에 몰리면 쪼갠다.
- 한 블록에 말문 1문장 + 설명 2문장 + 판단 1문장 정도가 가장 좋다.
- 단정은 줄인다. "절대 아닙니다"보다 "그렇게 보기엔 아직 부족합니다"로 말한다.
- "증명합니다", "여실히 보여줍니다"보다 "그쪽에 가깝습니다", "그렇게 볼 여지가 큽니다"를 우선한다.

## 구어체 기준
나쁜 문장: 오늘 삼성전자 종가는 삼십일만 팔천 원에 거래를 마쳤습니다.
좋은 문장: 자, 어제 삼성전자 가격만 보면 분명히 올랐어요. 그런데 이게요, 반등이 단단했는지는 따로 봐야 합니다.

나쁜 문장: 직전 사 일 평균 대비 칠십육 퍼센트 수준에 불과합니다.
좋은 문장: 거래량은 평소보다 덜 붙었어요. 그러니까 가격은 올랐는데, 돈이 강하게 따라붙은 그림은 아닌 거예요. 이러면 반등을 바로 믿긴 어렵습니다.

{format_rules}
""".strip()]

    if phase_rules:
        parts.append(phase_rules)

    if topic_rules:
        parts.append(topic_rules)

    if topic_memo:
        parts.append(f"""
## 사용자 지정 대본 주제 / 메모
- 이번 대본에서 우선 반영할 주제다.
- 단, 여기에 없는 숫자와 사실은 만들지 마라.
- 최종 대본에는 "사용자 메모" 같은 제작 과정 표현을 쓰지 마라.
- 메모에 대본 기준 날짜가 있고 그 날짜가 미래라면, 그날 주가·종가·장중 흐름·수급을 확인한 것처럼 쓰지 마라.
- 미래 날짜용 대본은 현재까지 확인된 흐름, 예정 일정, 큰 시나리오, 그날 확인할 기준을 중심으로 써라.
- 단, 최종 대본에는 "미래 날짜", "아직 오지 않은 날짜", "예약 작성", "사전에 작성" 같은 말은 쓰지 마라.

{topic_memo}
""".strip())

    if brief:
        parts.append(f"""
## 내부 데이터 우선순위 — 출력 금지
- 아래 메모는 어떤 데이터를 먼저 볼지 정하는 내부 참고다.
- 제목·비유·후킹 문구를 그대로 복사하지 마라.

{brief}
""".strip())

    parts.append(BLENDING_CTA.strip())
    parts.append("""
## 최종 확인
- 대본만 출력한다.
- ---< 구분자 위아래는 한 줄씩 비운다.
- 첫 질문이 마지막 확인 기준으로 회수되어야 한다.
- CTA 뒤에는 본문 핵심을 받아 3~5문장으로 마무리한다.

[수집 데이터]
""".strip() + "\n" + str(raw_data or "").strip())
    return "\n\n".join(parts)


def build_script_prompt(stock_name=None, stock_code=None, raw_data=None, format_name=None,
                        angle=None, custom_topic=None):
    """선택한 포맷의 최종 OpenAI 프롬프트를 만든다.

    angle은 내부 우선순위 메모로만 사용하고, custom_topic은 사용자가 직접 지정한
    대본 주제/메모로 사용한다. 비어 있으면 기존 자동 생성 흐름과 같다.
    """
    if raw_data is None:
        if not stock_name or not stock_code:
            raise ValueError("raw_data가 없으면 stock_name과 stock_code가 필요합니다.")
        if is_weekend_script_request(format_name=format_name, custom_topic=custom_topic):
            raw_data = build_weekend_raw_data(stock_name, stock_code, force=True)
        else:
            raw_data = build_raw_data(stock_name, stock_code, force=True)

    if format_name is None:
        format_name = next(iter(SCRIPT_FORMATS))

    format_name = _effective_script_format(format_name, raw_data=raw_data)
    if format_name not in SCRIPT_FORMATS:
        valid = ", ".join(SCRIPT_FORMATS.keys())
        raise ValueError(f"알 수 없는 대본 포맷입니다: {format_name}\n사용 가능: {valid}")

    prompt = _build_compact_script_prompt(
        stock_name=stock_name,
        raw_data=raw_data,
        format_name=format_name,
        angle=angle,
        custom_topic=custom_topic,
    )
    return prompt, raw_data


def _default_output_dir():
    """결과물을 저장할 기본 output 폴더를 반환한다."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def _format_separator_spacing(text):
    """---< 구분자 위아래를 한 줄씩 비운다."""
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _remove_overdrama_artifacts(text)
    text = text.replace("--- <", "---<").replace("---< ", "---<")
    text = re.sub(r"^\s*---\s*$", "---<", text, flags=re.M)
    text = re.sub(r"\n*\s*---<\s*\n*", "\n\n---<\n\n", text)
    text = _remove_mid_sentence_separators(text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip() + "\n"


def _remove_mid_sentence_separators(text):
    """문장 중간에 끼어든 ---< 만 제거한다. 새 문장은 만들지 않는다."""
    blocks = [b.strip() for b in str(text or "").split("---<")]
    if len(blocks) <= 1:
        return str(text or "")
    merged = []
    continuation_starts = (
        "없이", "하고", "하며", "하면서", "거나", "고요", "고요.", "인데", "인데요",
        "때문", "때문에", "정도", "수", "것", "게", "건", "데", "쪽", "만큼",
        "부터", "까지", "로", "으로", "은", "는", "이", "가", "을", "를",
    )
    dangling_ends = (
        "필요", "수", "것", "게", "건", "때", "뒤", "후", "전", "중", "정도",
        "그리고", "하지만", "그런데", "그러니까", "왜냐하면", "때문",
    )
    sentence_end_re = re.compile(r"(?:[.!?…]|요|죠|다|니다|습니다|입니다|거든요|잖아요|까요|네요|예요|에요)$")
    for block in blocks:
        if not block:
            continue
        if not merged:
            merged.append(block)
            continue
        prev = merged[-1].rstrip()
        cur = block.lstrip()
        prev_tail = re.sub(r"\s+", "", prev[-20:])
        cur_head = cur[:12].strip()
        should_merge = (
            any(prev_tail.endswith(x) for x in dangling_ends)
            or any(cur_head.startswith(x) for x in continuation_starts)
            or not sentence_end_re.search(prev_tail)
        )
        if should_merge:
            merged[-1] = (prev + " " + cur).strip()
        else:
            merged.append(cur)
    return "\n\n---<\n\n".join(merged)


def _restore_missing_separators(text, target_chars=250):
    """AI가 ---< 구분자를 거의 쓰지 않았을 때 기존 문장 사이에 구분자만 복구한다.

    새 문장을 만들지 않고, 긴 덩어리를 문장 경계 기준으로 나눠 읽기 좋게 만든다.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    if _separator_count(s) >= 3 or _memo_char_count(s) < 2500:
        return _format_separator_spacing(s)

    sentences = re.split(r"(?<=[.!?요죠다니다])\s+", s)
    blocks, cur = [], []
    cur_len = 0
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        cur.append(sent)
        cur_len += len(sent)
        if cur_len >= target_chars and len(cur) >= 2:
            blocks.append(" ".join(cur).strip())
            cur, cur_len = [], 0
    if cur:
        blocks.append(" ".join(cur).strip())
    if len(blocks) < 4:
        return _format_separator_spacing(s)
    return _format_separator_spacing("\n\n---<\n\n".join(blocks))


def _split_block_sentences_for_rhythm(block):
    """기존 문장 경계만 찾아 반환한다. 문장을 새로 만들거나 고치지 않는다."""
    b = str(block or "").strip()
    if not b:
        return []
    # 마침표가 없는 드문 대본도 고려하되, 일반적인 종결 어미 뒤 공백에서만 끊는다.
    pattern = re.compile(
        r".+?(?:[.!?…]+|(?:습니다|입니다|합니다|됩니다|했어요|였어요|예요|에요|거예요|거죠|거든요|잖아요|죠|요)(?=\s|$))",
        flags=re.S,
    )
    sentences = [m.group(0).strip() for m in pattern.finditer(b) if m.group(0).strip()]
    used = "".join(sentences).replace(" ", "")
    raw = b.replace(" ", "")
    if sentences and len(used) >= max(20, int(len(raw) * 0.75)):
        tail = b
        for sent in sentences:
            idx = tail.find(sent)
            if idx >= 0:
                tail = tail[idx + len(sent):]
        tail = tail.strip()
        if tail:
            sentences.append(tail)
        return sentences
    return [b]


def _rebalance_separator_rhythm(text, target_min=TARGET_SEPARATOR_COUNT, max_block_chars=360):
    """긴 분석 블록을 기존 문장 사이에서만 나눠 유튜브 컷 호흡을 만든다.

    새 문장이나 연결문은 만들지 않는다. CTA·자료안내 블록은 고정 문구 보호를 위해 건드리지 않는다.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    s = _format_separator_spacing(s)
    blocks = [b.strip() for b in s.split("---<") if b.strip()]
    if not blocks:
        return _format_separator_spacing(s)

    current_separators = max(0, len(blocks) - 1)
    balanced = []
    for block in blocks:
        if any(h in block for h in _CTA_HINTS):
            balanced.append(block)
            continue
        sentences = _split_block_sentences_for_rhythm(block)
        if len(sentences) < 4:
            balanced.append(block)
            continue

        # 이미 충분히 짧고 구분자도 충분하면 손대지 않는다.
        if len(block) <= max_block_chars and current_separators >= target_min:
            balanced.append(block)
            continue

        chunk, chunk_len = [], 0
        new_chunks = []
        for sent in sentences:
            sent_len = len(sent)
            chunk.append(sent)
            chunk_len += sent_len
            enough_for_cut = (
                (chunk_len >= 210 and len(chunk) >= 2)
                or (chunk_len >= max_block_chars)
                or (current_separators < target_min and chunk_len >= 170 and len(chunk) >= 2)
            )
            remaining_sentences = len(sentences) - sum(len(_split_block_sentences_for_rhythm(c)) for c in new_chunks) - len(chunk)
            if enough_for_cut and remaining_sentences >= 2:
                new_chunks.append(" ".join(chunk).strip())
                chunk, chunk_len = [], 0
        if chunk:
            new_chunks.append(" ".join(chunk).strip())

        if len(new_chunks) <= 1:
            balanced.append(block)
        else:
            balanced.extend(new_chunks)
            current_separators += len(new_chunks) - 1

    return _format_separator_spacing("\n\n---<\n\n".join(balanced))


def _remove_post_cta_analysis_tail(text):
    """CTA 본문 뒤에 다시 붙은 종목 분석성 문장만 삭제한다.

    CTA 문구 자체는 건드리지 않고, 모델이 CTA 끝에 덧붙인 '오늘 수급/외국인/주가'류
    확인 문장만 제거한다. 새 문장은 만들지 않는다.
    """
    s = str(text or "")
    if not s:
        return ""
    anchor_match = re.search(r"댓글창에\s*구독\s*완료\s*딱\s*네\s*글자만\s*남겨주세요|댓글창에\s*구독완료\s*딱\s*네\s*글자만\s*남겨주세요", s)
    if not anchor_match:
        return s
    cta_anchor = anchor_match.group(0)
    head = s[:anchor_match.start()]
    tail = s[anchor_match.end():]
    sentences = re.split(r"(?<=[.!?])\s+", tail)
    cleaned = []
    for sent in sentences:
        cur = sent.strip()
        if not cur:
            continue
        is_analysis_after_cta = (
            re.search(r"(오늘|내일|다음\s*장|외국인|수급|주가|거래량|환율|공매도|반등|매수세|매도세)", cur)
            and re.search(r"(장\s*마감|확인|돌아서는지|신호|기준|봐야|지켜봐야)", cur)
        )
        if is_analysis_after_cta:
            continue
        cleaned.append(cur)
    return head + cta_anchor + (" " + " ".join(cleaned).strip() if cleaned else "")


def save_text_file(text, stock_name="대본", output_dir=None, prefix="최종대본"):
    """텍스트를 UTF-8 TXT 파일로 저장하고 저장 경로를 반환한다.

    기본 저장 위치는 프로그램 폴더 안의 output 폴더다.
    파일명 맨 앞에는 날짜와 시간이 붙어 최신 결과물을 찾기 쉽게 한다.
    """
    if output_dir is None:
        output_dir = _default_output_dir()
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")
    safe_name = _safe_filename_part(stock_name)
    safe_prefix = _safe_filename_part(prefix)
    filename = f"{date_str}_{time_str}_{safe_prefix}_{safe_name}.txt"
    path = os.path.join(output_dir, filename)
    body = str(text or "").strip()
    if not body:
        raise ValueError("저장할 내용이 비어 있어 파일을 만들지 않았습니다.")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(body + "\n")
    os.replace(tmp_path, path)
    return path



# ──────────────────────────────────────────────
# AI 음성 낭독용 숫자·약어 변환
# ──────────────────────────────────────────────
_DIGIT_READING = {
    "0": "영", "1": "일", "2": "이", "3": "삼", "4": "사",
    "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구",
}
_SMALL_UNITS = ["", "십", "백", "천"]
_BIG_UNITS = ["", "만", "억", "조", "경"]

_ACRONYM_READINGS = {
    "HBM4E": "에이치비엠포이",
    "HBM3E": "에이치비엠쓰리이",
    "HBM4": "에이치비엠포",
    "HBM3": "에이치비엠쓰리",
    "HBM": "에이치비엠",
    "PER": "피이알",
    "PBR": "피비알",
    "ROE": "알오이",
    "ROA": "알오에이",
    "EPS": "이피에스",
    "BPS": "비피에스",
    "DPS": "디피에스",
    "DDR5": "디디알파이브",
    "DDR4": "디디알포",
    "DRAM": "디램",
    "D램": "디램",
    "NAND": "낸드",
    "GPU": "지피유",
    "CPU": "씨피유",
    "NPU": "엔피유",
    "AI": "에이아이",
    "ETF": "이티에프",
    "ETN": "이티엔",
    "IPO": "아이피오",
    "DART": "다트",
    "KRX": "케이알엑스",
    "KOSPI": "코스피",
    "KOSDAQ": "코스닥",
    "NASDAQ": "나스닥",
    "NVIDIA": "엔비디아",
    "NVDA": "엔비디아",
    "TSMC": "티에스엠씨",
    "HTS": "에이치티에스",
    "MTS": "엠티에스",
    "Home appliance Solution": "생활가전 솔루션",
    "Home Appliance Solution": "생활가전 솔루션",
    "Eco Solution": "공조 솔루션",
    "Vehicle component Solutions": "전장 부품 솔루션",
    "Media Entertainment Solution": "미디어 엔터테인먼트 솔루션",
    "HVAC": "공조",
    "B2B": "기업 간 거래",
    "B2C": "소비자 대상 거래",
    "HS": "생활가전",
    "ES": "공조",
    "VS": "전장",
    "MS": "미디어 엔터테인먼트",
    "Steel": "철강재",
    "Resin": "수지",
    "Copper": "구리",
    "Home appliance": "생활가전",
    "TV": "티비",
}

_UNIT_READINGS = {
    "%p": "퍼센트포인트",
    "%포인트": "퍼센트포인트",
    "%": "퍼센트",
    "퍼센트": "퍼센트",
    "조원": "조원",
    "억원": "억원",
    "만원": "만원",
    "원": "원",
    "달러": "달러",
    "주": "주",
    "만주": "만주",
    "억주": "억주",
    "조주": "조주",
    "배": "배",
    "건": "건",
    "개": "개",
    "명": "명",
    "일": "일",
    "월": "월",
    "년": "년",
    "거래일": "거래일",
    "영업일": "영업일",
    "Gbps": "지비피에스",
    "gbps": "지비피에스",
    "TB/s": "테라바이트퍼세컨드",
    "GB/s": "기가바이트퍼세컨드",
}


def _int_to_korean(num):
    """정수를 한국어 독음으로 변환한다. 예: 5230 -> 오천이백삼십."""
    try:
        n = int(str(num).replace(",", ""))
    except (TypeError, ValueError):
        return str(num)
    if n == 0:
        return "영"
    if n < 0:
        return "마이너스 " + _int_to_korean(abs(n))

    groups = []
    while n:
        groups.append(n % 10000)
        n //= 10000

    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        group = groups[gi]
        if group == 0:
            continue
        group_text = []
        digits = list(map(int, f"{group:04d}"))
        for idx, digit in enumerate(digits):
            if digit == 0:
                continue
            unit_idx = 3 - idx
            # 십/백/천 앞의 1은 자연스럽게 생략: 일천오백이 아니라 천오백
            if digit == 1 and unit_idx > 0:
                group_text.append(_SMALL_UNITS[unit_idx])
            else:
                group_text.append(_DIGIT_READING[str(digit)] + _SMALL_UNITS[unit_idx])
        parts.append("".join(group_text) + _BIG_UNITS[gi])
    return "".join(parts)


def _number_to_korean_reading(value):
    """정수·소수를 AI 낭독용 독음으로 변환한다."""
    raw = str(value).strip().replace(",", "")
    if not raw:
        return raw
    sign = ""
    if raw.startswith("-"):
        sign = "마이너스 "
        raw = raw[1:]
    elif raw.startswith("+"):
        sign = "플러스 "
        raw = raw[1:]
    if "." in raw:
        left, right = raw.split(".", 1)
        right = right.rstrip("0")
        if not right:
            return sign + _int_to_korean(left or "0")
        return sign + _int_to_korean(left or "0") + "쩜" + "".join(_DIGIT_READING.get(ch, ch) for ch in right)
    return sign + _int_to_korean(raw)


_NATIVE_HOUR_READINGS = {
    0: "영시",
    1: "한시",
    2: "두시",
    3: "세시",
    4: "네시",
    5: "다섯시",
    6: "여섯시",
    7: "일곱시",
    8: "여덟시",
    9: "아홉시",
    10: "열시",
    11: "열한시",
    12: "열두시",
}


def _hour_to_korean_reading(hour):
    try:
        h = int(str(hour).replace(",", ""))
    except (TypeError, ValueError):
        return str(hour) + "시"
    if h in _NATIVE_HOUR_READINGS:
        return _NATIVE_HOUR_READINGS[h]
    if h > 12:
        ampm_h = h % 12
        if ampm_h == 0:
            ampm_h = 12
        return _NATIVE_HOUR_READINGS.get(ampm_h, _int_to_korean(ampm_h) + "시")
    return _int_to_korean(h) + "시"


def _minute_to_korean_reading(minute):
    try:
        m = int(str(minute).replace(",", ""))
    except (TypeError, ValueError):
        return str(minute) + "분"
    if m == 0:
        return "정각"
    return _int_to_korean(m) + "분"


def _apply_time_readings(text):
    """오후 1시 51분, 13:51 같은 시간 표현을 AI 낭독용으로 바꾼다."""
    if not text:
        return ""

    def repl_ampm_hm(m):
        ampm = m.group("ampm") or ""
        hour = _hour_to_korean_reading(m.group("hour"))
        minute = _minute_to_korean_reading(m.group("minute"))
        return f"{ampm} {hour} {minute}".strip()

    text = re.sub(
        r"(?P<ampm>오전|오후|새벽|아침|낮|밤|정오)\s*(?P<hour>\d{1,2})\s*시\s*(?P<minute>\d{1,2})\s*분",
        repl_ampm_hm,
        text,
    )

    def repl_ampm_h(m):
        ampm = m.group("ampm") or ""
        hour = _hour_to_korean_reading(m.group("hour"))
        return f"{ampm} {hour}".strip()

    text = re.sub(
        r"(?P<ampm>오전|오후|새벽|아침|낮|밤|정오)\s*(?P<hour>\d{1,2})\s*시",
        repl_ampm_h,
        text,
    )

    def repl_colon_time(m):
        prefix = m.group("prefix") or ""
        hour_raw = m.group("hour")
        minute = _minute_to_korean_reading(m.group("minute"))
        hour = _hour_to_korean_reading(hour_raw)
        return f"{prefix} {hour} {minute}".strip()

    text = re.sub(
        r"(?<!\d)(?P<prefix>오전|오후|새벽|아침|낮|밤)?\s*(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})(?!\d)",
        repl_colon_time,
        text,
    )

    def repl_plain_hm(m):
        return _hour_to_korean_reading(m.group("hour")) + " " + _minute_to_korean_reading(m.group("minute"))

    text = re.sub(
        r"(?<!\d)(?P<hour>\d{1,2})\s*시\s*(?P<minute>\d{1,2})\s*분",
        repl_plain_hm,
        text,
    )

    def repl_plain_h(m):
        return _hour_to_korean_reading(m.group("hour"))

    text = re.sub(
        r"(?<!\d)(?P<hour>\d{1,2})\s*시(?![가-힣]*총액)",
        repl_plain_h,
        text,
    )
    return text


def _apply_acronym_readings(text):
    """대본 안의 영문 약어를 AI가 읽기 쉬운 한글 독음으로 바꾼다."""
    for key in sorted(_ACRONYM_READINGS, key=len, reverse=True):
        val = _ACRONYM_READINGS[key]
        if re.search(r"[A-Za-z]", key):
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", val, text, flags=re.I)
        else:
            text = text.replace(key, val)
    return text


def _apply_number_unit_readings(text):
    """숫자+단위 표현을 낭독용 한글 독음으로 바꾼다.

    예: 4.6% -> 사쩜육퍼센트, 5230조원 -> 오천이백삼십조원,
        3500만주 -> 삼천오백만주, 1,550원대 -> 천오백오십원대.
    """
    # 1조8천억 원 / 89조4천억 원 / 28만6천 원처럼
    # 숫자+한국식 단위가 여러 덩어리로 붙는 경우를 먼저 처리한다.
    # 이 처리를 먼저 하지 않으면 앞 덩어리만 바뀌어 "일조8천억" 같은 혼종이 생긴다.
    def repl_chained_korean_units(m):
        body = m.group("body")
        tail = m.group("tail") or ""
        suffix = m.group("suffix") or ""
        parts = []
        for sm in re.finditer(r"(?P<num>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<units>[십백천만억조경]+)", body):
            parts.append(_number_to_korean_reading(sm.group("num")) + sm.group("units"))
        if not parts:
            return m.group(0)
        result = " ".join(parts)
        if tail:
            result += " " + tail
        if suffix:
            result += suffix
        return result

    text = re.sub(
        r"(?<![A-Za-z0-9])(?P<body>[+-]?\d[\d,]*(?:\.\d+)?\s*[십백천만억조경]+(?:\s*\d[\d,]*(?:\.\d+)?\s*[십백천만억조경]+)+)\s*(?P<tail>원|주)?(?P<suffix>어치|대)?",
        repl_chained_korean_units,
        text,
    )

    # 4천억 원 / 6천 원처럼 단일 숫자+복합 한국식 단위도 처리한다.
    def repl_single_korean_units(m):
        num = m.group("num")
        units = m.group("units")
        tail = m.group("tail") or ""
        suffix = m.group("suffix") or ""
        result = _number_to_korean_reading(num) + units
        if tail:
            result += " " + tail
        if suffix:
            result += suffix
        return result

    text = re.sub(
        r"(?<![A-Za-z0-9])(?P<num>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<units>[십백천만억조경]{2,})(?P<tail>원|주)?(?P<suffix>어치|대)?",
        repl_single_korean_units,
        text,
    )

    # AI가 이미 앞부분을 한글로 써서 "팔십구조4천억", "이십팔만6천"처럼
    # 한글 단위 뒤에 숫자가 붙은 경우도 정리한다.
    korean_number_chars = "영일이삼사오육칠팔구십백천만억조경점쩜마이너스플러스"

    def repl_mixed_korean_digit_units(m):
        prefix = m.group("prefix")
        num = m.group("num")
        units = m.group("units")
        tail = m.group("tail") or ""
        suffix = m.group("suffix") or ""
        result = prefix + " " + _number_to_korean_reading(num) + units
        if tail:
            result += " " + tail
        if suffix:
            result += suffix
        return result

    text = re.sub(
        rf"(?P<prefix>[{korean_number_chars}]+[만억조경])(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<units>[십백천만억조경]+)(?P<tail>원|주)?(?P<suffix>어치|대)?",
        repl_mixed_korean_digit_units,
        text,
    )

    # 89.4조, 1.57조원처럼 소수점+조 단위는 그대로 읽으면
    # "팔십구쩜사조"가 되어 방송 말맛이 깨진다. 같은 값 안에서만
    # 조/억으로 풀어 읽는다. 예: 89.4조 -> 팔십구조 사천억.
    def repl_decimal_jo(m):
        raw = (m.group("num") or "").replace(",", "")
        tail = m.group("tail") or ""
        suffix = m.group("suffix") or ""
        try:
            left, right = raw.split(".", 1)
            jo = int(left or "0")
            frac = float("0." + right)
            eok = int(round(frac * 10000))
        except Exception:
            return m.group(0)
        if eok >= 10000:
            jo += eok // 10000
            eok = eok % 10000
        parts = []
        if jo:
            parts.append(_number_to_korean_reading(str(jo)) + "조")
        if eok:
            parts.append(_number_to_korean_reading(str(eok)) + "억")
        if not parts:
            parts.append("영조")
        result = " ".join(parts)
        if tail:
            result += " " + tail
        if suffix:
            result += suffix
        return result

    text = re.sub(
        r"(?<![A-Za-z0-9])(?P<num>[+-]?\d[\d,]*\.\d+)\s*조\s*(?P<tail>원)?(?P<suffix>어치|대)?",
        repl_decimal_jo,
        text,
    )

    # "일조팔천억", "팔십구조사천억", "이십팔만육천"처럼
    # 모두 한글로 바뀐 뒤에도 덩어리가 붙어 있으면 읽기 좋게 띄운다.
    text = re.sub(
        r"([만억조경])(?=(?:영|일|이|삼|사|오|육|칠|팔|구)?(?:십|백|천))",
        r"\1 ",
        text,
    )

    # 복합 단위는 먼저 처리한다.
    ordered_units = sorted(_UNIT_READINGS, key=len, reverse=True)
    unit_alt = "|".join(re.escape(u) for u in ordered_units)

    # 9,300억 원 / 5230조원 / 3500만주 같은 한국식 큰 단위 결합
    def repl_korean_money_or_qty(m):
        num = m.group("num")
        mid = m.group("mid") or ""
        tail = m.group("tail") or ""
        suffix = m.group("suffix") or ""
        return _number_to_korean_reading(num) + mid + tail + suffix

    text = re.sub(
        r"(?<![A-Za-z0-9])(?P<num>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<mid>조|억|만)\s*(?P<tail>원|주)?(?P<suffix>대)?",
        repl_korean_money_or_qty,
        text,
    )

    # 0.5%p, 4.6%, 16Gbps, 3.6TB/s, 4.6배 같은 일반 숫자+단위
    def repl_number_unit(m):
        num = m.group("num")
        unit = m.group("unit")
        suffix = m.group("suffix") or ""
        return _number_to_korean_reading(num) + _UNIT_READINGS.get(unit, unit) + suffix

    text = re.sub(
        rf"(?<![A-Za-z0-9])(?P<num>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>{unit_alt})(?P<suffix>대)?",
        repl_number_unit,
        text,
    )

    # AI가 직접 만든 "29쩜삼퍼센트" 같은 혼종 표기를 한글 낭독체로 정리한다.
    def repl_mixed_jjeom_decimal(m):
        left = _number_to_korean_reading((m.group("left") or "").replace(",", ""))
        right = "".join(_DIGIT_READING.get(ch, ch) for ch in (m.group("right") or ""))
        unit = m.group("unit") or ""
        return left + "쩜" + right + unit

    text = re.sub(
        r"(?<![A-Za-z0-9])(?P<left>\d[\d,]*)쩜(?P<right>[0-9영일이삼사오육칠팔구]+)(?P<unit>퍼센트포인트|퍼센트|배)?",
        repl_mixed_jjeom_decimal,
        text,
    )

    # 47.25%에서 처리 후 남은 퍼센트포인트 표기 정리
    text = text.replace("퍼센트p", "퍼센트포인트")
    return text


def make_ai_voice_readable(text):
    """최종 대본을 AI 음성이 읽기 쉬운 형태로 바꾼다."""
    if not text:
        return ""
    text = _apply_acronym_readings(str(text))
    text = _apply_time_readings(text)
    text = _apply_number_unit_readings(text)
    return text



_TONE_SOFTEN_REPLACEMENTS = []
_REPORT_STYLE_REPLACEMENTS = []


def _soften_overheated_tone(text):
    """이전 버전 호환용. 새 문장 생성·톤 창작을 하지 않고 원문만 반환한다."""
    return "" if text is None else str(text)

def _reinforce_spoken_korean(text):
    """이전 버전 호환용. 구어체 문장을 코드가 새로 만들지 않는다."""
    return "" if text is None else str(text)

def _dedupe_repeated_script_blocks(text):
    """반복되는 블록·문장을 줄인다. 글자 수를 과하게 훼손하지 않도록 느슨하게 처리한다."""
    if not text:
        return ""
    blocks = [b.strip() for b in str(text).split("---<")]
    new_blocks = []
    seen_norm = set()
    for b in blocks:
        if not b:
            continue
        norm = re.sub(r"\s+", "", b)
        norm = re.sub(r"[.,!?…·'\"“”‘’]", "", norm)
        # 거의 같은 긴 블록이 반복되면 제거
        key = norm[:220]
        if len(norm) > 180 and key in seen_norm:
            continue
        seen_norm.add(key)
        new_blocks.append(b)
    s = "\n\n---<\n\n".join(new_blocks)

    # 같은 문장이 가까운 거리에서 반복될 때 한 번만 남긴다.
    sentences = re.split(r"(?<=[.!?요죠다니다])\s+", s)
    out = []
    recent = []
    for sent in sentences:
        raw = sent.strip()
        if not raw:
            continue
        norm = re.sub(r"\s+", "", raw)
        norm = re.sub(r"[.,!?…·'\"“”‘’]", "", norm)
        if len(norm) > 35 and norm in recent:
            continue
        out.append(raw)
        recent.append(norm)
        if len(recent) > 18:
            recent.pop(0)
    # 문장 분리 과정에서 구분자 주변이 흐트러질 수 있어 다시 정리
    return _format_separator_spacing(" ".join(out))


def _reinforce_opening_retention(text):
    """새 후킹 문장을 강제로 붙이지 않는다. AI 원문을 유지한다."""
    return "" if text is None else str(text)



def _reinforce_flow_bridges(text):
    """흐름 보강용 강제 연결문 삽입은 중단한다.

    이전 버전에서는 연결문을 코드가 직접 삽입하면서 AI 문장과 충돌했다.
    이제 이 함수는 새 문장을 넣지 않고 원문을 그대로 반환한다.
    반복 제거는 _collapse_repeated_bridge_phrases에서만 수행한다.
    """
    return "" if text is None else str(text)



_SCRIPT_META_REPLACEMENTS = [
    (r"오늘\s*이면\s*추적은[^.!?\n]{0,120}[.!?]?", ""),
    (r"오늘\s*이면추적은[^.!?\n]{0,120}[.!?]?", ""),
    (r"이면\s*추적을?\s*해\s*보겠습니다[.!?]?", ""),
    (r"이면추적을?\s*해\s*보겠습니다[.!?]?", ""),
    (r"추적해\s*보겠습니다[.!?]?", ""),
    (r"교차\s*검증(?:을)?\s*해(?:보)?(?:야)?(?:겠죠|합니다|보겠습니다)[.!?]?", ""),
    (r"근본적인\s*질문에서부터\s*시작하겠습니다[.!?]?", ""),
    (r"오늘은\s*(?:이|그)\s*질문에서(?:부터)?\s*시작하겠습니다[.!?]?", ""),
    (r"진짜\s*안쪽\s*흐름의\s*시간(?:입니다|이에요)[.!?]?", ""),
    (r"을\s*하나씩\s*파고들어[^.!?\n]{0,80}[.!?]?", ""),
]

def _remove_script_meta_phrases(text):
    """작가용/포맷명/반복 연결문이 실제 대본에 새는 것을 삭제한다. 새 문장은 만들지 않는다."""
    if not text:
        return ""
    s = str(text)
    for pat, repl in _SCRIPT_META_REPLACEMENTS:
        s = re.sub(pat, repl, s, flags=re.I)
    # 포맷명 자체가 문장 속에 새면 삭제한다.
    s = re.sub(r"이면\s*추적", "", s)
    s = re.sub(r"이면추적", "", s)
    # 각도/흐름 후보가 대본에 새어 나온 경우 삭제한다.
    s = re.sub(r"오늘의\s*지정\s*각도", "", s)
    s = re.sub(r"내부\s*참고\s*메모", "", s)
    s = re.sub(r"흐름\s*후보", "", s)
    s = re.sub(r"주인공\s*데이터", "", s)
    s = re.sub(r"중심\s*데이터", "", s)
    s = re.sub(r"차별점", "", s)
    s = re.sub(r"[^.!?\n]*(?:주말\s*대본|당일\s*등락률|이\s*포맷|자료\s*성격|수집\s*데이터)[^.!?\n]*[.!?]?", "", s)
    s = re.sub(r"[^.!?\n]*에이치티에스에서\s*마지막으로\s*맞춰보셔야[^.!?\n]*[.!?]?", "", s)
    # 어색한 공백만 정리한다. 새 표현을 만들지 않는다.
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _collapse_repeated_bridge_phrases(text):
    """프롬프트 찌꺼기처럼 보이는 고정 후렴구를 삭제한다. 새 문장은 넣지 않는다."""
    if not text:
        return ""
    s = str(text)
    forced_phrases = [
        "숫자는 그렇게 말한다.",
        "이게 지금 봐야 할 지점이다.",
        "이 지점은 그냥 넘기면 안 된다.",
        "바로 옆에 붙여봐야 할 데이터가 하나 더 있어요.",
        "옆에 붙여볼 데이터가 하나 더 있습니다.",
        "그런데 이 숫자만으로는 부족합니다.",
        "그런데 이 숫자만으로는 부족해요.",
        "이 흐름을 보셨다면, 다음으로 확인할 건 돈의 실제 방향입니다.",
        "이 흐름을 보셨다면, 다음으로 확인할 건 돈의 실제 방향이에요.",
        "이 흐름을 확인하셨다면, 다음으로 넘어가서 돈의 실제 방향, 그 힘의 균형을 봐야 합니다.",
        "여기서 한 단계 더 들어가야 합니다.",
        "여기서 한 단계 더 들어가야 해요.",
        "여기서 한 단계 더 들어가야 합니다. 가격이 아니라 주체를 봐야 하거든요.",
        "여기서 한 단계 더 들어가야 해요. 가격이 아니라 주체를 봐야 하거든요.",
        "여기서 한 단계 더 들어가야 합니다. 가격이 아니라 돈의 주체, 그 성격을 봐야 하거든요.",
        "여기서 한 단계 더 들어가야 해요. 가격이 아니라 돈의 주체, 그 성격을 봐야 하거든요.",
        "그럼 여기서 다음 질문이 자연스럽게 생깁니다.",
        "그렇다면 이 힘없는 반등의 커튼 뒤에서, 진짜 돈은 실제로 어떻게 움직였을까요.",
        "돈의 흐름이 그렇게 말합니다.",
        "이 을 맞출 때 비로소 시장의 진짜 의도가 보이기 시작합니다.",
        "쪼개어 짚어보겠습니다.",
        "쪼개서 짚어보겠습니다.",
        "하나씩 뜯어볼게요.",
        "같이 한 번 볼게요.",
        "같이 한번 볼게요.",
        "살펴보겠습니다.",
        "확인해 보겠습니다.",
    ]
    for phrase in forced_phrases:
        s = s.replace(phrase, "")
    s = re.sub(r"숫자는\s*그렇게\s*말한다[.!?]?", "", s)
    s = re.sub(r"이게\s*지금\s*봐야\s*할\s*지점이다[.!?]?", "", s)
    s = re.sub(r"바로\s*옆에\s*붙여봐야[^.!?\n]{0,100}[.!?]?", "", s)
    s = re.sub(r"여기서\s*한\s*단계\s*더\s*들어가야[^.!?\n]{0,120}[.!?]?", "", s)
    # 같은 짧은 문장이 바로 반복되면 하나만 남긴다.
    s = re.sub(r"([^.!?\n]{8,90}[.!?])(?:\s*\1){1,}", r"\1", s)
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _force_spoken_rhythm(text):
    """이전 버전 호환용. 말맛 보정은 프롬프트에 맡기고 코드는 원문만 반환한다."""
    return "" if text is None else str(text)

def _force_broadcast_mixed_tone(text):
    """이전 버전 호환용. 방송 리듬을 코드가 강제로 쓰지 않는다."""
    return "" if text is None else str(text)

def _remove_invalid_opening_continuation(text):
    """첫 줄이 앞 문장을 전제로 시작하면 연결 부사만 삭제한다."""
    if not text:
        return ""
    s = str(text).lstrip()
    return re.sub(r"^(그런데|그렇다면|하지만|여기서|그러면|그럼)\s*,?\s*", "", s, count=1)

def _remove_adjacent_near_duplicate_sentences(text):
    """서로 거의 같은 문장이 연속으로 붙은 경우 하나만 남긴다. 새 문장은 만들지 않는다."""
    if not text:
        return ""
    import difflib
    s = str(text)
    blocks = [b.strip() for b in s.split('---<')]
    fixed_blocks = []
    for block in blocks:
        if not block:
            continue
        parts = re.split(r'(?<=[.!?])\s+', block)
        kept = []
        for sent in parts:
            cur = sent.strip()
            if not cur:
                continue
            if kept:
                a = re.sub(r"\s+", "", kept[-1])
                b = re.sub(r"\s+", "", cur)
                # 너무 짧은 감탄/전환문은 비교하지 않음
                if min(len(a), len(b)) >= 25:
                    ratio = difflib.SequenceMatcher(None, a, b).ratio()
                    if ratio >= 0.72:
                        # 뒤 문장이 더 구체적이면 뒤 문장으로 교체, 아니면 앞 문장 유지
                        if len(b) > len(a):
                            kept[-1] = cur
                        continue
            kept.append(cur)
        fixed_blocks.append(" ".join(kept))
    return "\n\n---<\n\n".join(fixed_blocks)



def _remove_overdrama_artifacts(text):
    """과장 비유를 삭제한다. 대체 문장이나 연결 표현은 만들지 않는다."""
    if not text:
        return ""
    s = str(text)
    sentence_drop_phrases = [
        "폭포수처럼", "폭포수 같은", "총성 없는 전쟁", "창과 방패",
        "혈투", "피를 말리는", "피가 마르는", "매도 폭탄",
        "온몸으로 방어", "온몸으로 막아낸", "온몸으로 막아내고", "거대한 파도",
        "외나무다리 승부", "격전 상황", "집어던졌습니다", "집어던졌죠",
        "처참했습니다", "처참했죠", "거대한 매도 세력", "정면으로 충돌",
        "정면 충돌", "필사적으로 방어", "필사적으로 막아", "치열한 힘겨루기",
        "치열한 공방", "끔찍했던", "끔찍한", "패닉", "산산조각",
        "물량 폭탄", "온몸으로 받아내며", "온몸으로 받아내", "거대한 물량",
        "뒤도 안 돌아보고", "썰물처럼", "빈집에 소 들어간다는",
        "빈집에 소 들어간다", "여실히 증명해주고", "여실히 증명",
        "여실히 보여주는", "여실히 보여주", "역대급으로 물량을 던지고",
        "패대기치고", "패대기", "기형적인 엇박자", "기형적 상황",
        "우리를 유혹하는 함정", "유혹하는 함정", "큰손들은 오히려 짐을 싸고",
        "큰손들이 짐을 싸고", "짐을 싸고", "눈앞이 캄캄해지는",
        "짐을 싸서", "짐을 싸는", "짐 싸서", "짐 싸는", "무서운 속도로", "쌍발 엔진",
        "비바람이 치는데", "우리 집 지붕", "너무 순진한 생각", "글로벌 핑계거리",
        "굉장히 불안하게", "찝찝한 흔적",
        "온몸을 던져", "큰 파도", "험난한 과정", "와르르 무너지는", "와르르 무너졌",
        "돈이 말라붙은", "돈이 마르고", "찐바닥", "거대한 충격", "얼어붙은 심리",
        "거칠고 무거운", "엄청난 물량", "든든한 뒷배", "뒷배", "맷집",
        "거대한 돈", "무겁게 짓누르는", "급하게 팔고 나가는",
        "가슴이 철렁", "멱살 잡고", "샴페인을 터뜨리", "잔인하게 보여주는",
        "입이 떡 벌어", "구명조끼를 입고", "외줄 타기", "모래 위에 지은 성",
        "모래 위의 성", "무거운 중력", "매몰차게 내던지고", "내다 버렸습니다",
        "내다 버렸어요", "돈을 바리바리", "영끌해서 물타기",
        "고스란히 얻어맞", "얻어맞은", "얻어맞고", "거대한 엇박자",
        "초유의 사태", "시장 발작", "발작을 일으키", "치열한 싸움",
        "받아먹고", "험악한 시장 분위기",
        "숲을 먼저 봐야 나무", "나무가 제대로 보이는 법",
    ]
    for phrase in sorted(sentence_drop_phrases, key=len, reverse=True):
        pat = r"[^.!?\n。]*" + re.escape(phrase) + r"[^.!?\n。]*(?:[.!?。]+|\n|$)"
        s = re.sub(pat, "", s)
    banned_phrases = [
        "그야말로 ", "말 그대로 ", "상상하기조차 힘든 ", "숨 막히는 ",
        "처절한 ", "위험천만한 ", "역사적인 ", "폭포수처럼", "폭포수 같은",
        "총성 없는 전쟁", "창과 개인이라는 거대한 방패", "창과 방패",
        "거대한 방패", "거대한 창", "혈투", "피를 말리는", "피가 마르는",
        "피가 마를", "매도 폭탄", "온몸으로 방어", "온몸으로 막아낸", "온몸으로 막아내고",
        "거대한 파도", "외나무다리 승부", "격전 상황", "격전", "맹렬하게 ",
        "공포에 질려 ", "남김없이 전부 ", "집어던졌습니다", "집어던졌죠",
        "집어던지는", "집어던진", "처참했습니다", "처참했죠", "처참한 ",
        "무자비하고 ", "무자비한 ", "거대한 매도 세력", "정면으로 충돌",
        "정면 충돌", "필사적으로 방어", "필사적으로 막아", "치열한 힘겨루기",
        "치열한 공방", "어마어마한 ", "주범",
        "끔찍했던 ", "끔찍한 ", "패닉", "산산조각", "눈물겨운 ", "눈물겹고 ",
        "물량 폭탄", "온몸으로 받아내며 ", "온몸으로 받아내", "무자비하게 ",
        "무자비하게", "가차 없이 ", "가차 없이", "거대한 물량", "뒤도 안 돌아보고 ",
        "뒤도 안 돌아보고", "썰물처럼 ", "썰물처럼", "냉혹한 ", "지독한 ",
        "뼈아프게 ", "뼈아픈 ", "아주 허무하고 ", "여실히 증명해주고",
        "여실히 증명", "여실히 보여주는", "여실히 보여주", "빈집에 소 들어간다는",
        "빈집에 소 들어간다", "역대급으로 ", "패대기치고", "패대기",
        "기형적인 ", "기형적 ",
        "계좌에는 빨간불이 들어왔는데, ", "계좌에 모처럼 빨간불이 들어왔는데, ",
        "빨간불이 들어왔는데, ", "큰손들은 오히려 짐을 싸고 ", "큰손들이 짐을 싸고 ",
        "짐을 싸고 ", "짐을 싸서 ", "짐을 싸는 ", "짐 싸서 ", "짐 싸는 ",
        "우리를 유혹하는 함정", "유혹하는 함정", "피를 말리면서 ",
        "눈앞이 캄캄해지는 ", "가슴이 철렁 내려앉으셨을 ", "가슴이 철렁 ",
        "멱살 잡고 끌어올리면서 ", "멱살 잡고 ", "샴페인을 터뜨리시면 ",
        "샴페인을 터뜨리", "텅 빈 ", "초라한 ", "엄청나게 ", "잔인하게 ",
        "입이 떡 벌어집니다", "입이 떡 벌어지는 ", "완전히 꺼진 것도 모자라, ",
        "아예 뒤로 역주행을 하고 ", "역주행을 하고 ", "쪼그라들고 ",
        "구명조끼를 입고 바다로 뛰어내리고 있는 ", "구명조끼를 입고 ",
        "안타깝게도 ", "모래알처럼 ", "무거운 중력을 ", "외줄 타기를 하고 있는 ",
        "외줄 타기", "실망스럽기는 마찬가지입니다", "눈을 씻고 찾아봐도 ",
        "매몰차게 ", "내던지고 ", "내다 버렸습니다", "내다 버렸어요",
        "돈을 바리바리 싸 들고 ", "바리바리 싸 들고 ", "영끌해서 ",
        "온몸을 던져 ", "온몸을 던져", "큰 파도", "험난한 과정", "와르르 ",
        "돈이 말라붙은 ", "돈이 마르고 ", "찐바닥",
        "거대한 충격", "얼어붙은 심리", "거칠고 무거운 ", "거칠게 ",
        "엄청난 물량", "든든한 뒷배", "뒷배", "맷집", "거대한 돈",
        "무겁게 짓누르는 ", "급하게 팔고 나가는 ",
        "고스란히 얻어맞은 ", "고스란히 얻어맞고 ", "얻어맞은 ", "얻어맞고 ",
        "거대한 엇박자",
        "초유의 사태", "시장 발작", "발작을 일으키면서 ", "발작을 일으키고 ",
        "치열한 싸움", "받아먹고", "험악한 시장 분위기",
        "숲을 먼저 봐야 나무가 제대로 보이는 법입니다",
        "숲을 먼저 봐야 나무가 제대로 보이는 법",
        "귀추가 주목됩니다", "귀추가 주목되고 있습니다", "귀추가 주목되는 ",
        "향방이 주목됩니다", "향방이 주목되고 있습니다", "방향이 주목됩니다",
        "초미의 관심사입니다", "초미의 관심사", "이목이 쏠립니다", "이목이 쏠리고 있습니다",
        "촉각을 곤두세우고 있습니다", "촉각을 곤두세우고", "분수령입니다",
    ]
    for phrase in sorted(banned_phrases, key=len, reverse=True):
        s = s.replace(phrase, "")
    cliche_replacements = [
        ("독야청청 오르기", "혼자 오르기"),
        ("독야청청 상승하기", "혼자 상승하기"),
        ("독야청청", "혼자"),
        ("향방을 가를", "방향을 가를"),
        ("향방이 갈릴", "방향이 갈릴"),
        ("향방", "방향"),
        ("관건입니다", "중요합니다"),
        ("관건이에요", "중요해요"),
        ("분수령", "중요한 갈림길"),
        ("거대한 자금 흐름", "자금 흐름"),
        ("거대한 돈", "큰돈"),
        ("거대한", "큰"),
        ("엄청난", "큰"),
    ]
    for old, new in cliche_replacements:
        s = s.replace(old, new)
    s = re.sub(r"(?<![가-힣])(폭포수|방패|창|혈투)(?![가-힣])", "", s)
    s = re.sub(r"\.\.+", ".", s)
    s = re.sub(r"(규모|수준|흐름|상태|상황|신호)의\s+이었", r"\1였", s)
    s = re.sub(r"(규모|수준|흐름|상태|상황|신호)의\s+(였습니다|입니다)", r"\1\2", s)
    s = re.sub(r"\s+([,.!?])", r"\1", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s


def _remove_malformed_cleanup_fragments(text):
    """삭제 후 남은 깨진 조사·서술어 문장을 제거한다. 새 문장은 만들지 않는다."""
    if not text:
        return ""
    broken_patterns = [
        r"\b[가-힣]+의\s+(?:을|를|은|는|이|가)\b",
        r"\b(?:의|을|를|은|는|이|가|으로|로)\s+(?:돌변|받아냈|던졌|쏟아졌)",
        r"\b(?:을|를|은|는|이|가|으로|로)\s*(?:입니다|겁니다|거죠|거예요|죠|요)[.!?]?",
        r"\b(?:외국인의|개인의|기관의|금융투자의)\s+(?:을|를|은|는|이|가)\b",
    ]
    sentence_pat = r"[^.!?\n。]*{pat}[^.!?\n。]*(?:[.!?。]+|\n|$)"
    s = str(text)
    for pat in broken_patterns:
        s = re.sub(sentence_pat.format(pat=pat), "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


def _remove_unexpected_foreign_scripts(text):
    """한글 대본에 우발적으로 섞인 비정상 문자권의 단어를 제거한다.

    영문 종목명·약어는 정상적으로 쓰이므로 보존한다. 히브리어·아랍어·키릴 문자처럼
    한국어 주식 대본에서 사용할 이유가 없는 문자만 대상으로 삼는다.
    """
    if not text:
        return ""
    fixed = []
    for block in str(text).split("---<"):
        b = block.strip()
        if not b:
            continue
        # 한글·숫자·영문 약어·일반 문장부호만 남긴다. CTA의 정상 문구는 바꾸지 않는다.
        b = re.sub(
            r"[^가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9\s"
            r".,!?%+\-–—:;'\"“”‘’()\[\]/<>=&·…~@#₩$]",
            "",
            b,
        )
        b = re.sub(r"[ \t]{2,}", " ", b)
        b = re.sub(r"\s+([,.!?])", r"\1", b)
        fixed.append(b)
    return "\n\n---<\n\n".join(fixed)


def _is_cta_block_for_cleanup(text):
    """전환용 CTA 블록은 문장 정리 대상에서 완전히 제외한다."""
    hints = (
        "구독", "좋아요", "댓글창", "설명란", "고정 댓글", "링크",
        "신청 페이지", "알림센터", "무료로 공유", "무료 에이아이", "포트폴리오 리포트",
        "보유종목 화면", "잔고 캡처", "증권 에이아이", "도넛 그래프", "판독률",
        "시크릿 책자", "세력단가", "자금 순환 맵", "보조지표", "전략노트",
    )
    return any(hint in str(text) for hint in hints)


def _collapse_consecutive_korean_phrase_repeats(text):
    """한 문장 안에서 같은 한국어 어구가 연속 출력된 오류를 한 번으로 줄인다."""
    if not text:
        return ""
    # 2~8어절짜리 어구가 바로 이어서 2회 이상 반복된 경우만 처리한다.
    # 서로 떨어진 의도적 반복이나 CTA 문구에는 영향을 주지 않는다.
    pat = re.compile(
        r"(?P<phrase>(?:[가-힣]+(?:\s+|$)){2,8})"
        r"(?:(?P=phrase)){1,}"
    )
    fixed = []
    for block in str(text).split("---<"):
        b = block.strip()
        if not b:
            continue
        if not _is_cta_block_for_cleanup(b):
            previous = None
            while b != previous:
                previous = b
                b = pat.sub(lambda m: m.group("phrase"), b)
        fixed.append(b)
    return "\n\n---<\n\n".join(fixed)


def _finish_dangling_block_endings(text):
    """구분자 앞에서 명사형으로 끊긴 문단 끝을 낭독 가능한 종결문으로 보정한다."""
    if not text:
        return ""

    terminal_re = re.compile(
        r"(?:[.!?…]|습니다|입니다|됩니다|합니다|했습니다|됐습니다|"
        r"이에요|예요|해요|돼요|거든요|잖아요|겠죠|이죠|죠|네요|"
        r"겁니다|됩니다|않습니다|있습니다|없습니다)[\"'”’)]*$"
    )
    noun_endings = (
        "때문", "사실", "현장", "차이", "신호", "의미", "뜻", "상황", "상태",
        "가능성", "흐름", "과정", "결과", "이유", "증거", "대목", "관건",
        "기준", "선택", "전략", "구조", "셈", "것", "겁", "입장", "단서",
        "핵심", "목표", "포인트", "해석", "판단", "전망", "충돌",
    )
    adjective_endings = ("합리적", "중요", "명확", "필요", "유리", "불리")

    fixed = []
    for block in str(text).split("---<"):
        b = block.strip()
        if not b:
            continue
        if _is_cta_block_for_cleanup(b):
            fixed.append(b)
            continue
        if terminal_re.search(b):
            fixed.append(b)
            continue
        if b.endswith(noun_endings) or b.endswith(adjective_endings):
            b += "입니다."
        else:
            # 이미 완결된 구어체인데 마침표만 빠진 경우에는 문장을 새로 만들지 않는다.
            b += "."
        fixed.append(b)
    return "\n\n---<\n\n".join(fixed)


def _clean_awkward_time_basis_phrases(text):
    """'이 시각 기준 현재가' 같은 어색한 시간 기준 표현만 정리한다.

    새 분석 문장이나 연결문은 만들지 않고, 어색한 접두어를 삭제하거나
    같은 의미의 자연스러운 짧은 표현으로만 바꾼다.
    """
    if not text:
        return ""
    s = str(text)
    replacements = [
        (r"현재\s*시간\s*(?:오전|오후)?\s*[영일이삼사오육칠팔구십백천두세네다섯여섯일곱여덟아홉\d\s]+시[영일이삼사오육칠팔구십백천\d\s]*분?\s*기준으로\s*현재가는", "지금 확인되는 가격은"),
        (r"현재\s*시간\s*(?:오전|오후)?\s*[영일이삼사오육칠팔구십백천두세네다섯여섯일곱여덟아홉\d\s]+시[영일이삼사오육칠팔구십백천\d\s]*분?\s*기준\s*현재가는", "지금 확인되는 가격은"),
        (r"현재\s*시간\s*(?:오전|오후)?\s*[영일이삼사오육칠팔구십백천두세네다섯여섯일곱여덟아홉\d\s]+시[영일이삼사오육칠팔구십백천\d\s]*분?\s*기준으로\s*가격은", "지금 확인되는 가격은"),
        (r"현재\s*시간\s*(?:오전|오후)?\s*[영일이삼사오육칠팔구십백천두세네다섯여섯일곱여덟아홉\d\s]+시[영일이삼사오육칠팔구십백천\d\s]*분?\s*기준으로\s*거래량은", "장중 확인 거래량은"),
        (r"현재\s*시간\s*(?:오전|오후)?\s*[영일이삼사오육칠팔구십백천두세네다섯여섯일곱여덟아홉\d\s]+시[영일이삼사오육칠팔구십백천\d\s]*분?\s*기준\s*거래량은", "장중 확인 거래량은"),
        (r"이\s*시각\s*기준\s*현재가는", "지금 확인되는 가격은"),
        (r"이\s*시각\s*기준\s*현재가가", "지금 확인되는 가격이"),
        (r"이\s*시각\s*기준\s*현재가를", "지금 확인되는 가격을"),
        (r"이\s*시각\s*기준\s*현재가", "지금 확인되는 가격"),
        (r"이\s*시각\s*기준\s*가격은", "지금 확인되는 가격은"),
        (r"이\s*시각\s*기준\s*가격이", "지금 확인되는 가격이"),
        (r"이\s*시각\s*기준\s*가격을", "지금 확인되는 가격을"),
        (r"이\s*시각\s*기준\s*가격", "지금 확인되는 가격"),
        (r"이\s*시각\s*기준\s*거래량은", "장중 확인 거래량은"),
        (r"이\s*시각\s*기준\s*거래량이", "장중 확인 거래량이"),
        (r"이\s*시각\s*기준\s*거래량을", "장중 확인 거래량을"),
        (r"이\s*시각\s*기준\s*거래량", "장중 확인 거래량"),
    ]
    for pat, repl in replacements:
        s = re.sub(pat, repl, s)
    # 단독으로 남은 "이 시각 기준으로 보면"류는 과장 없이 삭제한다.
    s = re.sub(r"이\s*시각\s*기준으로\s*보면\s*,?\s*", "", s)
    s = re.sub(r"이\s*시각\s*기준으로는\s*,?\s*", "", s)
    s = re.sub(r"첫\s*체결권", "장 초반 가격", s)
    s = re.sub(r"마지막\s*체결권", "마감 무렵 가격", s)
    s = re.sub(r"((?:[영일이삼사오육칠팔구십백천만억조경쩜]+\s*)+억)(?=\s*(?:을|를|은|는|이|가|샀|팔|순매수|순매도))", r"\1 원", s)
    return s


def _clean_common_spoken_typos(text):
    """대본 생성 중 자주 붙는 조사/띄어쓰기 깨짐만 정리한다."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"이게\s*무슨\s*소리냐(?!면)", "이게 무슨 소리냐면", s)
    s = s.replace("이게 무슨 소리냐면면", "이게 무슨 소리냐면")
    s = re.sub(r"([가-힣]+원)아\s+낮습니다", r"\1 낮습니다", s)
    s = re.sub(r"([가-힣]+원)아\s+낮아요", r"\1 낮아요", s)
    s = re.sub(r"([가-힣]+원)아\s+높습니다", r"\1 높습니다", s)
    s = re.sub(r"([가-힣]+원)아\s+높아요", r"\1 높아요", s)
    s = re.sub(r"([가-힣])요\s+좋아", r"\1요. 좋아", s)
    s = re.sub(r"([영일이삼사오육칠팔구십백천]+)영업일", r"\1 영업일", s)
    s = re.sub(r"최근\s*오\s*영업일\s*동안", "최근 닷새 확정 데이터로", s)
    s = re.sub(r"최근\s*오\s*영업일로는", "최근 닷새 확정 데이터로는", s)
    s = re.sub(r"최근\s*오\s*영업일", "최근 닷새 확정 데이터", s)
    s = re.sub(r"최근\s*오일\s*동안", "최근 닷새 동안", s)
    s = re.sub(r"최근\s*오일\s*기준", "최근 닷새 기준", s)
    s = re.sub(r"최근\s*오일", "최근 닷새", s)
    s = re.sub(r"(공매도)\s*(?:0|영)\s*주(?:는|가|를|로)?", r"\1 수치는", s)
    s = re.sub(r"(거래량|잔고)\s*(?:0|영)\s*주(?:는|가|를|로)?", r"\1 영 주", s)
    return s


def clean_generated_script(text):
    """AI가 콘티/마크다운 형태로 새는 경우를 실제 낭독용 대본 형태로 후처리한다."""
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _remove_overdrama_artifacts(text)
    text = text.replace("--- <", "---<").replace("---< ", "---<")
    text = re.sub(r"^\s*---\s*$", "---<", text, flags=re.M)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.M)

    banned_line_patterns = [
        r"^\s*\[[^\]]*(화면|훅|표면|이면|그래서|마무리|오프닝|체크포인트|시나리오|인트로|아웃트로|자막)[^\]]*\]\s*$",
        r"^\s*\([^)]*(카메라|인트로|아웃트로|음악|로고|화면|톤|자막|BGM)[^)]*\)\s*$",
        r"^\s*[\-–—]?\s*\[화면\s*:[^\n]*$",
        r"^\s*##?\s*(내부 참고|오늘의 지정 각도|각도|흐름 후보)[^\n]*$",
        r"^\s*[\-–—]?\s*(중심 데이터|주인공 데이터|핵심 질문|보조 데이터|제외할 데이터|전개|차별점)\s*:[^\n]*$",
        r"^\s*\[(?:각도|흐름)\d+\]\s*[=:].*$",
    ]
    kept = []
    for line in text.split("\n"):
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped:
            kept.append("")
            continue
        if any(re.search(p, stripped, flags=re.I) for p in banned_line_patterns):
            continue
        if re.match(r"^\s*(영상\s*)?(인트로|아웃트로)\s*(음악|시작|로고)?\s*$", stripped):
            continue
        kept.append(raw)
    text = "\n".join(kept)

    # 문장 중간에 남은 화면 지시 제거
    text = re.sub(r"\[화면\s*:[^\]]*\]", "", text)
    text = re.sub(r"\([^\n)]*(카메라|인트로|아웃트로|음악|로고|자막|톤|BGM)[^\n)]*\)", "", text)

    # 흔한 시작 메타 제거
    text = re.sub(r"^\s*네[,，]?\s*[‘'\"]?시장의 이면[’'\"]?입니다\.\s*", "", text)
    text = re.sub(r"^\s*[‘'\"]?시장의 이면[’'\"]?입니다\.\s*", "", text)

    # 후처리는 새 문장을 만들지 않는다. 삭제·중복정리·메타문구 제거만 수행한다.
    text = _remove_invalid_opening_continuation(text)
    text = _remove_script_meta_phrases(text)
    text = _collapse_repeated_bridge_phrases(text)
    text = _dedupe_repeated_script_blocks(text)
    text = _remove_adjacent_near_duplicate_sentences(text)
    text = _remove_script_meta_phrases(text)
    text = _collapse_repeated_bridge_phrases(text)
    text = _collapse_consecutive_korean_phrase_repeats(text)
    text = _clean_awkward_time_basis_phrases(text)
    text = _clean_common_spoken_typos(text)
    text = _remove_unexpected_foreign_scripts(text)
    text = _remove_malformed_cleanup_fragments(text)
    text = _finish_dangling_block_endings(text)
    text = _remove_post_cta_analysis_tail(text)

    # AI 음성 낭독용 독음 변환
    text = make_ai_voice_readable(text)
    text = _clean_awkward_time_basis_phrases(text)
    text = _clean_common_spoken_typos(text)

    # 과도한 빈 줄 정리, 구분자 주변 정리
    text = _format_separator_spacing(text)
    text = _restore_missing_separators(text)
    text = _rebalance_separator_rhythm(text)
    return text



_BEHIND_PHRASES = [
    "겉으로", "안쪽", "이면", "겉보기", "그런데", "이상한", "말이 안 맞", "충돌", "돈의 방향", "주체", "왜"
]


def _reinforce_behind_trace_language(text):
    """이면 분석 문장을 코드가 강제로 삽입하지 않는다."""
    return "" if text is None else str(text)


_CLOSING_SIGNATURES = ["오늘은 여기서 마무리", "마지막으로 오늘 내용은", "마지막으로 오늘 흐름은"]


def _looks_like_cta_ending(text):
    """대본의 '마지막 블록'이 CTA 안내로 끝났는지 감지한다. (마무리 블록이 이미 있으면 False)"""
    blocks = [b.strip() for b in str(text or "").split("---<") if b.strip()]
    if not blocks:
        return False
    last = blocks[-1]
    # 이미 방송 마무리 블록으로 끝났다면 추가 불필요
    if any(sig in last for sig in _CLOSING_SIGNATURES):
        return False
    cta_end_markers = [
        "기준표로 활용해 보시면 되겠습니다",
        "편하게 확인하시고 여러분만의 매매 기준표로 활용해 보시면 되겠습니다",
        "그 자리에서 바로 확인해 보실 수 있습니다",
        "바로 확인해 보실 수 있습니다",
        "무료 에이아이 포트폴리오 리포트",
        "잔고 캡처 한 장만 준비하시면 됩니다",
        "구독완료",
        "링크 안에서 바로 볼 수 있게 정리해 놨으니까",
    ]
    return any(marker in last for marker in cta_end_markers)


def _dedupe_trailing_blocks(text):
    """맨 끝에 같은 블록이 반복해 붙은 경우(마무리 중복 등) 하나만 남긴다."""
    blocks = [b.strip() for b in str(text or "").split("---<")]
    blocks = [b for b in blocks if b]
    while len(blocks) >= 2 and blocks[-1] == blocks[-2]:
        blocks.pop()
    return "\n\n---<\n\n".join(blocks)


def _ensure_post_cta_closing(text, format_name=None):
    """CTA 뒤 마무리 문단은 AI 프롬프트에서 쓰게 한다.

    코드가 고정 마무리 문장을 새로 붙이면 모든 대본이 비슷해진다.
    따라서 여기서는 중복 마무리 정리와 구분자 정리만 수행한다.
    """
    text = clean_generated_script(text)
    text = _fix_jungpro_opening_structure(text, format_name=format_name)
    text = _remove_wrong_jungpro_greeting(text, format_name=format_name)
    text = _dedupe_trailing_blocks(text)
    return _format_separator_spacing(text)


def _fix_jungpro_opening_structure(text, format_name=None):
    """정프로용 첫 블록에 섞인 인사 문장을 두 번째 블록으로 분리한다.

    새 문장을 쓰지 않고, 기존 "반갑습니다/안녕하세요, 정프로입니다" 문장의 위치만 조정한다.
    """
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    if "정프로용" not in canonical:
        return "" if text is None else str(text)
    s = str(text or "").strip()
    if not s:
        return ""
    blocks = [b.strip() for b in s.split("---<") if b.strip()]
    if not blocks:
        return s

    greet_re = re.compile(r"((?:자,\s*)?반갑습니다,\s*정프로입니다\.?|안녕하세요,\s*정프로입니다\.?)")

    # 이미 두 번째 블록 첫머리에 인사가 있으면 정상이다.
    if len(blocks) >= 2 and greet_re.match(blocks[1].lstrip()):
        return s

    first = blocks[0]
    m = greet_re.search(first)
    if m and m.start() > 0:
        hook = first[:m.start()].strip()
        greeting_and_after = first[m.start():].strip()
        new_blocks = []
        if hook:
            new_blocks.append(hook)
        new_blocks.append(greeting_and_after)
        new_blocks.extend(blocks[1:])
        return "\n\n---<\n\n".join(new_blocks)

    if m and m.start() == 0 and len(blocks) >= 2:
        greeting = m.group(0).strip()
        after_greeting = first[m.end():].strip()
        new_blocks = []
        if after_greeting:
            new_blocks.append(after_greeting)
            new_blocks.append((greeting + " " + blocks[1]).strip())
            new_blocks.extend(blocks[2:])
            return "\n\n---<\n\n".join(new_blocks)

    # 인사만 있고 후킹 문장이 없으면 새 문장을 만들 수 없으므로 그대로 둔다.
    return s


def _remove_wrong_jungpro_greeting(text, format_name=None):
    """정프로가 아닌 포맷에 섞인 정프로 인사만 제거한다.

    새 문장을 만들지 않고, "반갑습니다/안녕하세요, 정프로입니다." 문장만 삭제한다.
    """
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    if "정프로용" in canonical:
        return "" if text is None else str(text)
    s = str(text or "").strip()
    if not s:
        return ""
    blocks = []
    greet_patterns = [
        r"^\s*(?:자,\s*)?반갑습니다,\s*정프로입니다\.?\s*",
        r"^\s*안녕하세요,\s*정프로입니다\.?\s*",
        r"\s*(?:자,\s*)?반갑습니다,\s*정프로입니다\.?\s*",
        r"\s*안녕하세요,\s*정프로입니다\.?\s*",
    ]
    for block in [b.strip() for b in s.split("---<") if b.strip()]:
        b = block
        if re.search(r"정프로입니다", b):
            for pat in greet_patterns:
                b = re.sub(pat, " ", b, count=1)
            b = re.sub(r"\s{2,}", " ", b).strip()
        if b:
            blocks.append(b)
    return "\n\n---<\n\n".join(blocks)

def _memo_char_count(text):
    """메모장에서 보이는 글자 수에 가까운 단순 글자 수를 센다."""
    return len(str(text or "").replace("\r\n", "\n"))


def _separator_count(text):
    """대본 구분자 개수를 센다."""
    return len(re.findall(r"^\s*---<\s*$", str(text or ""), flags=re.M))


_REPORT_TONE_PATTERNS = [
    r"수치입니다",
    r"기록했습니다",
    r"해당합니다",
    r"시사합니다",
    r"증거입니다",
    r"대목입니다",
    r"형국입니다",
    r"흐름입니다",
    r"거래를\s*마쳤습니다",
    r"여실히",
    r"증명해주",
    r"증명합니다",
    r"나타났습니다",
    r"전망됩니다",
    r"판단됩니다",
    r"분석됩니다",
    r"확인됩니다",
    r"관측됩니다",
    r"풀이됩니다",
    r"절대\s+아닙니다",
    r"패닉",
    r"끔찍",
    r"산산조각",
    r"패대기",
    r"기형적",
    r"역대급",
    r"거대한",
    r"엄청난",
    r"무겁게\s*짓누르",
    r"뒷배",
    r"맷집",
    r"큰\s*파도",
    r"험난한",
    r"찐바닥",
    r"쪼개어\s*짚어보",
    r"살펴보겠습니다",
    r"확인해\s*보겠습니다",
]


def _find_report_tone_warnings(text, max_warn=8):
    """방송 구어체를 망치는 리포트식 표현을 찾는다. 문장은 고치지 않고 경고만 만든다."""
    found = []
    raw_blocks = [b for b in str(text or "").split("---<") if b.strip()]
    try:
        blocks = [b for b in raw_blocks if not any(h in b for h in _CTA_HINTS)]
    except NameError:
        blocks = raw_blocks
    s = "\n---<\n".join(blocks)
    for pat in _REPORT_TONE_PATTERNS:
        for m in re.finditer(pat, s):
            snippet = s[max(0, m.start() - 24):m.end() + 24].replace("\n", " ")
            found.append(snippet.strip())
            if len(found) >= max_warn:
                return found
    return found

def _split_script_blocks(text):
    """대본 블록을 구분자 기준으로 나눈다."""
    return [b.strip() for b in str(text or "").split("---<") if b.strip()]


def _split_script_sentences(text):
    """대략적인 문장 단위로 나눈다. 검사 전용이며 원문은 수정하지 않는다."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return []
    pattern = re.compile(
        r".+?(?:[.!?…]+|(?:습니다|입니다|합니다|됩니다|했어요|였어요|예요|에요|거예요|거죠|거든요|잖아요|죠|요)(?=\s|$))"
    )
    parts = [m.group(0) for m in pattern.finditer(s)]
    if not parts:
        parts = re.split(r"(?<=[.!?…])\s+", s)
    return [p.strip() for p in parts if p.strip()]


def _find_spoken_tone_warnings(text, max_warn=8):
    """대본이 리포트체로 기울었는지 검사한다. 문장은 고치지 않고 경고만 만든다."""
    s = str(text or "")
    sentences = _split_script_sentences(s)
    if not sentences:
        return ["대본 문장 수를 확인할 수 없습니다."]

    sample = sentences[:80]
    endings = {
        "yo": len(re.findall(r"(요|죠|거든요|잖아요|예요|에요)[.!?…]?(?=\s|$)", " ".join(sample))),
        "formal": len(re.findall(r"(습니다|입니다|합니다|됩니다|했습니다|였습니다)[.!?…]?(?=\s|$)", " ".join(sample))),
    }
    transition_hits = len(re.findall(
        r"(자,|그러니까|이게요|쉽게 말하면|여기서 중요한 건|다시 말해서|그럼 이걸 어떻게 봐야 하냐면|같이 볼게요|봐야 해요)",
        s,
    ))
    report_hits = len(_find_report_tone_warnings(s, max_warn=20))

    warnings = []
    if len(sample) >= 20 and endings["yo"] < max(4, len(sample) // 12):
        warnings.append("구어체 어미가 부족합니다. '~요/~죠/~거든요/~잖아요'가 적게 섞였습니다.")
    if endings["formal"] > endings["yo"] * 2 and endings["formal"] >= 12:
        warnings.append("'~습니다/~입니다' 비중이 높아 리포트 낭독처럼 들릴 수 있습니다.")
    if transition_hits < 4 and _memo_char_count(s) >= 4000:
        warnings.append("말문 전환이 부족합니다. 숫자와 해석 사이의 입말 호흡이 약합니다.")
    if report_hits >= 3:
        warnings.append("리포트체/과장 표현이 여러 개 남아 있습니다.")
    return warnings[:max_warn]


def _find_jungpro_structure_warnings(text, format_name=None, max_warn=8):
    """정프로 포맷의 후킹 → 인사 구조를 검사한다."""
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    if "정프로용" not in canonical:
        return []
    blocks = _split_script_blocks(text)
    warnings = []
    if len(blocks) < 2:
        return ["정프로용은 첫 블록 후킹 다음에 두 번째 블록 인사가 있어야 하는데, 블록이 부족합니다."]
    first = blocks[0].strip()
    second = blocks[1].lstrip()
    if re.search(r"(반갑습니다|안녕하세요|정프로입니다)", first):
        warnings.append("첫 블록에 정프로 인사가 섞여 있습니다. 첫 블록은 궁금증 유발만 해야 합니다.")
    if not re.match(r"((?:자,\s*)?반갑습니다,\s*정프로입니다\.?|안녕하세요,\s*정프로입니다\.?)", second):
        warnings.append("두 번째 블록 첫 문장이 '자, 반갑습니다, 정프로입니다.', '반갑습니다, 정프로입니다.' 또는 '안녕하세요, 정프로입니다.'로 시작하지 않습니다.")
    if not re.search(r"(왜|무엇|어느 쪽|진짜 이유|봐야 할|확인해야)", first):
        warnings.append("정프로 첫 블록의 궁금증 유발이 약합니다.")
    return warnings[:max_warn]



def _find_spoken_consistency_warnings(text, max_warn=8):
    """구어체가 초반뿐 아니라 중후반까지 유지되는지 검사한다."""
    blocks = _split_script_blocks(text)
    try:
        blocks = [b for b in blocks if not any(h in b for h in _CTA_HINTS)]
    except NameError:
        pass
    if len(blocks) < 8:
        return []
    warnings = []
    thirds = [blocks[:max(1, len(blocks)//3)], blocks[len(blocks)//3: max(len(blocks)//3 + 1, (len(blocks)*2)//3)], blocks[(len(blocks)*2)//3:]]
    labels = ["초반", "중반", "후반"]
    for label, group in zip(labels, thirds):
        chunk = " ".join(group)
        if len(chunk) < 700:
            continue
        yo = len(re.findall(r"(요|죠|거든요|잖아요|예요|에요)[.!?…]?(?=\s|$)", chunk))
        formal = len(re.findall(r"(습니다|입니다|합니다|됩니다|했습니다|였습니다)[.!?…]?(?=\s|$)", chunk))
        talk = len(re.findall(r"(자,|그러니까|이게요|쉽게 말하면|여기서 중요한 건|같이 볼게요|봐야 해요|그럼)", chunk))
        if formal >= 10 and formal > yo * 2:
            warnings.append(f"{label}이 '~습니다/~입니다' 위주로 밀려 리포트체처럼 들릴 수 있습니다.")
        if yo < 2 and talk < 1:
            warnings.append(f"{label} 구간의 입말 호흡이 약합니다.")
        if len(warnings) >= max_warn:
            break
    return warnings[:max_warn]


def _find_repetition_artifact_warnings(text, max_warn=8):
    """모델이 금지/수정 지시를 따라 말하거나 같은 숫자를 비정상 반복하는지 검사한다."""
    s = str(text or "")
    warnings = []
    bad_patterns = [
        r"아니라[^.!?\n]{0,40}아니라",
        r"말하지\s*않겠습니다",
        r"반복할\s*필요",
        r"제외하고\s*보겠습니다",
        r"수집\s*데이터",
        r"검토\s*결과",
        r"수정\s*하면",
        r"최종본",
        r"초안",
    ]
    for pat in bad_patterns:
        m = re.search(pat, s)
        if m:
            warnings.append("편집/검열 과정처럼 보이는 문장이 대본에 섞였습니다.")
            break
    numberish = re.findall(r"마이너스\s*[영일이삼사오육칠팔구십백천쩜]+|[영일이삼사오육칠팔구십백천]+쩜[영일이삼사오육칠팔구]+", s)
    for val in set(numberish):
        if val and s.count(val) >= 6:
            warnings.append(f"같은 숫자 표현이 과하게 반복됩니다: {val}")
            break
    return warnings[:max_warn]


def _find_cta_flow_warnings(text, max_warn=6):
    """CTA가 본문과 끊겨 보이거나 CTA 뒤 분석이 다시 붙는지 검사한다."""
    blocks = _split_script_blocks(text)
    if not blocks:
        return []
    cta_idx = -1
    warnings = []
    for i, b in enumerate(blocks):
        if any(h in b for h in _CTA_HINTS):
            cta_idx = i
            before_cta = b.split("주식하다 보면", 1)[0].strip() if "주식하다 보면" in b else ""
            if before_cta and len(before_cta) > 120:
                warnings.append("CTA 시작 문장이 이전 분석 문단에 붙어 있습니다. CTA 앞에 구분자를 넣어 분리해야 합니다.")
            break
    if cta_idx < 0:
        return ["CTA 본문을 찾지 못했습니다."]
    if cta_idx == 0:
        warnings.append("CTA가 너무 앞에 배치되었습니다.")
    else:
        bridge = blocks[cta_idx - 1]
        if not re.search(r"(계좌|비중|손실|종목|수익률|순서|점검|기준|흔들릴 때|헷갈릴)", bridge):
            warnings.append("CTA 직전 연결 문단이 본문 분석과 계좌 점검으로 자연스럽게 이어지지 않습니다.")
        if len(bridge) > 650:
            warnings.append("CTA 직전 연결 문단이 너무 길어 광고 전환이 늘어집니다.")
    tail = " ".join(blocks[cta_idx + 1:])
    if re.search(r"(외국인|기관|개인|공매도|거래량|종가|현재가|고점|저점).{0,30}(샀|팔|봤|확인|지켜)", tail):
        warnings.append("CTA 뒤에 종목 분석이 다시 붙었습니다.")
    return warnings[:max_warn]

def _find_format_identity_warnings(text, format_name=None, max_warn=8):
    """선택 포맷과 다른 말투/구조가 섞였는지 검사한다."""
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    blocks = _split_script_blocks(text)
    s = str(text or "")
    warnings = []
    if "정프로용" not in canonical and re.search(r"정프로입니다|반갑습니다,\s*정프로", s):
        warnings.append("정프로용이 아닌데 정프로 인사가 섞였습니다.")
    if "장중" in canonical and re.search(r"오늘\s*종가|오늘\s*마감|마감했습니다|장을\s*마쳤", s):
        warnings.append("장중 포맷에 장마감 표현이 섞였습니다.")
    if _format_is_weekend(canonical):
        if re.search(r"오늘\s*(현재가|장중|수급|종가|마감)", s):
            warnings.append("주말/주간 포맷에 당일 시세 확정 표현이 섞였습니다.")
        if blocks and re.search(r"(전일대비|현재가|장중|시가|고가|저가|종가).{0,30}(원|퍼센트)", blocks[0]):
            warnings.append("주말/주간 포맷 첫 블록이 큰그림이 아니라 당일 가격 브리핑처럼 시작합니다.")
    if "정보전달형" in canonical or "숨은정보형" in canonical:
        if re.search(r"(오늘\s*(현재가|종가|장중|마감|수급)|어제\s*(종가|마감)|이번\s*주말|다음\s*장|장마감|마감했습니다|장을\s*마쳤)", s):
            warnings.append("숨은정보형에 날짜·시황·장마감 표현이 섞였습니다.")
        if blocks and re.search(r"(현재가|종가|등락률|시가|고가|저가|거래량).{0,40}(원|퍼센트|주)", " ".join(blocks[:3])):
            warnings.append("숨은정보형 초반이 숨은 구조 설명이 아니라 가격 브리핑처럼 시작합니다.")
    if "장마감" in canonical and blocks:
        if not re.search(r"(마감|종가|정규장|마감 위치|고점|저점|거래량)", " ".join(blocks[:3])):
            warnings.append("장마감 포맷 초반에 마감 위치/거래량 중심성이 약합니다.")
    if "이면추적" in canonical and re.search(r"정프로입니다|안녕하세요", " ".join(blocks[:2])):
        warnings.append("이면추적 포맷 초반에 개인채널 인사가 섞였습니다.")
    return warnings[:max_warn]

def _score_script_quality_from_stats(stats, format_name=None):
    """100점 만점 대본 품질 추정치. 90점 미만이면 자동 보완 대상."""
    score = 100
    reasons = []
    chars = int(stats.get("chars") or 0)
    seps = int(stats.get("separators") or 0)
    is_weekend = _format_is_weekend(format_name)
    hard_min = WEEKEND_HARD_MIN_SCRIPT_CHARS if is_weekend else HARD_MIN_SCRIPT_CHARS
    target_min = MIN_SCRIPT_CHARS
    if chars < hard_min:
        score -= 14
        reasons.append(f"길이 부족: 약 {chars:,}자")
    elif chars < target_min:
        score -= 6
        reasons.append(f"권장보다 약간 짧음: 약 {chars:,}자")
    if seps < MIN_SEPARATOR_COUNT:
        score -= 5
        reasons.append(f"구분자 부족: {seps}개")
    penalty_map = {
        "spoken_tone_warnings": 5,
        "spoken_consistency_warnings": 8,
        "repetition_artifact_warnings": 12,
        "cta_flow_warnings": 8,
        "format_identity_warnings": 10,
        "jungpro_warnings": 7,
        "report_tone_warnings": 3,
    }
    for key, penalty in penalty_map.items():
        vals = stats.get(key) or []
        if vals:
            score -= min(18, penalty * len(vals))
            reasons.extend(str(v) for v in vals[:3])
    return max(0, min(100, score)), reasons[:12]
def get_script_quality_stats(text, format_name=None):
    """대본 길이/구분자 상태를 UI와 저장 로그에서 확인할 수 있게 반환한다."""
    text = str(text or "")
    chars = _memo_char_count(text)
    separators = _separator_count(text)
    report_tone_warnings = _find_report_tone_warnings(text)
    spoken_tone_warnings = _find_spoken_tone_warnings(text)
    jungpro_warnings = _find_jungpro_structure_warnings(text, format_name=format_name)
    spoken_consistency_warnings = _find_spoken_consistency_warnings(text)
    repetition_artifact_warnings = _find_repetition_artifact_warnings(text)
    cta_flow_warnings = _find_cta_flow_warnings(text)
    format_identity_warnings = _find_format_identity_warnings(text, format_name=format_name)
    result = {
        "chars": chars,
        "separators": separators,
        "meets_length": MIN_SCRIPT_CHARS <= chars <= MAX_SCRIPT_CHARS,
        "meets_separator_count": MIN_SEPARATOR_COUNT <= separators <= MAX_SEPARATOR_COUNT,
        "report_tone_warnings": report_tone_warnings,
        "spoken_tone_warnings": spoken_tone_warnings,
        "jungpro_warnings": jungpro_warnings,
        "spoken_consistency_warnings": spoken_consistency_warnings,
        "repetition_artifact_warnings": repetition_artifact_warnings,
        "cta_flow_warnings": cta_flow_warnings,
        "format_identity_warnings": format_identity_warnings,
        "meets_spoken_tone": not (spoken_tone_warnings or spoken_consistency_warnings or repetition_artifact_warnings),
        "meets_jungpro_structure": not jungpro_warnings,
        "meets_cta_flow": not cta_flow_warnings,
        "meets_format_identity": not format_identity_warnings,
    }
    quality_score, quality_reasons = _score_script_quality_from_stats(result, format_name=format_name)
    result["quality_score"] = quality_score
    result["quality_reasons"] = quality_reasons
    return result


def _build_expansion_prompt(current_text, raw_data, format_name=None):
    """짧은 대본을 OpenAI가 다시 쓰도록 요청한다. 코드가 문장을 만들지 않는다."""
    stats = get_script_quality_stats(current_text)
    canonical_format = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    weekend_rules = _weekend_big_picture_lock_rules() if (("주말용 사전작성 자료 수집" in str(raw_data or "")) or _format_is_weekend(format_name)) else ""
    return f"""
아래 [현재 대본]을 같은 사실관계와 같은 톤으로 다시 확장해서 완성본으로 출력하라.

## 목표
- 최종 길이: 메모장 기준 10,000자~12,000자.
- 현재 글자 수: 약 {stats['chars']:,}자.
- 현재 구분자 수: {stats['separators']}개.
- 구분자 ---< 는 34개~50개 사이로 맞춘다.
- 실제 출력은 36개 안팎의 말덩어리 블록으로 구성한다.
- 각 블록은 보통 180자~320자 정도로 쓴다. 숫자 공개, 반전 해석, 다음 기준 전환은 짧은 블록으로 따로 끊어도 된다.
- 단순 덧붙이기가 아니라 전체 대본을 자연스럽게 다시 작성한다.

## 확장 원칙
1. [수집 데이터]에 없는 숫자, 환산 금액, 임의 계산을 새로 만들지 마라.
2. 같은 말을 반복해 분량을 채우지 마라. 이미 나온 숫자는 배경, 반론, 확인 기준처럼 새 역할로만 다시 쓴다.
3. 첫 문장에서 던진 질문이 중반과 마지막까지 이어지게 하라.
4. 블록 전환은 고정 연결문이 아니라 내용의 논리로 이어지게 하라.
5. 수급·환율·거래량·글로벌·공매도·내부자를 순서대로 나열하지 말고, 선택된 포맷의 중심 질문에 필요한 순서로만 배치하라.
6. 장중 가격과 최근 확정 수급·외국인 지분율·공매도 시점을 반드시 구분하라.
7. 모든 포맷을 방송 구어체로 다시 써라. 장마감·장중·월요일·주간도 리포트가 아니라 실제 말이어야 한다.
7-1. 구어체는 어미만 바꾸지 말고 문장 길이와 호흡으로 만든다. 긴 문장은 나누고, 설명과 짧은 판단을 섞어라.
7-2. "수치입니다/기록했습니다/나타났습니다/전망됩니다/판단됩니다" 같은 리포트체 종결을 반복하지 마라.
7-3. 담백한 구어체로 쓴다. 공포 유튜버, 드라마 내레이션, 흥분한 진행자처럼 쓰지 마라.
7-4. 함정, 유혹, 빨간불, 짐을 싼다, 피를 말린다, 눈앞이 캄캄하다, 가슴이 철렁하다, 멱살 잡다, 샴페인을 터뜨리다, 잔인하게 보여준다, 입이 떡 벌어진다, 구명조끼, 외줄 타기, 모래 위의 성 같은 감정 과잉 표현을 쓰지 마라.
7-4-1. 독야청청, 귀추가 주목, 향방, 초미의 관심사, 이목이 쏠림, 촉각을 곤두세움, 관건, 분수령 같은 뉴스·칼럼식 관용어를 쓰지 마라. "혼자 오르기 어렵다", "어디로 움직일지 봐야 한다"처럼 쉬운 말로 풀어라.
7-4-2. 감성 터치는 조금 허용한다. 다만 감정 문장으로 오프닝을 길게 끌지 말고, 한 문장 안에서 끝낸 뒤 바로 숫자와 확인 기준으로 돌아와라.
7-5. "~습니다"로만 이어지는 설명문은 실패다. "자,", "그러니까", "이게요", "쉽게 말하면", "여기서 중요한 건", "그럼 이걸 어떻게 봐야 하냐면" 같은 자연스러운 말문을 섞어라.
7-6. 종결은 "~요", "~예요/~에요", "~거든요", "~잖아요", "~죠", 짧은 "~다/합니다"를 섞어라. 한 가지 어미로만 밀지 마라.
7-7. 숫자 뒤에는 바로 말로 풀어라. 예: "거래량은 줄었어요. 그러니까 가격은 올랐지만 돈이 강하게 붙은 흐름은 아닌 거예요."
7-8. 한 블록은 "말문 1문장 → 숫자 1~2문장 → 해석 1~2문장 → 짧은 판단 1문장"의 호흡으로 쓴다.
7-9. "거대한/엄청난/무겁게 짓누르는/맷집/뒷배/큰 파도/찐바닥/험난한 과정" 같은 과장 표현을 쓰지 마라. 숫자가 강하면 문장은 더 차분해야 한다.
7-10. 첫 문장은 사건 설명이 아니라 숫자 충돌로 시작한다. 좋은 숫자 하나와 나쁜 돈의 방향 하나를 바로 부딪혀라.
7-11. 한 블록에 숫자 제시, 긴 해석, 다음 주제 전환을 모두 몰아넣지 마라. 숫자를 던진 뒤에는 다음 블록에서 해석하는 식으로 유튜브 편집 컷 호흡을 만든다.
7-11-1. 숫자 과밀 블록은 반드시 나눈다. 한 블록에 종가·등락률·고가·저가·수급·누적수급이 같이 있으면 실패다.
7-11-2. "첫 체결권/마지막 체결권" 라벨은 대본에서 반복하지 말고 "장 초반 가격/마감 무렵 가격"으로 풀어라.
7-11-3. 억 단위 수급 금액은 "이천사백십사억"에서 끝내지 말고 반드시 "이천사백십사억 원"처럼 원을 붙여라.
7-12. 첫 블록 마지막 문장은 반드시 질문으로 끝낸다. 첫 블록에서 "상황입니다/흐름입니다"처럼 정리하면 실패다. 답을 뒤로 미뤄라.
7-13. 첫 블록 첫 문장에는 대본 종목명을 반드시 넣어라. 주어 없는 실적 숫자로 시작하지 마라.
7-14. 첫 3블록에서는 사업부·사업보고서·제품군·회사 구조 설명을 하지 마라. 초반은 가격, 거래량, 실적 숫자, 수급 충돌로 시청자가 무엇을 보는지 붙잡아야 한다.
8. 포맷명, 작업 과정, 화면 지시, 작가용 문구를 출력하지 마라.
8-1. 전일·어제 배경을 확장 소재로 부풀리지 마라. 실적·급락·서킷브레이커·전일 외국인 매도는 오늘 흐름을 설명하는 배경으로 2~3블록 안에서만 사용한다.
8-2. 장중·장마감 대본을 확장할 때는 전일 복기보다 오늘 가격, 거래량, 시간대별 흐름, 확정 수급과 미확정 수급의 구분을 먼저 보강한다.
8-3. "방금 전", "방금", "조금 전"은 현재 장중 가격·거래량 움직임에만 쓴다. 실적 발표, DART 공시, 뉴스, 전일 수급에는 쓰지 말고 "어제", "전일", "최근 확인된 자료"로 고쳐라.
9. CTA 본문은 고정 문구로 유지한다. CTA 문장, 순서, 혜택, 표현을 새로 쓰거나 줄이거나 늘리지 마라.
9-1. 확장은 본문 분석 부족분에만 적용한다. CTA를 확장 대상으로 삼지 마라.
9-2. CTA 직전 연결 문단만 본문 핵심을 받아 자연스럽게 쓴다.
9-3. CTA 뒤에는 종목 분석을 다시 이어 붙이지 마라. "오늘 수급은 장 마감 뒤 확인" 같은 분석성 문장은 CTA 전에 배치하고, CTA 뒤에는 짧은 방송 마무리만 둔다.
10. 최종 출력은 완성 대본만 출력한다.

{weekend_rules}

## 대본 포맷
{canonical_format}

[현재 대본]
{current_text}

[수집 데이터]
{raw_data}
""".strip()


def _build_tone_rewrite_prompt(current_text, raw_data, format_name=None):
    """말투가 리포트체로 나온 대본을 OpenAI가 다시 쓰도록 요청한다. 코드가 문장을 만들지 않는다."""
    stats = get_script_quality_stats(current_text, format_name=format_name)
    canonical_format = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    tone_notes = []
    tone_notes.extend(stats.get("spoken_tone_warnings") or [])
    tone_notes.extend(stats.get("report_tone_warnings") or [])
    tone_notes.extend(stats.get("jungpro_warnings") or [])
    tone_note_text = "\n".join(f"- {x}" for x in tone_notes[:12]) or "- 말투를 더 자연스러운 방송 구어체로 정리한다."
    weekend_rules = _weekend_big_picture_lock_rules() if (("주말용 사전작성 자료 수집" in str(raw_data or "")) or _format_is_weekend(format_name)) else ""
    if "정프로용" in canonical_format:
        format_lock_rules = """
## 정프로용 추가 규칙
- 첫 블록은 궁금증 유발만 한다.
- 첫 블록 다음에 ---< 를 넣고, 두 번째 블록 첫 문장은 "자, 반갑습니다, 정프로입니다.", "반갑습니다, 정프로입니다." 또는 "안녕하세요, 정프로입니다."로 시작한다.
- 정프로 인사는 첫 줄에 나오면 실패다.
""".strip()
    else:
        format_lock_rules = """
## 비정프로 포맷 추가 규칙
- 이 대본은 정프로 개인채널 포맷이 아니다.
- 개인채널식 인사말이나 진행자 이름 인사를 쓰지 마라.
- 첫 블록부터 바로 핵심 숫자 충돌과 질문으로 들어간다.
""".strip()
    return f"""
아래 [현재 대본]은 사실관계는 유지하되 말투와 흐름을 다시 정리해야 한다.
새로운 숫자, 새로운 뉴스, 새로운 계산은 절대 만들지 말고, [수집 데이터]와 [현재 대본] 안에 있는 사실만 사용하라.

## 지금 발견된 문제
{tone_note_text}

## 다시 쓰기 목표
- 완성 대본만 출력한다. 제목, 점검표, 해설, 마크다운은 쓰지 않는다.
- 글자 수는 가능한 한 현재 분량을 유지하되, 최종 10,000~12,000자에 가깝게 쓴다.
- ---< 구분자는 34~50개 사이로 유지한다.
- 코드나 시스템이 쓴 것처럼 보이는 연결문을 반복하지 마라.
- 모든 포맷은 실제 사람이 말하는 구어체다.
- "~습니다/~입니다"만 이어지는 리포트 낭독을 피한다.
- "~요", "~죠", "~거든요", "~잖아요", 짧은 "~다/합니다"를 자연스럽게 섞는다.
- 긴 리포트 문장은 둘로 나눈다. 숫자 하나, 뜻 하나, 판단 하나로 간다.
- "자,", "그러니까", "이게요", "쉽게 말하면", "여기서 중요한 건" 같은 말문을 필요한 곳에만 섞는다.
- 과장 표현, 재난 비유, 전쟁 비유, 뉴스·칼럼식 관용어는 쓰지 않는다.
- "수치입니다/기록했습니다/나타났습니다/전망됩니다/판단됩니다/분석됩니다/확인됩니다" 같은 종결을 쓰지 않는다.
- CTA 본문은 고정 문구다. CTA 문장, 순서, 혜택, 표현을 새로 쓰거나 줄이거나 늘리지 마라.
- CTA 직전 연결 문단만 본문 흐름에 맞게 자연스럽게 정리할 수 있다.

{format_lock_rules}

{weekend_rules}

## 대본 포맷
{canonical_format}

[현재 대본]
{current_text}

[수집 데이터]
{raw_data}
""".strip()

def _require_openai_sdk():
    """OpenAI SDK를 불러온다."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai 모듈이 없습니다. 먼저 `pip install openai`를 실행하세요.") from e
    return OpenAI


def _make_openai_client():
    """OpenAI 클라이언트를 만든다."""
    if not OPENAI_API_KEY:
        raise RuntimeError("config.txt에 OPENAI_API_KEY를 입력하세요.")
    OpenAI = _require_openai_sdk()
    return OpenAI(api_key=OPENAI_API_KEY)


def _extract_openai_text(response):
    """Responses API 응답에서 텍스트를 안전하게 꺼낸다."""
    text = getattr(response, "output_text", None)
    if text:
        return text
    chunks = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            val = getattr(content, "text", None)
            if val:
                chunks.append(val)
    return "\n".join(chunks).strip()


def _generate_text_with_openai(client, types, model, prompt, temperature=0.7):
    """OpenAI Responses API 호출 공통 함수.

    gpt-5.5 신규/낮은 티어 계정은 TPM이 10,000인 경우가 많다.
    긴 원자료 + 긴 출력 상한을 한 번에 요청하면 429 rate_limit_exceeded가 나므로
    출력 상한을 낮춰 자동 재시도한다.
    """
    model = (model or OPENAI_TEXT_MODEL or "gpt-5.5").strip()
    prompt = str(prompt or "")
    token_limits = []
    for n in [OPENAI_MAX_OUTPUT_TOKENS, 5000, 4000, 3000, 2200]:
        try:
            n = int(n)
        except Exception:
            continue
        if n > 0 and n not in token_limits:
            token_limits.append(n)
    last_error = None
    response = None
    for max_tokens in token_limits:
        kwargs = {
            "model": model,
            "input": prompt,
            "max_output_tokens": max_tokens,
        }
        try:
            response = client.responses.create(**kwargs)
            break
        except TypeError:
            kwargs.pop("max_output_tokens", None)
            response = client.responses.create(**kwargs)
            break
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            if "rate_limit_exceeded" in msg or "tokens per min" in msg or "requested" in msg and "tpm" in msg:
                print(f"[OpenAI] TPM 제한으로 max_output_tokens={max_tokens} 요청 실패, 더 낮춰 재시도합니다.")
                time.sleep(3)
                continue
            raise
    if response is None:
        raise last_error or RuntimeError("OpenAI 호출 실패")
    text = _extract_openai_text(response)
    if not text:
        raise RuntimeError("OpenAI 응답이 비어 있습니다. API 상태나 모델명을 확인하세요.")
    return text


def _generate_text_with_gemini(model, prompt, temperature=0.7):
    """Gemini 텍스트 생성. google-genai 신버전과 google-generativeai 구버전을 모두 지원한다."""
    if not GEMINI_API_KEY:
        raise RuntimeError("config.txt에 GEMINI_API_KEY를 입력하세요.")
    model = (model or GEMINI_TEXT_MODEL or "gemini-2.5-pro").strip()
    prompt = str(prompt or "")

    # 신 SDK: google-genai
    try:
        from google import genai
        try:
            from google.genai import types as genai_types
        except Exception:
            genai_types = None
        client = genai.Client(api_key=GEMINI_API_KEY)
        kwargs = {"model": model, "contents": prompt}
        output_token_limit = max(8192, min(int(OPENAI_MAX_OUTPUT_TOKENS or 12000), 16000))
        if genai_types is not None:
            try:
                kwargs["config"] = genai_types.GenerateContentConfig(
                    temperature=max(0.2, min(float(temperature or 0.7), 1.0)),
                    max_output_tokens=output_token_limit,
                )
            except Exception:
                try:
                    kwargs["config"] = genai_types.GenerateContentConfig(
                        temperature=max(0.2, min(float(temperature or 0.7), 1.0))
                    )
                except Exception:
                    pass
        response = client.models.generate_content(**kwargs)
        text = getattr(response, "text", None)
        if text:
            return text.strip()
        chunks = []
        for cand in getattr(response, "candidates", []) or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", []) or []:
                val = getattr(part, "text", None)
                if val:
                    chunks.append(val)
        text = "\n".join(chunks).strip()
        if text:
            return text
    except ImportError:
        pass
    except Exception as e:
        last_new_sdk_error = e
    else:
        last_new_sdk_error = None

    # 구 SDK: google-generativeai
    try:
        import google.generativeai as genai_old
    except ImportError as e:
        raise RuntimeError("Gemini SDK가 없습니다. `pip install google-genai`를 실행하세요.") from e
    try:
        genai_old.configure(api_key=GEMINI_API_KEY)
        gm = genai_old.GenerativeModel(model)
        output_token_limit = max(8192, min(int(OPENAI_MAX_OUTPUT_TOKENS or 12000), 16000))
        response = gm.generate_content(
            prompt,
            generation_config={
                "temperature": max(0.2, min(float(temperature or 0.7), 1.0)),
                "max_output_tokens": output_token_limit,
            },
        )
        text = getattr(response, "text", None)
        if text:
            return text.strip()
        chunks = []
        for cand in getattr(response, "candidates", []) or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", []) or []:
                val = getattr(part, "text", None)
                if val:
                    chunks.append(val)
        text = "\n".join(chunks).strip()
        if not text:
            raise RuntimeError("Gemini 응답이 비어 있습니다. 모델명이나 API 상태를 확인하세요.")
        return text
    except Exception as e:
        if "last_new_sdk_error" in locals() and last_new_sdk_error:
            raise RuntimeError(f"Gemini 호출 실패: {e} / 신SDK 오류: {last_new_sdk_error}") from e
        raise


def _normalize_ai_engine(engine=None):
    engine = str(engine or "chain").strip().lower()
    if engine in ("fast", "quick", "speed", "fast_openai", "fast-openai", "quick_openai"):
        return "fast_openai" if OPENAI_API_KEY else "fast_gemini"
    if engine in ("fast_gemini", "fast-gemini", "quick_gemini"):
        return "fast_gemini" if GEMINI_API_KEY else "fast_openai"
    if engine in ("chain", "dual", "both", "openai_gemini", "openai-gemini"):
        return "chain"
    if engine in ("gemini", "google"):
        return "gemini"
    if engine in ("openai", "gpt"):
        return "openai"
    if engine in ("auto", "mixed"):
        return "chain" if (OPENAI_API_KEY and GEMINI_API_KEY) else ("gemini" if GEMINI_API_KEY else "openai")
    return "chain" if (OPENAI_API_KEY and GEMINI_API_KEY) else ("gemini" if GEMINI_API_KEY else "openai")

def _generate_initial_script_text(prompt, engine="chain", model=None, temperature=0.7):
    """초안 생성 엔진 선택. 후처리·검증은 기존 파이프라인을 재사용한다."""
    engine = _normalize_ai_engine(engine)
    if engine in ("gemini", "fast_gemini"):
        return _generate_text_with_gemini(
            model=model or GEMINI_TEXT_MODEL,
            prompt=prompt,
            temperature=temperature,
        )
    client = _make_openai_client()
    return _generate_text_with_openai(
        client=client,
        types=None,
        model=model or OPENAI_TEXT_MODEL,
        prompt=prompt,
        temperature=temperature,
    )


def _ensure_script_length_with_openai(client, types, text, raw_data, model, temperature=0.7, format_name=None):
    """대본이 10,000자 미만이거나 구분자 30개 미만이면 OpenAI로 자동 확장한다."""
    text = clean_generated_script(text)
    for _ in range(MAX_AI_EXPAND_ROUNDS):
        stats = get_script_quality_stats(text, format_name=format_name)
        if stats["chars"] >= MIN_SCRIPT_CHARS and stats["separators"] >= MIN_SEPARATOR_COUNT:
            break
        expansion_prompt = _build_expansion_prompt(text, raw_data, format_name=format_name)
        expanded = _generate_text_with_openai(
            client=client,
            types=types,
            model=model,
            prompt=expansion_prompt,
            temperature=max(0.35, min(float(temperature or 0.55), 0.55)),
        )
        expanded = clean_generated_script(expanded)
        if _memo_char_count(expanded) <= _memo_char_count(text) + 500:
            # 확장이 거의 안 됐으면 무한 반복하지 않고 중단한다.
            break
        text = expanded
    text = _ensure_post_cta_closing(text, format_name=format_name)
    return _rebalance_separator_rhythm(text)


def _ensure_spoken_tone_with_openai(client, types, text, raw_data, model, temperature=0.7, format_name=None):
    """말투/정프로 구조가 약하면 OpenAI에게 한 번만 다시 쓰게 한다. 코드가 문장을 만들지 않는다."""
    text = clean_generated_script(text)
    stats = get_script_quality_stats(text, format_name=format_name)
    canonical = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    spoken_warnings = stats.get("spoken_tone_warnings") or []
    report_warnings = stats.get("report_tone_warnings") or []
    jungpro_warnings = stats.get("jungpro_warnings") or []
    needs_rewrite = (
        len(spoken_warnings) >= 2
        or len(report_warnings) >= 5
        or ("정프로용" in canonical and bool(jungpro_warnings))
    )
    if not needs_rewrite:
        return _remove_wrong_jungpro_greeting(text, format_name=format_name)

    rewrite_prompt = _build_tone_rewrite_prompt(text, raw_data, format_name=format_name)
    rewritten = _generate_text_with_openai(
        client=client,
        types=types,
        model=model,
        prompt=rewrite_prompt,
        temperature=max(0.35, min(float(temperature or 0.55), 0.55)),
    )
    rewritten = clean_generated_script(rewritten)
    if _memo_char_count(rewritten) < max(3000, int(_memo_char_count(text) * 0.75)):
        return _remove_wrong_jungpro_greeting(text, format_name=format_name)
    rewritten = _fix_jungpro_opening_structure(rewritten, format_name=format_name)
    rewritten = _remove_wrong_jungpro_greeting(rewritten, format_name=format_name)
    return _rebalance_separator_rhythm(rewritten)


def _final_length_guard_with_openai(client, types, text, raw_data, model, temperature=0.7, format_name=None):
    """후처리로 짧아진 대본을 저장 직전에 다시 확장한다. 코드가 문장을 새로 만들지는 않는다."""
    text = clean_generated_script(text)
    is_weekend_raw = "주말용 사전작성 자료 수집" in str(raw_data or "")
    hard_min = WEEKEND_HARD_MIN_SCRIPT_CHARS if (is_weekend_raw or _format_is_weekend(format_name)) else HARD_MIN_SCRIPT_CHARS
    stats = get_script_quality_stats(text, format_name=format_name)
    if stats["chars"] >= hard_min and stats["separators"] >= MIN_SEPARATOR_COUNT:
        return text

    expanded = _ensure_script_length_with_openai(
        client=client,
        types=types,
        text=text,
        raw_data=raw_data,
        model=model,
        temperature=temperature,
        format_name=format_name,
    )
    expanded = _rewrite_market_phase_violation_with_openai(
        client=client,
        types=types,
        text=expanded,
        raw_data=raw_data,
        model=model,
        format_name=format_name,
        temperature=temperature,
    )
    expanded = _remove_blocks_with_unverified_numbers(expanded, raw_data)
    expanded = _ensure_post_cta_closing(expanded, format_name=format_name)
    expanded = _restore_missing_separators(expanded)
    expanded = _rebalance_separator_rhythm(expanded)
    expanded = clean_generated_script(expanded)

    final_stats = get_script_quality_stats(expanded, format_name=format_name)
    if final_stats["chars"] < hard_min:
        # 길이가 짧아도 저장은 막지 않는다.
        # 모델 확장이 충분히 안 된 경우 사용자가 결과물을 직접 확인할 수 있어야 한다.
        return expanded
    return expanded



# ──────────────────────────────────────────────
# OpenAI → Gemini → OpenAI → Gemini 체인 대본 생성
# ──────────────────────────────────────────────
def _build_chain_refine_prompt(current_text, raw_data, format_name=None, stage="gemini_refine"):
    """두 모델이 서로 보완하는 체인용 프롬프트. 출력은 항상 완성 대본만 요구한다."""
    canonical_format = SCRIPT_FORMAT_ALIASES.get(format_name, format_name or "")
    weekend_rules = _weekend_big_picture_lock_rules() if (("주말용 사전작성 자료 수집" in str(raw_data or "")) or _format_is_weekend(format_name)) else ""
    if "정프로용" in canonical_format:
        format_lock = """
- 정프로용은 첫 블록에서 궁금증만 만든다.
- 첫 블록 다음에 ---< 를 넣고, 두 번째 블록 첫 문장을 "자, 반갑습니다, 정프로입니다."로 시작한다.
- 첫 블록에는 인사를 넣지 않는다. 인사는 한 번만 한다.
""".strip()
    else:
        format_lock = """
- 정프로용이 아니면 정프로 인사나 개인채널 인사를 쓰지 않는다.
- 첫 블록부터 바로 핵심 충돌과 질문으로 들어간다.
""".strip()

    stage_note = {
        "gemini_refine": "OpenAI 초안을 바탕으로 구조, 구어체, 흐름, CTA 연결을 다듬는다.",
        "openai_polish": "Gemini가 다듬은 대본에서 어색한 반복, 숫자 과밀, 국면 혼동, 문장 깨짐만 잡는다.",
        "gemini_final": "OpenAI가 다듬은 대본을 최종 방송 대본으로 자연스럽게 마무리한다.",
    }.get(stage, "대본을 자연스럽게 다듬는다.")

    return f"""
당신은 한국 주식 유튜브 대본 편집자다.
아래 [현재 대본]과 [수집 데이터]만 사용해서 완성 대본을 다시 출력하라.

## 이번 단계
{stage_note}

## 절대 출력 금지
- 평가표, 점검표, 수정 이유, 해설, 마크다운, 제목, 목차를 쓰지 마라.
- "이 부분은", "수정하면", "검토 결과", "초안", "최종본" 같은 편집자 말을 대본에 넣지 마라.
- 금지 문구를 설명하다가 대본에 복사하지 마라.
- 같은 숫자를 부정형으로 반복하거나, 검열·수정 과정처럼 보이는 문장을 쓰지 마라.
- "수집 데이터", "포맷", "주말 대본", "당일 등락률" 같은 작가용 표현을 쓰지 마라.

## 최종 대본 규칙
- 완성 대본만 출력한다.
- 최종 분량은 CTA 포함 10,000~12,000자다. 어떤 포맷이든 최소 9,000자 미만이면 실패다.
- CTA 전 본문 분석만으로 최소 24블록 이상을 만든다. 전체 ---< 구분자는 34~50개를 목표로 한다.
- 짧게 요약하지 마라. 같은 말을 반복하지 말고 가격, 수급, 뉴스, 밸류, 시장 흐름, 계좌 관점으로 블록을 나눠 충분히 풀어라.
- ---< 구분자를 유지한다.
- 실제 사람이 읽는 구어체다. 숫자 낭독문이 아니라 흐름 설명이어야 한다.
- 첫 블록은 숫자 폭탄 금지다. 핵심 숫자 1~2개와 질문만 남긴다.
- 한 블록에 종가·등락률·고가·저가·수급·누적수급을 모두 몰아넣지 마라.
- "첫 체결권/마지막 체결권" 대신 "장 초반 가격/마감 무렵 가격"으로 말한다.
- 억 단위 수급 금액은 반드시 "원"을 붙인다.
- 소수점은 AI 낭독용으로 "쩜"을 쓴다. 단, 29쩜삼처럼 숫자 혼종을 쓰지 말고 이십구쩜삼처럼 한글로 풀어라.
- CTA 본문은 고정 문구다. CTA 문장, 순서, 혜택, 표현을 새로 쓰거나 줄이거나 늘리지 마라.
- CTA 직전 연결 문단만 본문 흐름에 맞게 자연스럽게 이어라.
- CTA 뒤에는 분석을 길게 다시 붙이지 말고 짧게 마무리한다.\n- 초반만 구어체로 쓰고 중반·후반이 리포트체로 돌아가면 실패다. 끝까지 "요/죠/거든요/잖아요"와 짧은 판단문을 섞어라.\n- 같은 숫자 표현을 세 번 이상 반복하지 마라. 반복 대신 그 숫자가 뜻하는 방향을 쉬운 말로 풀어라.\n- CTA 직전 문단은 계좌 점검, 비중, 손실, 종목 우선순위 중 하나로 자연스럽게 이어라.\n- CTA 뒤에는 외국인, 공매도, 거래량, 종가 같은 종목 분석을 다시 붙이지 마라.
- 새 숫자, 새 뉴스, 새 계산을 만들지 마라.

## 포맷 고정
{format_lock}

{weekend_rules}

## 대본 포맷
{canonical_format}

[현재 대본]
{current_text}

[수집 데이터]
{raw_data}
""".strip()


def _generate_openai_gemini_chain_script(prompt, raw_data, format_name=None, model=None, temperature=0.7):
    """OpenAI 초안 → Gemini 다듬기 → OpenAI 어색함 점검 → Gemini 최종 출력."""
    client = _make_openai_client()
    openai_model = model or OPENAI_TEXT_MODEL

    draft = _generate_text_with_openai(
        client=client,
        types=None,
        model=openai_model,
        prompt=prompt,
        temperature=temperature,
    )
    draft = clean_generated_script(draft)
    if not draft.strip():
        raise RuntimeError("openai 초안 응답이 비어 있습니다.")

    gemini_prompt = _build_chain_refine_prompt(draft, raw_data, format_name=format_name, stage="gemini_refine")
    refined = _generate_text_with_gemini(
        model=GEMINI_TEXT_MODEL,
        prompt=gemini_prompt,
        temperature=max(0.35, min(float(temperature or 0.55), 0.65)),
    )
    refined = clean_generated_script(refined)
    if _memo_char_count(refined) < max(2500, int(_memo_char_count(draft) * 0.45)):
        refined = draft

    polish_prompt = _build_chain_refine_prompt(refined, raw_data, format_name=format_name, stage="openai_polish")
    polished = _generate_text_with_openai(
        client=client,
        types=None,
        model=openai_model,
        prompt=polish_prompt,
        temperature=max(0.25, min(float(temperature or 0.45), 0.55)),
    )
    polished = clean_generated_script(polished)
    if _memo_char_count(polished) < max(2500, int(_memo_char_count(refined) * 0.55)):
        polished = refined

    final_prompt = _build_chain_refine_prompt(polished, raw_data, format_name=format_name, stage="gemini_final")
    final = _generate_text_with_gemini(
        model=GEMINI_TEXT_MODEL,
        prompt=final_prompt,
        temperature=max(0.3, min(float(temperature or 0.5), 0.6)),
    )
    final = clean_generated_script(final)
    if _memo_char_count(final) < max(2500, int(_memo_char_count(polished) * 0.55)):
        final = polished

    # 최종본이 90점 미만이면 Gemini가 최대 2번 더 보완한다.
    hard_min = WEEKEND_HARD_MIN_SCRIPT_CHARS if (("주말용 사전작성 자료 수집" in str(raw_data or "")) or _format_is_weekend(format_name)) else HARD_MIN_SCRIPT_CHARS
    quality_keys = ("spoken_consistency_warnings", "repetition_artifact_warnings", "cta_flow_warnings", "format_identity_warnings", "jungpro_warnings")
    for repair_round in range(2):
        stats = get_script_quality_stats(final, format_name=format_name)
        score = int(stats.get("quality_score") or 0)
        repair_notes = list(stats.get("quality_reasons") or [])
        if _memo_char_count(final) < hard_min:
            repair_notes.insert(0, f"분량이 부족합니다. 현재 약 {_memo_char_count(final):,}자이고 최소 약 {hard_min:,}자 이상으로 보강해야 합니다.")
        for key in quality_keys:
            repair_notes.extend(stats.get(key) or [])
        if score >= 90 and not repair_notes:
            break
        if score >= 90 and _memo_char_count(final) >= hard_min:
            break
        repair_prompt = _build_chain_refine_prompt(final, raw_data, format_name=format_name, stage="gemini_final")
        repair_prompt += f"\n\n## 현재 품질 점수: {score}점"
        repair_prompt += "\n## 90점 이상으로 올리기 위해 반드시 해결할 문제\n"
        repair_prompt += "\n".join(f"- {x}" for x in repair_notes[:14])
        repair_prompt += "\n\n보완 지시:"
        repair_prompt += "\n- 완성 대본만 출력하라."
        repair_prompt += "\n- 최종 분량은 CTA 포함 최소 9,000자, 권장 10,000~12,000자다. 짧으면 실패다."
        repair_prompt += "\n- 본문 분석을 억지 반복 없이 6~10블록 보강하라. 특히 CTA 전 본문을 충분히 늘려라."
        repair_prompt += "\n- 전체 구분자는 34~50개를 목표로 하고, CTA 전 본문만 최소 24블록 이상으로 구성하라."
        repair_prompt += "\n- 초반부터 후반까지 구어체를 유지하라."
        repair_prompt += "\n- CTA 직전 블록은 이 종목을 계좌에서 어떻게 들고 있는지, 비중, 손실, 점검 순서로 자연스럽게 연결하라."
        repair_prompt += "\n- CTA 시작 문장은 반드시 새 블록으로 분리하라."
        repair_prompt += "\n- CTA 본문은 고정 문구 그대로 유지하라."
        repair_prompt += "\n- CTA 뒤에는 종목 분석을 다시 붙이지 말고 짧게 마무리하라."
        repaired = _generate_text_with_gemini(
            model=GEMINI_TEXT_MODEL,
            prompt=repair_prompt,
            temperature=max(0.25, min(float(temperature or 0.45), 0.55)),
        )
        repaired = clean_generated_script(repaired)
        if _memo_char_count(repaired) < max(3500, int(_memo_char_count(final) * 0.80)):
            break
        repaired_stats = get_script_quality_stats(repaired, format_name=format_name)
        old_score = int(stats.get("quality_score") or 0)
        new_score = int(repaired_stats.get("quality_score") or 0)
        old_warns = sum(len(stats.get(k) or []) for k in quality_keys)
        new_warns = sum(len(repaired_stats.get(k) or []) for k in quality_keys)
        if new_score >= old_score or new_warns <= old_warns or _memo_char_count(repaired) > _memo_char_count(final) + 800:
            final = repaired
        else:
            break
    return final
# ──────────────────────────────────────────────
# 대본 숫자 자동 검증: 한글 독음을 값으로 역환산해 수집 데이터와 대조
# ──────────────────────────────────────────────
_KDIGIT = {"영": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5,
           "육": 6, "칠": 7, "팔": 8, "구": 9}
_KSMALL = {"십": 10, "백": 100, "천": 1000}
_KBIG = {"만": 10**4, "억": 10**8, "조": 10**12}

# 검증에서 제외할 블록 판별용 (CTA·자료안내는 데이터 밖 숫자가 정상)
_CTA_HINTS = ["구독", "좋아요", "링크", "알림센터", "신청 페이지", "시크릿", "책자",
              "세력단가", "순환 맵", "템플릿", "전략노트", "리딩방", "고정 댓글",
              "무료 에이아이", "포트폴리오 리포트", "보유종목", "잔고 캡처",
              "증권 에이아이", "도넛 그래프", "판독률", "비중 쏠림", "리밸런싱"]

# 근사·파생 표현 (약/거의/가까이 등) 은 원본과 달라도 정상
_APPROX_HINTS = ["약 ", "거의", "가까", "넘게", "넘는", "이상", "수준", "정도", "남짓", "안팎"]


def _kor_group_to_num(s):
    """'삼천이백구십사' 같은 만 미만 한글 수를 정수로"""
    total, cur = 0, 0
    for ch in s:
        if ch in _KDIGIT:
            cur = _KDIGIT[ch]
        elif ch in _KSMALL:
            total += (cur or 1) * _KSMALL[ch]
            cur = 0
    return total + cur


def kor_to_num(s):
    """'구조 삼천이백구십사억', '사십칠쩜삼팔' 등 한글 독음 → float. 실패 시 None"""
    s = re.sub(r"[\s,]", "", str(s))
    if not s:
        return None
    # 소수부 분리
    frac = 0.0
    if "쩜" in s or "점" in s:
        sep = "쩜" if "쩜" in s else "점"
        s, _, tail = s.partition(sep)
        digits = "".join(str(_KDIGIT[c]) for c in tail if c in _KDIGIT)
        if digits:
            frac = float("0." + digits)
        if not s:
            return frac
    total, remainder = 0, s
    for unit_ch, mul in [("조", _KBIG["조"]), ("억", _KBIG["억"]), ("만", _KBIG["만"])]:
        if unit_ch in remainder:
            head, _, remainder = remainder.rpartition(unit_ch)
            # rpartition은 마지막 단위 기준 — 조/억 순서로 왼쪽부터 잘라야 하므로 partition 사용
    # 위 방식 대신 왼쪽부터 순차 분해
    total, remainder = 0, s
    for unit_ch in ("조", "억", "만"):
        if unit_ch in remainder:
            head, _, remainder = remainder.partition(unit_ch)
            g = _kor_group_to_num(head) if head else 1
            if g == 0 and head:
                g = 0
            total += g * _KBIG[{"조": "조", "억": "억", "만": "만"}[unit_ch]]
    tail_val = _kor_group_to_num(remainder) if remainder else 0
    if total == 0 and tail_val == 0 and "영" not in s:
        return None
    return float(total + tail_val) + frac


def _collect_reference_values(raw_data):
    """수집 데이터에서 (값, 단위클래스) 기준집합 추출"""
    refs = {"pct": set(), "krw": set(), "x": set(), "shares": set(), "amt": set()}
    for m in re.finditer(r"([-+]?[0-9][0-9,]*(?:\.[0-9]+)?)\s*(조|억|원|%p|%|배|주)", str(raw_data)):
        val = abs(float(m.group(1).replace(",", "")))
        unit = m.group(2)
        if unit == "%" or unit == "%p":
            refs["pct"].add(round(val, 2))
        elif unit == "원":
            refs["krw"].add(round(val, 1))
        elif unit == "배":
            refs["x"].add(round(val, 2))
        elif unit == "주":
            refs["shares"].add(round(val, 0))
        elif unit == "억":
            refs["amt"].add(round(val * 1e8, 0))
        elif unit == "조":
            refs["amt"].add(round(val * 1e12, 0))
    return refs


def _amount_variants_krw(val):
    """원 단위로 해석된 한글 금액의 한국식 단위 오탐을 줄이기 위한 후보값들."""
    vals = {float(val)}
    # 대본에서 "사조 사천억 원"처럼 말하면 kor_to_num은 4조4천억 '원'으로 읽는다.
    # 원자료는 보통 억 단위 금액을 val*1e8로 저장하므로, 값 자체가 원 단위로 들어온 경우와
    # 억 단위 독음이 섞인 경우를 모두 비교 후보로 둔다.
    if val >= 1e4:
        vals.add(float(val) * 1e8)
    # 반대로 원자료가 원 단위인데 대본 독음이 억 단위 값처럼 잡힌 경우도 제한적으로 허용한다.
    if val >= 1e8:
        vals.add(float(val) / 1e8)
    return vals


def _is_too_short_number_token(token, unit):
    """'천 원', '십만 주' 같은 짧은 조각 오탐을 줄인다."""
    compact = re.sub(r"\s+", "", str(token or ""))
    if not compact:
        return True
    # 금액의 한두 글자짜리 단위 조각은 대체로 문장 일부를 잘못 잡은 것이다.
    if unit == "원" and len(compact) <= 2:
        return True
    # 금액에서 '천 원/만 원/백 원' 같은 작은 조각은 대본 핵심 수치 검증에서 제외한다.
    if unit == "원" and compact in {"십", "백", "천", "만", "십만", "백만"}:
        return True
    return False


def _is_round_number(v):
    """방송식 반올림 수인지 (유효숫자 2자리 이하: 3100만, 9.3조, 30만 등)"""
    if v <= 0:
        return False
    import math
    exp = math.floor(math.log10(v))
    scaled = v / (10 ** (exp - 1))
    return abs(scaled - round(scaled)) < 1e-9


def _matches(val, refs, rel=0.006, abs_tol=0.02):
    for r in refs:
        if r == 0:
            if abs(val) <= abs_tol:
                return True
            continue
        if abs(val - r) <= max(abs_tol, r * rel):
            return True
        # 방송식 반올림 허용: 대본 수치가 깔끔한 반올림 수이고 원본과 7% 이내면 통과
        # (예: 31,658,279주 → "삼천백만 주" / 9조3,294억 → "구조 삼천억")
        # 자릿수 자체가 틀린 오류(9.3조→93조)는 여전히 검출됨
        if _is_round_number(val) and abs(val - r) <= r * 0.07:
            return True
    return False


def verify_script_numbers(script_text, raw_data, max_warn=10):
    """대본 속 숫자(한글 독음 포함)를 수집 데이터와 대조. 확인 안 되는 수치 목록 반환.
    CTA·자료안내 블록은 검사에서 제외한다."""
    refs = _collect_reference_values(raw_data)
    warnings = []

    blocks = [b for b in str(script_text or "").split("---<")]
    for block in blocks:
        if any(h in block for h in _CTA_HINTS):
            continue  # CTA/자료안내 블록 제외
        # 한글 독음 + 단위
        pattern = (r"((?:[영일이삼사오육칠팔구십백천만억조]|쩜|\s){1,30}?)"
                   r"(퍼센트포인트|퍼센트|프로|배|원|주)")
        for m in re.finditer(pattern, block):
            token, unit = m.group(1).strip(), m.group(2)
            if not any(c in _KDIGIT or c in _KSMALL or c in _KBIG for c in token):
                continue
            if _is_too_short_number_token(token, unit):
                continue
            val = kor_to_num(token)
            if val is None or val == 0:
                continue
            if unit in ("퍼센트", "퍼센트포인트", "프로"):
                ok = _matches(val, refs["pct"])
                shown = f"{val:g}%"
            elif unit == "배":
                ok = _matches(val, refs["x"])
                shown = f"{val:g}배"
            elif unit == "주":
                ok = _matches(val, refs["shares"], rel=0.01)
                shown = f"{val:,.0f}주"
            else:  # 원
                ok = (_matches(val, refs["krw"], rel=0.006)
                      or _matches(val, refs["amt"], rel=0.006)
                      or any(_matches(v, refs["amt"], rel=0.01) for v in _amount_variants_krw(val)))
                shown = f"{val:,.0f}원"
            if not ok:
                snippet = (token + " " + unit).strip()
                warnings.append(f"'{snippet}' ≈ {shown} — 수집 데이터에서 확인 안 됨")
            if len(warnings) >= max_warn:
                return warnings
    return warnings


def _remove_blocks_with_unverified_numbers(text, raw_data):
    """원자료로 확인되지 않는 수치가 '2건 이상' 몰린 본문 블록만 삭제한다.

    - 코드가 새 수치나 대체 문장을 만들지 않도록 재계산·치환은 하지 않는다.
    - CTA 블록은 기존 검증 정책대로 제외한다.
    - 경고 1건짜리 블록은 살려두고 최종 검증 경고(팝업)로 사람에게 넘긴다.
      (반올림·파생 표현일 가능성이 커서, 통삭제는 오탐 비용이 더 크기 때문)
    """
    kept, removed = [], 0
    for block in str(text or "").split("---<"):
        b = block.strip()
        if not b:
            continue
        warns = verify_script_numbers(b, raw_data, max_warn=2)
        if len(warns) >= 2:
            removed += 1
            continue
        kept.append(b)
    if removed:
        print(f"[숫자검증] 미확인 수치 2건 이상 블록 {removed}개 삭제됨")
    return _format_separator_spacing("\n\n---<\n\n".join(kept))



_PHASE_VIOLATION_PATTERNS = [
    r"종가",
    r"오늘\s*(?:정규장\s*)?종가",
    r"오늘\s*장\w*\s*(?:을\s*)?마쳤",
    r"오늘\s*.*마감(?:했|됐|되었|했습니다|됐습니다)",
    r"장\s*마감(?!\s*뒤\s*확인)",
    r"마감\s*기준",
    r"마감했습니다",
    r"마감됐습니다",
    r"마감되었습니다",
    r"장을\s*마쳤",
    r"오늘\s*(?:밤|저녁)[^\n]{0,30}(?:확정|발표|나옵)",
    r"오늘\s*장[^\n]{0,12}(?:모두\s*)?(?:끝나고|끝난\s*뒤|끝난\s*후)",
    r"오늘(?:도)?[^\n]{0,30}(?:서킷브레이커|서킷\s*브레이커|사이드카)[^\n]{0,30}(?:걸렸|발동|터졌)",
    r"(?:서킷브레이커|서킷\s*브레이커|사이드카)[^\n]{0,30}오늘(?:도)?[^\n]{0,30}(?:걸렸|발동|터졌)",
    r"오늘(?:도)?[^\n]{0,20}(?:거래가|매매가|거래를|매매를)[^\n]{0,20}(?:멈췄|중단)",
    r"(?:방금\s*전|방금|조금\s*전)[^\n]{0,30}(?:실적|영업이익|매출|공시|뉴스|발표|서킷브레이커|서킷\s*브레이커|사이드카)",
    r"(?:실적|영업이익|매출|공시|뉴스)[^\n]{0,30}(?:방금\s*전|방금|조금\s*전)",
]


# 과거·해외 시장 참조는 장전/장중에도 정당한 표현 — 위반 검사 전에 가려둔다
_PHASE_EXEMPT_PATTERNS = [
    r"(어제|전일|전날|지난주|지난\s*금요일|금요일|직전\s*거래일)\s*(정규장\s*)?종가",
    r"(어제|전일|전날)\s*[^\n]{0,10}(마감|장을\s*마쳤)",
    r"(어제|전일|전날|직전\s*거래일)[^\n]{0,30}(서킷브레이커|서킷\s*브레이커|사이드카|거래\s*중단|매매\s*중단)",
    r"(미국|나스닥|뉴욕|간밤|월가|필라델피아)[^\n]{0,14}(마감|종가)",
    r"52주",  # '52주 최고/최저'의 '주'와 무관하지만 종가 인접 오탐 방지용 컨텍스트 보호
]


def _mask_phase_exempt(text):
    masked = str(text or "")
    for pat in _PHASE_EXEMPT_PATTERNS:
        masked = re.sub(pat, lambda m: "◇" * len(m.group(0)), masked)
    return masked


def _find_market_phase_violations(text, raw_data, format_name=None):
    """장전/장중 대본에 '오늘 확정' 화법이 섞였는지 찾는다.
    (어제/금요일/미국 종가 등 과거·해외 참조는 정당하므로 제외)"""
    phase = _extract_market_phase(raw_data)
    if phase not in ("PREOPEN", "INTRADAY"):
        return []
    scan_target = _mask_phase_exempt(text)
    found = []
    for pat in _PHASE_VIOLATION_PATTERNS:
        for m in re.finditer(pat, scan_target):
            snippet = str(text or "")[max(0, m.start()-20):m.end()+20].replace("\n", " ")
            if "◇" in scan_target[m.start():m.end()]:
                continue  # 예외 구간과 겹치면 통과
            found.append(snippet.strip())
            if len(found) >= 8:
                return found
    return found


def _remove_market_phase_violation_blocks(text, raw_data, format_name=None):
    """재작성 뒤에도 남은 장전·장중 금지 표현은 해당 블록만 삭제한다."""
    if not _find_market_phase_violations(text, raw_data, format_name=format_name):
        return text
    kept = []
    for block in str(text or "").split("---<"):
        b = block.strip()
        if not b:
            continue
        scan_block = _mask_phase_exempt(b)
        if any(re.search(pat, scan_block) for pat in _PHASE_VIOLATION_PATTERNS):
            continue
        kept.append(b)
    return _format_separator_spacing("\n\n---<\n\n".join(kept))


def _rewrite_market_phase_violation_with_openai(client, types, text, raw_data, model, format_name=None, temperature=0.55):
    violations = _find_market_phase_violations(text, raw_data, format_name=format_name)
    if not violations:
        return text
    phase_rules = _market_phase_prompt_rules(raw_data, format_name)
    prompt = f"""
아래 [현재 대본]은 시장 단계 표현을 위반했다.
내용과 수치는 유지하되, 시제와 시장 단계 표현만 바로잡아 완성 대본으로 다시 출력하라.

## 반드시 고칠 점
- 이 대본은 장마감 브리핑이 아니다.
- 장전/장중 대본에서 '종가', '오늘 종가', '오늘 마감', '장마감', '마감했습니다', '장을 마쳤습니다', '마감 기준' 같은 표현을 절대 쓰지 마라.
- KRX 일봉의 마지막 값은 '직전 거래일 기준' 또는 '최근 확정 거래일 기준'으로만 말하라.
- 현재 가격은 '자료 수집 시점에 확인된 가격', '장중 확인 가격', '지금 확인되는 가격'처럼 자연스럽게 말하라.
- '이 시각 기준 현재가', '이 시각 기준 가격'처럼 어색한 표현은 쓰지 마라.
- 수급·외국인 지분율·공매도는 '최근 확정 데이터 기준'으로만 말하라. 오늘 수급은 '장 마감 뒤 확인해야 합니다'라고만 말하라.
- 새 숫자, 환산 금액, 새로운 뉴스는 추가하지 마라.
- 완성 대본만 출력하라. 제목, 설명, 수정 내역을 쓰지 마라.

{phase_rules}

[위반 표현 예시]
{chr(10).join('- ' + v for v in violations)}

[현재 대본]
{text}

[수집 데이터]
{raw_data}
""".strip()
    rewritten = _generate_text_with_openai(
        client=client,
        types=types,
        model=model,
        prompt=prompt,
        temperature=max(0.35, min(float(temperature or 0.55), 0.65)),
    )
    rewritten = clean_generated_script(rewritten)
    if rewritten and _memo_char_count(rewritten) > 3000:
        return _remove_market_phase_violation_blocks(
            rewritten, raw_data, format_name=format_name
        )
    return _remove_market_phase_violation_blocks(
        text, raw_data, format_name=format_name
    )


def generate_ai_script(stock_name="삼성전자", stock_code="005930", format_name=None,
                       raw_data=None, model=None, temperature=0.7,
                       output_dir=None, save=True, angle=None, custom_topic=None,
                       engine="chain"):
    """
    데이터 수집 → 프롬프트 생성 → AI 호출 → TXT 저장까지 한 번에 실행한다.

    반환값:
        {"text": 완성대본, "path": 저장경로 또는 None, "prompt": 최종프롬프트, "raw_data": 수집데이터}
    """
    prompt, raw_data = build_script_prompt(
        stock_name=stock_name,
        stock_code=stock_code,
        raw_data=raw_data,
        format_name=format_name,
        angle=angle,
        custom_topic=custom_topic,
    )
    format_name = _effective_script_format(format_name, raw_data=raw_data)

    engine = _normalize_ai_engine(engine)
    initial_model = model or (GEMINI_TEXT_MODEL if engine in ("gemini", "fast_gemini") else OPENAI_TEXT_MODEL)
    if engine == "chain":
        result_text = _generate_openai_gemini_chain_script(
            prompt=prompt,
            raw_data=raw_data,
            format_name=format_name,
            model=model or OPENAI_TEXT_MODEL,
            temperature=temperature,
        )
        initial_model = f"{OPENAI_TEXT_MODEL}→{GEMINI_TEXT_MODEL}→{OPENAI_TEXT_MODEL}→{GEMINI_TEXT_MODEL}"
    else:
        result_text = _generate_initial_script_text(
            prompt=prompt,
            engine=engine,
            model=initial_model,
            temperature=temperature,
        )
    if not result_text.strip():
        raise RuntimeError(f"{engine} 응답이 비어 있습니다. API 상태나 프롬프트 길이를 확인하세요.")

    if engine in ("fast_openai", "fast_gemini"):
        # 빠른 모드: 추가 재작성/90점 보정 루프를 생략하고 기본 정리만 한다.
        # 초안 확인이나 여러 종목 빠른 제작용이며, 최종 90점 보장은 chain 모드가 담당한다.
        result_text = clean_generated_script(result_text)
        result_text = _remove_blocks_with_unverified_numbers(result_text, raw_data)
        result_text = _ensure_post_cta_closing(result_text, format_name=format_name)
        result_text = _restore_missing_separators(result_text)
        result_text = _rebalance_separator_rhythm(result_text)
        result_text = clean_generated_script(result_text)
        final_hard_min = WEEKEND_HARD_MIN_SCRIPT_CHARS if ("주말용 사전작성 자료 수집" in str(raw_data or "") or _format_is_weekend(format_name)) else HARD_MIN_SCRIPT_CHARS
        stats = get_script_quality_stats(result_text, format_name=format_name)
        stats["length_warnings"] = [] if _memo_char_count(result_text) >= final_hard_min else [f"빠른 모드라 대본이 짧을 수 있습니다. 현재 약 {_memo_char_count(result_text):,}자 / 권장 최소 약 {final_hard_min:,}자입니다."]
        stats["engine"] = engine
        stats["speed_mode"] = "fast"
        stats["initial_model"] = initial_model
        stats["number_warnings"] = []
        try:
            stats["market_phase_warnings"] = _find_market_phase_violations(result_text, raw_data, format_name=format_name)
        except Exception:
            stats["market_phase_warnings"] = []
        saved_path = save_text_file(result_text, stock_name, output_dir=output_dir) if save else None
        return {
            "text": result_text,
            "path": saved_path,
            "prompt": prompt,
            "raw_data": raw_data,
            "stats": stats,
        }
    if engine == "chain":
        # 체인 생성은 이미 양쪽 모델 검토를 거쳤으므로 기존 OpenAI 재작성 안전망에 다시 태우지 않는다.
        result_text = _remove_blocks_with_unverified_numbers(result_text, raw_data)
        result_text = _ensure_post_cta_closing(result_text, format_name=format_name)
        result_text = _restore_missing_separators(result_text)
        result_text = _rebalance_separator_rhythm(result_text)
        result_text = clean_generated_script(result_text)
        # 저장 직전 품질이 90점 미만이면 숫자검증/정리 이후 깨진 흐름을 다시 보완한다.
        for final_round in range(2):
            pre_stats = get_script_quality_stats(result_text, format_name=format_name)
            if int(pre_stats.get("quality_score") or 0) >= 90 and _memo_char_count(result_text) >= (WEEKEND_HARD_MIN_SCRIPT_CHARS if ("주말용 사전작성 자료 수집" in str(raw_data or "") or _format_is_weekend(format_name)) else HARD_MIN_SCRIPT_CHARS):
                break
            repair_reasons = list(pre_stats.get("quality_reasons") or [])
            for key in ("spoken_consistency_warnings", "repetition_artifact_warnings", "cta_flow_warnings", "format_identity_warnings", "jungpro_warnings"):
                repair_reasons.extend(pre_stats.get(key) or [])
            repair_prompt = _build_chain_refine_prompt(result_text, raw_data, format_name=format_name, stage="gemini_final")
            repair_prompt += f"\n\n## 저장 직전 품질 점수: {int(pre_stats.get('quality_score') or 0)}점"
            repair_prompt += "\n## 90점 이상으로 올리기 위해 반드시 해결할 문제\n"
            repair_prompt += "\n".join(f"- {x}" for x in repair_reasons[:14])
            repair_prompt += "\n\n완성 대본만 출력하라. 최종 분량은 CTA 포함 최소 9,000자, 권장 10,000~12,000자다. 본문 분석을 6~10블록 보강하고, 전체 구분자는 34~50개로 맞춰라. CTA 직전에는 계좌 비중, 손실, 종목 점검 순서로 자연스럽게 연결하고, CTA 시작은 반드시 새 블록으로 분리하라. CTA 본문은 그대로 유지하라."
            repaired = _generate_text_with_gemini(
                model=GEMINI_TEXT_MODEL,
                prompt=repair_prompt,
                temperature=max(0.25, min(float(temperature or 0.45), 0.55)),
            )
            repaired = clean_generated_script(repaired)
            repaired = _remove_blocks_with_unverified_numbers(repaired, raw_data)
            repaired = _ensure_post_cta_closing(repaired, format_name=format_name)
            repaired = _restore_missing_separators(repaired)
            repaired = _rebalance_separator_rhythm(repaired)
            repaired = clean_generated_script(repaired)
            if _memo_char_count(repaired) < max(3500, int(_memo_char_count(result_text) * 0.75)):
                break
            old_score = int(pre_stats.get("quality_score") or 0)
            new_stats = get_script_quality_stats(repaired, format_name=format_name)
            new_score = int(new_stats.get("quality_score") or 0)
            if new_score >= old_score or _memo_char_count(repaired) > _memo_char_count(result_text) + 800:
                result_text = repaired
            else:
                break
        final_hard_min = WEEKEND_HARD_MIN_SCRIPT_CHARS if ("주말용 사전작성 자료 수집" in str(raw_data or "") or _format_is_weekend(format_name)) else HARD_MIN_SCRIPT_CHARS
        stats = get_script_quality_stats(result_text, format_name=format_name)
        stats["length_warnings"] = [] if _memo_char_count(result_text) >= final_hard_min else [f"대본이 기준보다 짧습니다. 현재 약 {_memo_char_count(result_text):,}자 / 권장 최소 약 {final_hard_min:,}자입니다. 저장은 완료했습니다."]
        stats["engine"] = engine
        stats["initial_model"] = initial_model
        stats["number_warnings"] = []
        try:
            stats["market_phase_warnings"] = _find_market_phase_violations(result_text, raw_data, format_name=format_name)
        except Exception:
            stats["market_phase_warnings"] = []
        saved_path = save_text_file(result_text, stock_name, output_dir=output_dir) if save else None
        return {
            "text": result_text,
            "path": saved_path,
            "prompt": prompt,
            "raw_data": raw_data,
            "stats": stats,
        }

    # 확장·시장단계 교정·말투 재작성은 기존 OpenAI 안전망을 재사용한다.
    # Gemini 초안도 같은 검증 파이프라인을 태워 포맷과 말투를 맞춘다.
    client = _make_openai_client()
    post_model = OPENAI_TEXT_MODEL
    result_text = _ensure_script_length_with_openai(
        client=client,
        types=None,
        text=result_text,
        raw_data=raw_data,
        model=post_model,
        temperature=temperature,
        format_name=format_name,
    )
    result_text = _rewrite_market_phase_violation_with_openai(
        client=client,
        types=None,
        text=result_text,
        raw_data=raw_data,
        model=post_model,
        format_name=format_name,
        temperature=temperature,
    )
    result_text = _remove_blocks_with_unverified_numbers(result_text, raw_data)
    result_text = _ensure_post_cta_closing(result_text, format_name=format_name)
    result_text = _restore_missing_separators(result_text)
    result_text = _rebalance_separator_rhythm(result_text)
    result_text = _ensure_spoken_tone_with_openai(
        client=client,
        types=None,
        text=result_text,
        raw_data=raw_data,
        model=post_model,
        temperature=temperature,
        format_name=format_name,
    )
    result_text = _rewrite_market_phase_violation_with_openai(
        client=client,
        types=None,
        text=result_text,
        raw_data=raw_data,
        model=post_model,
        format_name=format_name,
        temperature=temperature,
    )
    result_text = _remove_blocks_with_unverified_numbers(result_text, raw_data)
    result_text = _ensure_post_cta_closing(result_text, format_name=format_name)
    result_text = _restore_missing_separators(result_text)
    result_text = _rebalance_separator_rhythm(result_text)
    result_text = _final_length_guard_with_openai(
        client=client,
        types=None,
        text=result_text,
        raw_data=raw_data,
        model=post_model,
        temperature=temperature,
        format_name=format_name,
    )
    final_hard_min = WEEKEND_HARD_MIN_SCRIPT_CHARS if ("주말용 사전작성 자료 수집" in str(raw_data or "") or _format_is_weekend(format_name)) else HARD_MIN_SCRIPT_CHARS
    stats = get_script_quality_stats(result_text, format_name=format_name)
    if _memo_char_count(result_text) < final_hard_min:
        stats["length_warnings"] = [
            f"대본이 기준보다 짧습니다. 현재 약 {_memo_char_count(result_text):,}자 / 권장 최소 약 {final_hard_min:,}자입니다. 저장은 완료했습니다."
        ]
    else:
        stats["length_warnings"] = []
    stats["engine"] = engine
    stats["initial_model"] = initial_model
    # 숫자 검증은 위의 블록 삭제 단계에서만 조용히 사용한다.
    # 사용자에게 미확인 수치 경고를 띄우지 않도록 결과 통계에는 싣지 않는다.
    stats["number_warnings"] = []
    try:
        stats["market_phase_warnings"] = _find_market_phase_violations(result_text, raw_data, format_name=format_name)
    except Exception:
        stats["market_phase_warnings"] = []

    saved_path = save_text_file(result_text, stock_name, output_dir=output_dir) if save else None
    return {
        "text": result_text,
        "path": saved_path,
        "prompt": prompt,
        "raw_data": raw_data,
        "stats": stats,
    }


THUMBNAIL_COPY_PROMPT = """당신은 한국 주식 유튜브 채널의 썸네일 카피라이터다.
아래 [완성 대본]과 [수집 데이터]를 읽고, 영상 내용과 정확히 맞는 썸네일 문구 조합 8개를 작성하라.

## 절대 규칙
1. 대본과 수집 데이터에 없는 숫자·사실·뉴스·목표가를 만들지 마라.
2. 현재가, 장중, 장전, 최근 확정 거래일 등 대본의 시장 단계를 바꾸지 마라.
3. CTA의 무료 자료, 링크, 구독, 알림센터는 썸네일 소재로 사용하지 마라.
4. 폭포수, 총성 없는 전쟁, 방패, 창, 혈투, 매도 폭탄, 역사적인 같은 재난·대결 비유를 쓰지 마라.
5. 상승·하락을 확정 예언하거나 매수·매도를 지시하지 마라.
6. 대본의 핵심 질문과 가장 강한 확인 가능 숫자를 우선 사용하라.
7. 같은 표현을 바꿔 쓴 후보를 반복하지 마라.
8. 이미지 썸네일에 바로 얹을 문구다. 설명문처럼 길게 쓰면 실패다.
9. 큰 문구는 2~5어절 이내, 가능하면 7~11자 안팎으로 강하게 압축하라.
10. 보조 문구는 큰 문구를 설명하지 말고 궁금증을 한 번 더 찌르는 짧은 말로 써라.
11. "진짜 이유", "사상 최대 실적에", "돈이 빠진"처럼 흔한 설명형 조합만 반복하지 마라.
12. 좋은 방향: "좋은데 왜 빠져?", "외국인 왜 팔까", "실적보다 큰 변수", "반등 믿어도 되나".

## 출력 형식
[후보1]
큰 문구: 7~11자 권장, 최대 14자
보조 문구: 6~14자 권장, 최대 18자
강조 숫자: 대본에 실제로 있는 숫자 1개 또는 없음
근거: 이 문구가 대본의 어느 핵심 흐름을 잡았는지 한 문장

후보2부터 후보8까지 같은 형식으로 작성하라.
제목·해설·마크다운 코드블록은 추가하지 마라.

[종목]
{stock_name}

[완성 대본]
{script_text}

[수집 데이터]
{raw_data}
"""


def generate_thumbnail_copy(stock_name, script_text, raw_data=None,
                            model=None, temperature=0.75,
                            output_dir=None, save=True):
    """완성 대본에 맞는 썸네일 문구 후보를 OpenAI로 생성한다."""
    script_text = str(script_text or "").strip()
    if not script_text:
        raise ValueError("완성 대본이 비어 있습니다.")

    prompt = THUMBNAIL_COPY_PROMPT.format(
        stock_name=(stock_name or "해당 종목"),
        script_text=script_text,
        raw_data=(raw_data or "별도 수집 데이터 없음 — 완성 대본에 있는 사실만 사용"),
    )
    client = _make_openai_client()
    model = model or OPENAI_TEXT_MODEL
    result_text = _generate_text_with_openai(
        client=client,
        types=None,
        model=model,
        prompt=prompt,
        temperature=max(0.45, min(float(temperature or 0.75), 0.9)),
    ).strip()
    if not result_text:
        raise RuntimeError("OpenAI 썸네일 문구 응답이 비어 있습니다.")

    result_text = re.sub(r"^```(?:text)?\s*", "", result_text, flags=re.I)
    result_text = re.sub(r"\s*```$", "", result_text).strip()
    saved_path = (
        save_text_file(
            result_text,
            stock_name or "썸네일",
            output_dir=output_dir,
            prefix="썸네일문구",
        )
        if save else None
    )
    return {"text": result_text, "path": saved_path, "prompt": prompt}


def _extract_thumbnail_image_text(thumbnail_copy):
    """썸네일 문구 후보 텍스트에서 첫 후보의 큰 문구/보조 문구/강조 숫자를 뽑는다."""
    text = str(thumbnail_copy or "").strip()
    if not text:
        return {"main": "오늘의 핵심", "sub": "숫자로 확인하세요", "badge": ""}

    first = text
    m = re.search(r"\[?\s*후보\s*1\s*\]?(.*?)(?:\n\s*\[?\s*후보\s*2\s*\]?|$)", text, flags=re.S | re.I)
    if m:
        first = m.group(1).strip()

    def pick(label):
        pat = rf"{label}\s*[:：]\s*(.+)"
        mm = re.search(pat, first)
        if not mm:
            return ""
        value = mm.group(1).strip()
        value = re.sub(r"\s*\(.+?\)\s*$", "", value).strip()
        return value.strip(" \"'“”‘’")

    main = pick("큰 문구") or pick("메인 문구")
    sub = pick("보조 문구") or pick("서브 문구")
    badge = pick("강조 숫자")
    if badge in {"없음", "없다", "-", "없습니다"}:
        badge = ""

    if not main:
        lines = [ln.strip() for ln in first.splitlines() if ln.strip()]
        for ln in lines:
            if ":" not in ln and len(ln) <= 28:
                main = ln
                break
    if not main:
        main = "오늘의 핵심"
    if not sub:
        sub = "숫자로 확인하세요"
    main = _compress_thumbnail_phrase(main, max_chars=14)
    sub = _compress_thumbnail_phrase(sub, max_chars=16)
    return {"main": main, "sub": sub, "badge": badge}


def _extract_thumbnail_image_candidates(thumbnail_copy, limit=3):
    """썸네일 문구 후보 여러 개를 이미지 생성용으로 뽑는다."""
    text = str(thumbnail_copy or "").strip()
    if not text:
        return [_extract_thumbnail_image_text(text)]
    chunks = []
    matches = list(re.finditer(r"\[?\s*후보\s*(\d+)\s*\]?", text, flags=re.I))
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            chunks.append(body)
        if len(chunks) >= limit:
            break
    if not chunks:
        chunks = [text]
    out = []
    seen = set()
    for chunk in chunks:
        picked = _extract_thumbnail_image_text("[후보1]\n" + chunk)
        key = (picked.get("main", ""), picked.get("sub", ""), picked.get("badge", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(picked)
        if len(out) >= limit:
            break
    return out or [_extract_thumbnail_image_text(text)]


def _compress_thumbnail_phrase(text, max_chars=14):
    """이미지용 썸네일 문구가 설명문처럼 길어지는 것을 막는다."""
    s = re.sub(r"\s+", " ", str(text or "").strip())
    if not s:
        return s
    s = s.replace("돈이 빠진 진짜 이유", "왜 팔았나?")
    s = s.replace("돈이 빠진 이유", "왜 팔았나?")
    s = s.replace("외국인이 판 진짜 이유", "왜 팔았나?")
    s = s.replace("외국인이 파는 이유", "왜 팔았나?")
    s = re.sub(r"^(사상\s*최대\s*실적에\s*)", "", s).strip()
    s = re.sub(r"(진짜\s*이유|이유)$", "", s).strip()
    s = s.replace("하락했습니다", "하락").replace("급락했습니다", "급락")
    s = s.replace("빠졌습니다", "빠졌다").replace("밀렸습니다", "밀렸다")
    if len(s.replace(" ", "")) <= max_chars:
        return s
    chunks = re.split(r"[\s,·/]+", s)
    kept = []
    cur_len = 0
    for chunk in chunks:
        if not chunk:
            continue
        next_len = cur_len + len(chunk)
        if kept:
            next_len += 1
        if next_len > max_chars:
            break
        kept.append(chunk)
        cur_len = next_len
    if kept:
        return " ".join(kept)
    return s[:max_chars]


def _thumbnail_font(size, bold=False):
    """윈도우 기본 한글 폰트를 우선 사용한다."""
    try:
        from PIL import ImageFont
    except ImportError as e:
        raise RuntimeError("썸네일 이미지 생성을 위해 Pillow가 필요합니다. `pip install pillow`를 실행하세요.") from e

    candidates = []
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        candidates.extend([
            os.path.join(windir, "Fonts", "malgunbd.ttf" if bold else "malgun.ttf"),
            os.path.join(windir, "Fonts", "NanumGothicBold.ttf" if bold else "NanumGothic.ttf"),
        ])
    candidates.extend(["malgunbd.ttf" if bold else "malgun.ttf", "arialbd.ttf" if bold else "arial.ttf"])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_for_thumbnail(draw, text, font, max_width, max_lines=2):
    """Pillow 텍스트 폭 기준으로 썸네일 문구를 줄바꿈한다."""
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if not text:
        return []
    words = text.split(" ")
    if len(words) == 1 and len(text) > 7:
        words = list(text)
    lines, cur = [], ""
    for word in words:
        cand = (cur + (" " if cur and len(word) > 1 else "") + word).strip()
        box = draw.textbbox((0, 0), cand, font=font)
        if box[2] - box[0] <= max_width or not cur:
            cur = cand
            continue
        lines.append(cur)
        cur = word
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return lines


def generate_thumbnail_image(stock_name, thumbnail_copy, output_dir=None,
                             variant="auto", save=True):
    """썸네일 문구 후보를 1280x720 PNG 이미지로 저장한다."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as e:
        raise RuntimeError("썸네일 이미지 생성을 위해 Pillow가 필요합니다. `pip install pillow`를 실행하세요.") from e

    if output_dir is None:
        output_dir = _default_output_dir()
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    picked = _extract_thumbnail_image_text(thumbnail_copy)
    main = picked["main"]
    sub = picked["sub"]
    badge = picked["badge"]
    stock = str(stock_name or "오늘 종목").strip()

    danger_words = "하락 급락 매도 이탈 경고 불안 충격 리스크"
    is_danger = any(w in (main + " " + sub) for w in danger_words)
    if variant == "blue":
        is_danger = False
    if variant == "red":
        is_danger = True

    bg = (245, 248, 252)
    text_color = (18, 24, 38)
    muted = (94, 105, 122)
    accent = (238, 76, 76) if is_danger else (54, 116, 255)
    accent_dark = (187, 38, 38) if is_danger else (25, 82, 210)
    soft = (255, 232, 232) if is_danger else (229, 238, 255)

    img = Image.new("RGB", (1280, 720), bg)
    draw = ImageDraw.Draw(img)

    # 배경 카드와 포인트 그래픽
    draw.rounded_rectangle((54, 54, 1226, 666), radius=42, fill=(255, 255, 255))
    draw.rounded_rectangle((54, 54, 1226, 666), radius=42, outline=(226, 233, 242), width=2)
    draw.ellipse((850, -120, 1380, 410), fill=soft)
    draw.ellipse((910, 35, 1320, 445), outline=accent, width=18)
    draw.rounded_rectangle((78, 82, 365, 146), radius=25, fill=accent)

    font_badge = _thumbnail_font(34, bold=True)
    font_stock = _thumbnail_font(38, bold=True)
    font_main = _thumbnail_font(92, bold=True)
    font_sub = _thumbnail_font(42, bold=True)
    font_num = _thumbnail_font(56, bold=True)
    font_small = _thumbnail_font(28, bold=True)

    draw.text((104, 94), stock[:18], fill=(255, 255, 255), font=font_badge)

    main_lines = _wrap_for_thumbnail(draw, main, font_main, 760, max_lines=2)
    if len("".join(main_lines)) > 18:
        font_main = _thumbnail_font(82, bold=True)
        main_lines = _wrap_for_thumbnail(draw, main, font_main, 800, max_lines=2)

    y = 205
    for line in main_lines:
        draw.text((88, y), line, fill=text_color, font=font_main)
        y += 100

    draw.rounded_rectangle((88, 450, 830, 526), radius=22, fill=(244, 247, 251))
    sub_lines = _wrap_for_thumbnail(draw, sub, font_sub, 690, max_lines=1)
    draw.text((118, 464), (sub_lines[0] if sub_lines else sub)[:28], fill=muted, font=font_sub)

    if badge:
        draw.rounded_rectangle((875, 448, 1176, 548), radius=28, fill=accent)
        badge_text = badge[:12]
        box = draw.textbbox((0, 0), badge_text, font=font_num)
        draw.text((875 + (301 - (box[2] - box[0])) / 2, 466),
                  badge_text, fill=(255, 255, 255), font=font_num)
    else:
        draw.rounded_rectangle((884, 450, 1176, 540), radius=28, fill=accent)
        draw.text((930, 474), "핵심 체크", fill=(255, 255, 255), font=font_small)

    draw.rectangle((88, 590, 1190, 602), fill=accent_dark)
    draw.text((90, 618), "자료 기반으로 차분하게 확인", fill=muted, font=font_small)

    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d_%H%M')}_썸네일이미지_{_safe_filename_part(stock)}.png"
    path = os.path.join(output_dir, filename)
    if save:
        img.save(path, "PNG", optimize=True)
    return {"path": path, "main": main, "sub": sub, "badge": badge}


THUMBNAIL_IMAGE_PROMPT_TEMPLATE = """Create a high-click Korean YouTube stock thumbnail image.

Canvas:
- 16:9 horizontal YouTube thumbnail, 1280x720 style.
- Bold, clean, premium Korean finance channel look, but optimized for click-through rate.
- Strong contrast, readable at small mobile size.
- No fake company logos, no real person's face, no watermark, no QR code.
- Do not use a real Samsung logo. Plain Korean stock label text is okay.

Base visual style:
- Background must be matte deep black, not blue corporate, not report-like white.
- Minimal and premium mood. The background should feel expensive, quiet, and dark.
- Do not add logos, app icons, random symbols, busy patterns, decorative wallpaper, or extra background text.
- Background detail is allowed only as very subtle shadow, faint chart silhouette, or soft gradient.
- The background must not compete with the headline.

Important text to render exactly in Korean:
- Stock label: "{stock_name}"
- Main headline: "{main_text}"
- Sub headline: "{sub_text}"
- Emphasis number: "{badge_text}"

Design profile for this version:
{design_profile}

CTR composition rules:
- The thumbnail must have only 3 text zones: stock label, main headline, tiny sub headline or badge.
- Main headline must be huge and take about 55% of the visual attention.
- Total Korean text on the image must be very short. Do not render long explanatory sentences.
- If sub headline is longer than 14 Korean characters, visually shorten it or make it a small badge.
- Do not create extra text such as "진짜 이유", "사상 최대 실적에", "돈이 빠진" unless it is exactly provided above.
- Avoid generic red down arrows dominating the image. A small tension cue is okay, but text curiosity is the hero.
- Avoid cluttered charts, too many candles, and report-cover style layouts.
- The image should make a viewer ask "why?" within one second.

Typography / effects:
- Use extremely bold Korean display typography.
- Add thick black or dark outline around white/yellow/red letters.
- Add strong drop shadow and subtle glow so text pops from the background.
- Use one highlighted word or number in yellow or red, with the rest in white.
- Use a short red/yellow sticker badge or diagonal label for the emphasis number.
- Keep safe margins. Text must not touch the edge.
- The main headline must be readable when the image is shown at phone size.
- Text style should feel flashy like a high-CTR YouTube/game UI: vivid color, strong gradient, emboss or 3D depth, shine, glow, thick outline, red point text.
- But the overall layout must remain premium and minimal because the background is matte deep black.
- Do not place all text in a centered lump. Place the main text large inside the left safe margin area.
- Use asymmetric composition: text-heavy left side, simple visual tension on the right side.

Design direction:
- Premium Korean finance YouTube style, closer to a sharp editorial thumbnail than a noisy stock ad.
- Matte deep black background with one strong accent color.
- Use large Korean typography. The main headline must be the biggest element by far.
- Keep text short and legible. Do not add extra Korean sentences that are not listed above.
- Do not add investment advice such as buy, sell, target price, guaranteed profit.
- Do not invent numbers, dates, prices, news, or logos.
- Make it look clickable, tense, and premium, not like a report slide.
- It should feel like a thumbnail that competes well on Korean finance YouTube, not a corporate presentation.

Thumbnail copy source:
{thumbnail_copy}
"""


THUMBNAIL_DESIGN_PROFILES = [
    {
        "name": "contrast_hook",
        "label": "강한대비",
        "prompt": "- Matte deep black background, huge white headline with black outline and yellow number.\n- Use one vivid red point text or badge, with glossy/embossed type effect.\n- Composition: left 65% text inside safe margin, right 35% abstract market shock graphic.\n- Mood: urgent, premium, clean, high CTR.",
    },
    {
        "name": "number_impact",
        "label": "숫자충격",
        "prompt": "- Matte deep black background. The number badge is the visual hero: oversized yellow/gold number with red contrast tag.\n- Background has only a very subtle blurred candlestick/chart silhouette, not cluttered.\n- Use thick outline, shadow, glow, and slight 3D/emboss effect on text.\n- Mood: surprising but not cheap.",
    },
    {
        "name": "question_mystery",
        "label": "궁금증",
        "prompt": "- Matte deep black background. Build curiosity around the question with a big red/yellow question mark and one highlighted phrase.\n- Use spotlight only on the left headline. Keep right side simple and tense.\n- Minimal chart elements, no busy interface screenshots, no logos or random icons.\n- Mood: mysterious, sharp, clickable.",
    },
]


def _openai_image_model_candidates(preferred=None):
    configured = (
        preferred
        or _cfg.get("THUMBNAIL_IMAGE_MODEL")
        or _cfg.get("OPENAI_IMAGE_MODEL")
        or os.environ.get("THUMBNAIL_IMAGE_MODEL")
        or os.environ.get("OPENAI_IMAGE_MODEL")
        or ""
    )
    candidates = [
        configured.strip(),
        "gpt-image-1.5",
        "gpt-image-1",
        "gpt-image-1-mini",
    ]
    out = []
    for item in candidates:
        if item and item not in out:
            out.append(item)
    return out


def _save_openai_image_response(response, output_dir, stock_name, model_name, suffix=None):
    """OpenAI 이미지 응답의 base64 이미지를 16:9 유튜브 썸네일 파일로 저장한다."""
    data_items = getattr(response, "data", None) or []
    if not data_items:
        raise RuntimeError("OpenAI 응답에서 이미지 데이터를 찾지 못했습니다.")
    b64 = getattr(data_items[0], "b64_json", None)
    if not b64:
        raise RuntimeError("OpenAI 응답에 base64 이미지가 없습니다.")
    raw = base64.b64decode(b64)

    now = datetime.now()
    suffix_part = f"_{_safe_filename_part(suffix)}" if suffix else ""
    filename = (
        f"{now.strftime('%Y-%m-%d_%H%M')}_AI썸네일_"
        f"{_safe_filename_part(stock_name)}{suffix_part}_{_safe_filename_part(model_name)}.png"
    )
    path = os.path.join(output_dir, filename)
    with open(path, "wb") as f:
        f.write(raw)

    # OpenAI 이미지 API의 가로형 규격을 유튜브 16:9 최종 파일로 맞춘다.
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        w, h = img.size
        target_ratio = 16 / 9
        if w / h > target_ratio:
            new_w = int(h * target_ratio)
            left = max(0, (w - new_w) // 2)
            img = img.crop((left, 0, left + new_w, h))
        elif w / h < target_ratio:
            new_h = int(w / target_ratio)
            top = max(0, (h - new_h) // 2)
            img = img.crop((0, top, w, top + new_h))
        img = img.resize((1280, 720), Image.LANCZOS)
        img.save(path, "PNG", optimize=True)
    except Exception:
        pass
    return path


def generate_thumbnail_image_ai(stock_name, thumbnail_copy, raw_data=None,
                                output_dir=None, model=None, save=True,
                                candidate=None, design_profile=None, suffix=None):
    """OpenAI 이미지 모델로 썸네일 PNG를 생성한다."""
    if output_dir is None:
        output_dir = _default_output_dir()
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    picked = candidate or _extract_thumbnail_image_text(thumbnail_copy)
    profile = design_profile or THUMBNAIL_DESIGN_PROFILES[0]
    prompt = THUMBNAIL_IMAGE_PROMPT_TEMPLATE.format(
        stock_name=(stock_name or "오늘 종목"),
        main_text=picked["main"],
        sub_text=picked["sub"],
        badge_text=(picked["badge"] or "없음"),
        design_profile=profile["prompt"] if isinstance(profile, dict) else str(profile),
        thumbnail_copy=str(thumbnail_copy or "").strip(),
    )
    if raw_data:
        prompt += "\n\nUse only this market context for mood. Do not copy long data into the image:\n"
        prompt += str(raw_data)[:2500]

    client = _make_openai_client()
    errors = []
    for model_name in _openai_image_model_candidates(model):
        try:
            response = client.images.generate(
                model=model_name,
                prompt=prompt,
                size="1536x1024",
                n=1,
            )
            path = _save_openai_image_response(
                response,
                output_dir,
                stock_name,
                model_name,
                suffix=suffix or (profile.get("label") if isinstance(profile, dict) else None),
            )
            return {
                "path": path,
                "model": model_name,
                "prompt": prompt,
                "main": picked["main"],
                "sub": picked["sub"],
                "badge": picked["badge"],
                "style": profile.get("label", "") if isinstance(profile, dict) else "",
            }
        except Exception as e:
            errors.append(f"{model_name}: {e}")
            msg = str(e).lower()
            if "404" not in msg and "not_found" not in msg and "not found" not in msg:
                break

    raise RuntimeError("OpenAI 이미지 생성 실패\n" + "\n".join(errors[-3:]))


def generate_thumbnail_images_ai(stock_name, thumbnail_copy, raw_data=None,
                                 output_dir=None, model=None, count=3, save=True):
    """썸네일 문구 후보와 디자인 프로필을 조합해 AI 썸네일 이미지를 여러 장 생성한다."""
    count = max(1, min(int(count or 3), 4))
    candidates = _extract_thumbnail_image_candidates(thumbnail_copy, limit=count)
    results = []
    errors = []
    for idx in range(count):
        candidate = candidates[idx % len(candidates)]
        profile = THUMBNAIL_DESIGN_PROFILES[idx % len(THUMBNAIL_DESIGN_PROFILES)]
        suffix = f"후보{idx + 1}_{profile['label']}"
        try:
            result = generate_thumbnail_image_ai(
                stock_name=stock_name,
                thumbnail_copy=thumbnail_copy,
                raw_data=raw_data,
                output_dir=output_dir,
                model=model,
                save=save,
                candidate=candidate,
                design_profile=profile,
                suffix=suffix,
            )
            result["candidate_no"] = idx + 1
            results.append(result)
        except Exception as e:
            errors.append(f"후보{idx + 1}: {e}")
            if not results:
                continue
    if not results:
        raise RuntimeError("AI 썸네일 이미지 여러 장 생성 실패\n" + "\n".join(errors[-3:]))
    return {
        "paths": [r["path"] for r in results],
        "items": results,
        "errors": errors,
        "path": results[0]["path"],
        "model": results[0].get("model", ""),
        "main": results[0].get("main", ""),
    }



def generate_ai_report(stock_name="삼성전자", stock_code="005930", raw_data=None,
                       model=None, temperature=0.45,
                       output_dir=None, save=True):
    """수집 데이터로 기관식 분석 리포트를 OpenAI로 생성하고 TXT 저장한다."""

    if raw_data is None:
        raw_data = build_raw_data(stock_name, stock_code)
    prompt = REPORT_PROMPT_TEMPLATE.format(raw_data=raw_data)

    client = _make_openai_client()
    model = model or OPENAI_TEXT_MODEL
    result_text = _generate_text_with_openai(
        client=client,
        types=None,
        model=model,
        prompt=prompt,
        temperature=temperature,
    )
    if not result_text.strip():
        raise RuntimeError("OpenAI 응답이 비어 있습니다. API 상태나 프롬프트 길이를 확인하세요.")
    saved_path = save_text_file(result_text, stock_name, output_dir=output_dir, prefix="분석리포트") if save else None
    return {"text": result_text, "path": saved_path, "prompt": prompt, "raw_data": raw_data}

# ──────────────────────────────────────────────
# 한 종목의 수집 데이터에서 '서로 다른 영상 각도' 추출
# 같은 종목을 매일 다뤄도 대본이 매번 다른 이야기가 되게 하는 핵심 기능
# ──────────────────────────────────────────────
ANGLE_PICK_PROMPT = """당신은 유튜브 경제 채널의 방송 기획자다. 아래는 {stock_name} 한 종목의 [수집 데이터]다. 같은 데이터라도 어떤 충돌을 먼저 보여주느냐에 따라 전혀 다른 방송 흐름이 된다. 서로 겹치지 않는 방송 흐름 후보 4개를 뽑아라.

## 각 후보마다 정확히 이 형식으로 작성하라
[흐름1]=한 줄 제목
- 중심 데이터: 이 흐름의 출발점이 될 데이터 섹션 번호와 구체 수치
- 핵심 질문: 시청자가 끝까지 보게 만들 질문 1문장
- 보조 데이터: 중심 데이터를 뒷받침하거나 반대로 긴장감을 주는 데이터 1~2개
- 제외할 데이터: 이번 흐름에서 억지로 넣지 말아야 할 약한 데이터

(흐름2~4도 동일 형식)

## 규칙
- 4개 후보의 중심 데이터는 서로 달라야 한다. 예: ①수급/지분율 ②내부자·큰손 ③공매도·거래량 ④밸류에이션·글로벌/환율
- [수집 데이터]에 없는 수치를 만들지 마라. 데이터가 약한 축은 후보로 뽑지 마라.
- 매수/매도 지시, 목표가, 확률 수치 금지.
- 과장어, 재난 비유, 대결 비유, 포맷명, 제작 과정 설명을 쓰지 마라. 후보 문장은 최종 대본에 들어갈 문장이 아니라 데이터 우선순위여야 한다.

마지막 줄에 오늘 가장 강한 후보 하나를 [추천]=번호 형식으로 적고 이유를 한 문장 붙여라.

[수집 데이터]
{raw_data}
"""


def generate_angles(stock_name, stock_code, raw_data=None,
                    model=None, temperature=0.6, output_dir=None, save=True):
    """수집 데이터에서 서로 다른 영상 각도 4개를 OpenAI로 추출한다."""

    if raw_data is None or not str(raw_data).strip():
        raw_data = build_raw_data(stock_name, stock_code)

    prompt = ANGLE_PICK_PROMPT.format(stock_name=stock_name, raw_data=raw_data)
    client = _make_openai_client()
    model = model or OPENAI_TEXT_MODEL
    result_text = _generate_text_with_openai(
        client=client,
        types=None,
        model=model,
        prompt=prompt,
        temperature=temperature,
    )
    if not result_text.strip():
        raise RuntimeError("OpenAI 응답이 비어 있습니다.")
    saved_path = (save_text_file(result_text, f"{stock_name}_각도후보",
                                 output_dir=output_dir, prefix="각도후보")
                  if save else None)
    return {"text": result_text, "path": saved_path, "raw_data": raw_data}


def extract_angle(angles_text, n):
    """흐름/각도 추출 결과에서 n번 후보 블록을 꺼낸다. 없으면 None."""
    text = str(angles_text or "")
    m = re.search(rf"\[(?:흐름|각도){n}\]\s*[=:].*?(?=\n\s*\[(?:흐름|각도)\d|\n\s*\[추천\]|\Z)",
                  text, re.S)
    return m.group(0).strip() if m else None


def extract_recommended_angle_no(angles_text, default=1):
    """[추천]=N 줄에서 추천 각도 번호를 꺼낸다."""
    m = re.search(r"\[추천\]\s*[=:]\s*(\d)", str(angles_text or ""))
    return int(m.group(1)) if m else default


def generate_topic_ideas(model=None, temperature=0.55, output_dir=None, save=True,
                         scan_text=None):
    """시장 스캐너 결과(또는 전달받은 스캔 텍스트)를 OpenAI에 넘겨 오늘의 소재 후보를 뽑는다."""

    scan = scan_text if (scan_text and scan_text.strip()) else scan_market_candidates()
    prompt = TOPIC_PICK_PROMPT.format(scan=scan)
    client = _make_openai_client()
    model = model or OPENAI_TEXT_MODEL
    result_text = _generate_text_with_openai(
        client=client,
        types=None,
        model=model,
        prompt=prompt,
        temperature=temperature,
    )
    if not result_text.strip():
        raise RuntimeError("OpenAI 응답이 비어 있습니다.")
    saved_path = save_text_file(result_text, "오늘의_소재", output_dir=output_dir, prefix="소재후보") if save else None
    return {"text": result_text, "path": saved_path, "prompt": prompt, "scan": scan}


# ──────────────────────────────────────────────
# 관심종목 스캔: config의 WATCHLIST 종목들만 훑어 '오늘 이상한 놈' 탐지
# ──────────────────────────────────────────────
def _parse_watchlist_cfg(raw_value=None, max_top=50):
    """'코드:이름,...' 또는 'TOP50' 문자열을 (코드, 이름) 리스트로 파싱.
    raw_value가 없으면 config의 MY_STOCKS(대본용) → WATCHLIST(알림봇용) 순으로 사용."""
    if raw_value is None:
        raw_value = str(_cfg.get("MY_STOCKS", "")).strip() or str(_cfg.get("WATCHLIST", "")).strip()
    rawv = str(raw_value).strip()
    m = re.match(r"^TOP\s*(\d{1,3})$", rawv.upper())
    if m:
        n = max(1, min(max_top, int(m.group(1))))
        if krx is None or not _krx_ready():
            return []
        try:
            base = (TODAY - timedelta(days=1)).strftime(FMT)
            day = krx.get_nearest_business_day_in_a_week(base)
            df = krx.get_market_cap_by_ticker(day, market="KOSPI")
            col = "시가총액" if "시가총액" in df.columns else df.columns[0]
            out = []
            for t in df.sort_values(col, ascending=False).head(n).index:
                try:
                    nm = krx.get_market_ticker_name(t)
                except Exception:
                    nm = str(t)
                out.append((str(t).zfill(6), str(nm)))
            return out
        except Exception:
            return []
    out = []
    for part in rawv.split(","):
        part = part.strip()
        if not part:
            continue
        code, _, nm = part.partition(":")
        code = code.strip().zfill(6)
        if code.isdigit() and len(code) == 6:
            out.append((code, nm.strip() or code))
    return out


def update_config_value(key, value):
    """config.txt에서 KEY=... 줄을 갱신(없으면 추가)하고 메모리(_cfg)에도 반영."""
    key = str(key).strip()
    value = str(value).strip()
    try:
        raw = ""
        if os.path.exists(CONFIG_PATH):
            try:
                raw = open(CONFIG_PATH, encoding="utf-8-sig").read()
            except UnicodeDecodeError:
                raw = open(CONFIG_PATH, encoding="cp949").read()
        lines = raw.splitlines()
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        replaced = False
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines += ["", f"# 대본용 관심종목 (UI에서 저장됨)" if key == "MY_STOCKS" else f"# {key}",
                      f"{key}={value}"]
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        _cfg[key] = value
        return True
    except OSError:
        return False


def scan_watchlist_candidates(max_stocks=30, stocks_raw=None):
    """관심종목만 훑어 반직관 신호를 태깅한 스캔 결과를 만든다.
    stocks_raw: '코드:이름,...' 문자열 (없으면 config의 MY_STOCKS → WATCHLIST)"""
    stocks = _parse_watchlist_cfg(stocks_raw)[:max_stocks]
    if not stocks:
        return ("관심종목이 없습니다. UI의 '대본용 관심종목' 칸에 입력하거나 "
                "config.txt의 MY_STOCKS/WATCHLIST를 확인하세요. "
                "(예: 005930:삼성전자,068270:셀트리온)")
    if krx is None or not _krx_ready():
        return "데이터 없음 (pykrx 설치와 KRX 로그인이 필요합니다)"

    header = [f"[관심종목 스캔] {TODAY.strftime('%Y-%m-%d %H:%M')} 기준, 대상 {len(stocks)}개",
              "반직관 신호(⚡)가 붙은 종목이 오늘의 소재 후보입니다.", ""]
    rows = []  # (점수, 출력줄)
    for code, name in stocks:
        try:
            # 전일 등락
            df = krx.get_market_ohlcv_by_date(START, END, code)
            if df is None or len(df) < 2:
                continue
            close = df["종가"].iloc[-1]
            chg = (close / df["종가"].iloc[-2] - 1) * 100

            # 5일 수급 (외국인/연기금)
            fdf = krx.get_market_trading_value_by_date(START, END, code, detail=True)
            frn = png = None
            if fdf is not None and not fdf.empty:
                tail = fdf.tail(5)
                frn = tail["외국인"].sum() / 1e8 if "외국인" in tail.columns else None
                png = tail["연기금"].sum() / 1e8 if "연기금" in tail.columns else None

            # 반직관 태깅
            tags = []
            if chg <= -2 and png is not None and png > 0:
                tags.append("⚡하락 중 연기금 매수")
            if chg <= -2 and frn is not None and frn > 0:
                tags.append("⚡하락 중 외국인 매수")
            if chg >= 2 and frn is not None and frn < 0:
                tags.append("⚡상승 중 외국인 매도")
            if chg >= 2 and png is not None and png < 0:
                tags.append("⚡상승 중 연기금 매도")

            # 최근 3일 공시 건수
            disc = 0
            if _dart_ready():
                try:
                    corp = _get_corp_code(code)
                    if corp:
                        res = requests.get(
                            "https://opendart.fss.or.kr/api/list.json",
                            params={"crtfc_key": DART_API_KEY, "corp_code": corp,
                                    "bgn_de": (TODAY - timedelta(days=3)).strftime(FMT),
                                    "end_de": END, "page_count": 30},
                            timeout=8)
                        d = res.json()
                        if d.get("status") == "000":
                            disc = sum(1 for it in d.get("list", [])
                                       if any(k in it.get("report_nm", "")
                                              for k in ("임원ㆍ주요주주", "대량보유", "자기주식")))
                except Exception:
                    disc = 0
            if disc:
                tags.append(f"⚡최근 3일 공시 {disc}건")

            parts = [f"{name}({code}): 전일 {chg:+.2f}%, 종가 {close:,.0f}원"]
            if frn is not None:
                parts.append(f"5일 외국인 {frn:+,.0f}억")
            if png is not None:
                parts.append(f"연기금 {png:+,.0f}억")
            line = "  " + " / ".join(parts)
            if tags:
                line += "\n    " + " · ".join(tags)
            score = len(tags) * 10 + abs(chg)
            rows.append((score, line))
        except Exception as e:
            rows.append((0, f"  {name}({code}): 조회 실패 ({type(e).__name__})"))
        time.sleep(0.3)

    rows.sort(key=lambda r: -r[0])
    footer = ["", "[소재 고르는 법] ⚡ 태그가 겹치는 종목부터 보세요. "
              "가격과 큰손이 반대로 움직이는 곳이 오늘의 '이면'입니다."]
    return "\n".join(header + [r[1] for r in rows] + footer)


# ──────────────────────────────────────────────
# 스캔 → 소재 선정 → 대본 자동 연결
# ──────────────────────────────────────────────
def extract_top_pick(topic_text):
    """AI 소재 선정 결과에서 1순위 종목명·코드 추출. (name, code|None) 반환, 실패 시 (None, None)"""
    text = topic_text or ""
    m = re.search(r"\[1순위\]\s*[=:]\s*([^|\n]+?)\s*\|\s*(\d{6}|코드미상)", text)
    if m:
        name = m.group(1).strip()
        code = m.group(2)
        return name, (code if code.isdigit() else None)
    # 형식이 안 지켜졌을 때 폴백: '1순위' 언급 이후 첫 '이름(6자리코드)' 패턴
    idx = text.find("1순위")
    tail = text[idx:] if idx >= 0 else text
    m = re.search(r"([가-힣A-Za-z0-9&·\s]{2,25}?)\s*\((\d{6})\)", tail)
    if m:
        return m.group(1).strip(), m.group(2)
    return None, None


def find_stock_code_by_name(name):
    """종목명 → 6자리 코드. DART 기업 매핑(캐시 XML)에서 조회. 못 찾으면 None."""
    name = str(name).strip()
    if not name:
        return None
    try:
        if not os.path.exists(_CORP_CODE_CACHE):
            _get_corp_code("005930")  # 최초 1회 매핑 파일 생성 유도
        tree = ET.parse(_CORP_CODE_CACHE)
        exact, partial = [], []
        for corp in tree.getroot().iter("list"):
            sc = (corp.findtext("stock_code") or "").strip()
            if not sc:
                continue
            cn = (corp.findtext("corp_name") or "").strip()
            if cn == name:
                exact.append(sc)
            elif name in cn or cn in name:
                partial.append(sc)
        if len(exact) == 1:
            return exact[0]
        if not exact and len(partial) == 1:
            return partial[0]
    except Exception:
        return None
    return None


def generate_script_from_topic(topic_text, format_name=None, model=None,
                               temperature=0.7, output_dir=None, save=True,
                               custom_topic=None):
    """AI 소재 선정 결과(topic_text)의 1순위 종목으로 심층수집→OpenAI 대본까지 실행."""
    name, code = extract_top_pick(topic_text)
    if not name:
        raise RuntimeError("소재 선정 결과에서 1순위 종목을 찾지 못했습니다. "
                           "'AI 소재 선정'을 다시 실행하거나 종목을 직접 입력해 주세요.")
    if not code:
        code = find_stock_code_by_name(name)
    if not code:
        raise RuntimeError(f"1순위 '{name}'의 종목코드를 자동으로 찾지 못했습니다. "
                           "상단에 종목명·코드를 직접 입력하고 'AI 대본 생성'을 사용해 주세요.")
    result = generate_ai_script(stock_name=name, stock_code=code, format_name=format_name,
                                model=model, temperature=temperature,
                                output_dir=output_dir, save=save,
                                custom_topic=custom_topic)
    result["stock_name"] = name
    result["stock_code"] = code
    return result


def generate_script_from_scan(format_name=None, model=None,
                              temperature=0.7, output_dir=None, save=True,
                              custom_topic=None):
    """스캔 → AI 소재 선정 → 1순위 심층수집 → OpenAI 대본까지 한 번에 (콘솔용)."""
    topic = generate_topic_ideas(model=model, output_dir=output_dir, save=save)
    script = generate_script_from_topic(topic["text"], format_name=format_name, model=model,
                                        temperature=temperature, output_dir=output_dir, save=save,
                                        custom_topic=custom_topic)
    script["topic"] = topic
    return script


# ──────────────────────────────────────────────
# 콘솔 실행 진입점
# ──────────────────────────────────────────────
def _main():
    import argparse

    parser = argparse.ArgumentParser(description="주식 대본 자료조사기 / OpenAI 자동 대본 생성기")
    parser.add_argument("--name", default="삼성전자", help="종목명")
    parser.add_argument("--code", default="005930", help="종목코드 6자리")
    parser.add_argument("--format", default="이면추적 (평일 심층, 8~10분)", choices=list(SCRIPT_FORMATS.keys()), help="대본 포맷")
    parser.add_argument("--ai", action="store_true", help="OpenAI로 완성 대본까지 생성하고 TXT 저장")
    parser.add_argument("--model", default=None, help="OpenAI 모델명 (기본: config의 OPENAI_TEXT_MODEL)")
    parser.add_argument("--out", default=OUTPUT_DIR, help="TXT 저장 폴더 (기본값: 프로그램 폴더/output)")
    parser.add_argument("--scan", action="store_true", help="시장 소재 스캔만 실행")
    parser.add_argument("--topic-ai", action="store_true", help="시장 소재 스캔 후 OpenAI로 후보 선정")
    parser.add_argument("--report-ai", action="store_true", help="OpenAI로 분석 리포트 생성하고 TXT 저장")
    args = parser.parse_args()

    if args.scan:
        print(scan_market_candidates())
        return

    if args.topic_ai:
        result = generate_topic_ideas(model=args.model, output_dir=args.out, save=True)
        print(result["text"])
        print(f"\n[저장 완료] {result['path']}")
        return

    stock_code = str(args.code).strip().zfill(6)
    raw_data = build_raw_data(args.name, stock_code, force=True)

    if args.report_ai:
        result = generate_ai_report(
            stock_name=args.name,
            stock_code=stock_code,
            raw_data=raw_data,
            model=args.model,
            output_dir=args.out,
            save=True,
        )
        print(result["text"])
        print(f"\n[저장 완료] {result['path']}")
    elif args.ai:
        result = generate_ai_script(
            stock_name=args.name,
            stock_code=stock_code,
            format_name=args.format,
            raw_data=raw_data,
            model=args.model,
            output_dir=args.out,
            save=True,
        )
        print(result["text"])
        print(f"\n[저장 완료] {result['path']}")
    else:
        print(raw_data)
        prompt, _ = build_script_prompt(raw_data=raw_data, format_name=args.format)
        print("\n" + "═" * 60)
        print("[OpenAI에 넣을 최종 대본 프롬프트]")
        print("═" * 60)
        print(prompt)


if __name__ == "__main__":
    _main()


















