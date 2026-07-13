# 오피스

주식 유튜브 대본 제작을 위한 로컬 AI 제작사 프로젝트입니다.

## 구성

- `market_research.py` — 자료 수집, 대본 생성, 썸네일 문구/이미지 엔진
- `market_research_ui.py` — 기존 데스크톱 UI
- `company_web_app.py` — 새 로컬 웹앱 서버
- `web_company/` — 회사형 HTML/CSS/JavaScript UI
- `start_ai_company_web.bat` — 웹앱 실행용 배치 파일

## 실행

```bat
start_ai_company_web.bat
```

또는:

```bash
python company_web_app.py
```

브라우저에서 `http://127.0.0.1:8788` 로 열립니다.

## 현재 연결된 부서

- 리서치팀: 종목 자료 수집
- 작가팀: 90점 대본 / 빠른 초안 생성
- 검수팀: 기존 대본 품질·숫자·시장 단계 방어 로직 사용
- 디자인실:
  - 썸네일 문구 생성
  - CTR 컨셉 회의
  - 선택 컨셉 기반 AI 썸네일 이미지 시안 생성
  - 생성 이미지 갤러리 미리보기
- 영상팀:
  - ElevenLabs 음성 파일 생성
  - 디자인실 썸네일 생성과 병렬 출고
- 출고 데스크:
  - 날짜·종목·주제별 패키지 폴더 생성
  - 수집 데이터, 대본, 썸네일, 음성을 한 폴더로 정리
- 인포그래픽팀:
  - 대본 장면 후보 추출
  - 선택 장면 AI 이미지 병렬 생성

## 아직 연결 전

- 자동 자막 타이밍
- MP4 최종 렌더링

## 주의

`config.txt`, API 키, 출력물, 백업, DART 대용량 XML은 Git에 올리지 않습니다.
로컬에서만 사용할 경우 `config_template.txt`를 참고해서 `config.txt`를 따로 만드세요.

오피스의 `config.txt`에서 KRX/OpenAI/Gemini 설정이 비어 있으면 기존
`stock_script_tool_v2_1/config.txt` 설정을 실행 중에만 자동으로 이어받습니다.
다른 위치의 설정을 쓰려면 `STOCK_SCRIPT_CONFIG` 환경변수에 해당 파일 경로를 지정하세요.
키 값은 오피스 파일로 복사하거나 Git에 저장하지 않습니다.
