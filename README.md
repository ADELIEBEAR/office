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

## 주의

`config.txt`, API 키, 출력물, 백업, DART 대용량 XML은 Git에 올리지 않습니다.
로컬에서만 사용할 경우 `config_template.txt`를 참고해서 `config.txt`를 따로 만드세요.
