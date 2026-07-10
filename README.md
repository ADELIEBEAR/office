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

브라우저에서 `http://127.0.0.1:8787` 로 열립니다.

## 현재 연결된 부서

- 리서치팀: 종목 자료 수집
- 작가팀: 90점 대본 / 빠른 초안 생성
- 검수팀: 기존 대본 품질·숫자·시장 단계 방어 로직 사용
- 디자인실:
  - 썸네일 문구 생성
  - CTR 컨셉 회의
  - 선택 컨셉 기반 AI 썸네일 이미지 시안 생성
  - 생성 이미지 갤러리 미리보기

## 보류 중인 부서

- 인포그래픽팀: 슬라이드형 이미지 생성 연결 예정
- 영상팀: 음성·자막·MP4 제작 라인 연결 예정

## 주의

`config.txt`, API 키, 출력물, 백업, DART 대용량 XML은 Git에 올리지 않습니다.
로컬에서만 사용할 경우 `config_template.txt`를 참고해서 `config.txt`를 따로 만드세요.
