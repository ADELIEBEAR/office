# -*- coding: utf-8 -*-
"""AI 제작사 로컬 웹앱.
기존 market_research.py 엔진은 그대로 두고, HTML/CSS/JS 회사형 UI에서 호출한다.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from flask import Flask, jsonify, request, send_file, send_from_directory

import market_research as mr

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web_company"
OUTPUT_DIR = Path(getattr(mr, "OUTPUT_DIR", ROOT / "output"))

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
_jobs: Dict[str, Dict[str, Any]] = {}
_last: Dict[str, Any] = {
    "raw_data": "",
    "script": "",
    "thumbnail_copy": "",
    "thumbnail_concepts": [],
    "thumbnail_images": [],
    "infographic_concepts": [],
    "infographic_slides": [],
    "stock_name": "삼성전자",
    "stock_code": "005930",
}
_lock = threading.Lock()

PRESETS = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "LG전자": "066570",
    "삼성전기": "009150",
    "테슬라": "TSLA",
    "엔비디아": "NVDA",
    "애플": "AAPL",
}

DEPARTMENTS = [
    {"id": "planning", "name": "기획실", "role": "주제/각도 선정"},
    {"id": "research", "name": "리서치 본부", "role": "뉴스·공시·수급"},
    {"id": "writing", "name": "작가팀", "role": "구어체 대본"},
    {"id": "review", "name": "검수팀", "role": "숫자·국면 체크"},
    {"id": "design", "name": "디자인실", "role": "썸네일·인포그래픽"},
    {"id": "video", "name": "영상팀", "role": "슬라이드·음성·영상"},
    {"id": "shipping", "name": "출고 데스크", "role": "저장·복사·폴더"},
]


def _json_ok(**kwargs):
    return jsonify({"ok": True, **kwargs})


def _json_error(message: str, code: int = 400):
    return jsonify({"ok": False, "error": str(message)}), code


def _new_job(kind: str, title: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "title": title,
            "status": "queued",
            "progress": 0,
            "department": "planning",
            "message": "작업 대기중",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    return job_id


def _set_job(job_id: str, **updates):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def _run_job(job_id: str, target, *args, **kwargs):
    try:
        result = target(job_id, *args, **kwargs)
        _set_job(job_id, status="done", progress=100, department="shipping", message="출고 완료", result=result)
    except Exception as exc:  # noqa: BLE001 - UI에 그대로 보여주기 위한 최종 방어
        _set_job(job_id, status="error", progress=100, department="review", message="오류 발생", error=str(exc))


def _start_job(kind: str, title: str, target, *args, **kwargs):
    job_id = _new_job(kind, title)
    threading.Thread(target=_run_job, args=(job_id, target, *args), kwargs=kwargs, daemon=True).start()
    return job_id


def _choose_raw_builder(format_name: str, custom_topic: str):
    if hasattr(mr, "is_information_script_request") and mr.is_information_script_request(format_name=format_name, custom_topic=custom_topic):
        return mr.build_information_raw_data
    if hasattr(mr, "is_weekend_script_request") and mr.is_weekend_script_request(format_name=format_name, custom_topic=custom_topic):
        return mr.build_weekend_raw_data
    return mr.build_raw_data


def _collect(job_id: str, payload: Dict[str, Any]):
    stock_name = (payload.get("stock_name") or "삼성전자").strip()
    stock_code = (payload.get("stock_code") or "005930").strip().upper()
    format_name = payload.get("format_name") or next(iter(mr.SCRIPT_FORMATS))
    custom_topic = payload.get("custom_topic") or ""
    _set_job(job_id, status="running", progress=15, department="research", message=f"{stock_name} 자료 수집중")
    builder = _choose_raw_builder(format_name, custom_topic)
    raw = builder(stock_name, stock_code, force=True)
    with _lock:
        _last.update({"raw_data": raw, "stock_name": stock_name, "stock_code": stock_code})
    _set_job(job_id, progress=85, department="review", message="수집 데이터 정리중")
    return {"stock_name": stock_name, "stock_code": stock_code, "raw_data": raw, "chars": len(raw)}


def _script(job_id: str, payload: Dict[str, Any]):
    stock_name = (payload.get("stock_name") or _last.get("stock_name") or "삼성전자").strip()
    stock_code = (payload.get("stock_code") or _last.get("stock_code") or "005930").strip().upper()
    format_name = payload.get("format_name") or next(iter(mr.SCRIPT_FORMATS))
    custom_topic = payload.get("custom_topic") or ""
    engine = payload.get("engine") or "chain"
    output_dir = payload.get("output_dir") or str(OUTPUT_DIR)
    raw_data = payload.get("raw_data") or _last.get("raw_data") or None
    if not raw_data:
        _set_job(job_id, progress=10, department="research", message="대본용 자료 먼저 수집중")
        builder = _choose_raw_builder(format_name, custom_topic)
        raw_data = builder(stock_name, stock_code, force=True)
    _set_job(job_id, status="running", progress=35, department="writing", message="작가팀 대본 작성중")
    result = mr.generate_ai_script(
        stock_name=stock_name,
        stock_code=stock_code,
        format_name=format_name,
        raw_data=raw_data,
        output_dir=output_dir,
        save=True,
        custom_topic=custom_topic,
        engine=engine,
    )
    text = result.get("text", "")
    with _lock:
        _last.update({"raw_data": raw_data, "script": text, "stock_name": stock_name, "stock_code": stock_code})
    _set_job(job_id, progress=88, department="review", message="검수팀 최종 확인중")
    return {"stock_name": stock_name, "stock_code": stock_code, "script": text, "path": result.get("path"), "chars": len(text)}


def _thumbnail_copy(job_id: str, payload: Dict[str, Any]):
    stock_name = (payload.get("stock_name") or _last.get("stock_name") or "삼성전자").strip()
    script = payload.get("script") or _last.get("script") or ""
    raw_data = payload.get("raw_data") or _last.get("raw_data") or None
    if not script.strip():
        raise ValueError("완성 대본이 없습니다. 먼저 대본을 생성하세요.")
    _set_job(job_id, status="running", progress=35, department="design", message="디자인실 썸네일 문구 기획중")
    result = mr.generate_thumbnail_copy(stock_name, script, raw_data=raw_data, output_dir=str(OUTPUT_DIR), save=True)
    text = result.get("text", "") if isinstance(result, dict) else str(result)
    with _lock:
        _last.update({"thumbnail_copy": text})
    return {"thumbnail_copy": text, "path": result.get("path") if isinstance(result, dict) else None, "chars": len(text)}


def _path_to_output_url(path: str | None) -> str | None:
    if not path:
        return None
    try:
        p = Path(path).resolve()
        return "/api/output-file?path=" + quote(str(p))
    except Exception:
        return None


def _thumbnail_concepts(job_id: str, payload: Dict[str, Any]):
    stock_name = (payload.get("stock_name") or _last.get("stock_name") or "삼성전자").strip()
    script = payload.get("script") or _last.get("script") or ""
    raw_data = payload.get("raw_data") or _last.get("raw_data") or None
    thumbnail_copy = payload.get("thumbnail_copy") or _last.get("thumbnail_copy") or ""
    count = max(4, min(int(payload.get("count") or 8), 10))
    if not script.strip() and not thumbnail_copy.strip():
        raise ValueError("대본 또는 썸네일 문구가 없습니다. 먼저 대본을 생성하세요.")
    _set_job(job_id, status="running", progress=25, department="design", message="디자인실 CTR 컨셉 회의중")
    if not thumbnail_copy.strip():
        result = mr.generate_thumbnail_copy(stock_name, script, raw_data=raw_data, output_dir=str(OUTPUT_DIR), save=True)
        thumbnail_copy = result.get("text", "") if isinstance(result, dict) else str(result)
    _set_job(job_id, progress=65, department="design", message="썸네일 후보 정리중")
    extractor = getattr(mr, "_extract_thumbnail_image_candidates", None)
    if callable(extractor):
        raw_candidates = extractor(thumbnail_copy, limit=count)
    else:
        raw_candidates = []
    profiles = list(getattr(mr, "THUMBNAIL_DESIGN_PROFILES", []) or [])
    concepts = []
    for idx in range(count):
        candidate = raw_candidates[idx % len(raw_candidates)] if raw_candidates else {}
        profile = profiles[idx % len(profiles)] if profiles else {"label": "프리미엄", "prompt": "premium high CTR Korean stock YouTube thumbnail"}
        concepts.append({
            "id": f"concept-{idx + 1}",
            "selected": idx < min(4, count),
            "candidate_no": idx + 1,
            "badge": candidate.get("badge", "") if isinstance(candidate, dict) else "",
            "main": candidate.get("main", "") if isinstance(candidate, dict) else "",
            "sub": candidate.get("sub", "") if isinstance(candidate, dict) else "",
            "style": profile.get("label", "") if isinstance(profile, dict) else str(profile),
            "style_prompt": profile.get("prompt", "") if isinstance(profile, dict) else str(profile),
            "ctr_score": max(70, 96 - idx * 3),
        })
    with _lock:
        _last.update({"thumbnail_copy": thumbnail_copy, "thumbnail_concepts": concepts})
    return {"thumbnail_copy": thumbnail_copy, "concepts": concepts}


def _thumbnail_images(job_id: str, payload: Dict[str, Any]):
    stock_name = (payload.get("stock_name") or _last.get("stock_name") or "삼성전자").strip()
    copy = payload.get("thumbnail_copy") or _last.get("thumbnail_copy") or ""
    raw_data = payload.get("raw_data") or _last.get("raw_data") or None
    count = int(payload.get("count") or 3)
    selected_concepts = payload.get("concepts") or []
    if not copy.strip():
        raise ValueError("썸네일 문구가 없습니다. 먼저 썸네일 문구를 생성하세요.")
    _set_job(job_id, status="running", progress=25, department="design", message="디자인실 이미지 시안 생성중")
    if selected_concepts:
        # 선택된 컨셉은 원본 썸네일 문구 앞에 붙여서 이미지 모델이 해당 문구를 우선 쓰게 한다.
        concept_lines = []
        for c in selected_concepts[:4]:
            concept_lines.append(
                f"[선택 컨셉] 배지: {c.get('badge','')} / 메인: {c.get('main','')} / 서브: {c.get('sub','')} / 스타일: {c.get('style','')}"
            )
        copy = "\n".join(concept_lines) + "\n\n" + copy
        count = len(selected_concepts)
    result = mr.generate_thumbnail_images_ai(stock_name, copy, raw_data=raw_data, output_dir=str(OUTPUT_DIR), count=count, save=True)
    for item in result.get("items", []):
        item["url"] = _path_to_output_url(item.get("path"))
    result["urls"] = [_path_to_output_url(p) for p in result.get("paths", [])]
    with _lock:
        _last.update({"thumbnail_images": result.get("items", [])})
    return result


INFOGRAPHIC_STYLES = [
    {
        "label": "프리미엄 다크 리포트",
        "layout": "큰 제목 + 핵심 숫자 카드 + 3개 근거 카드",
        "prompt": "deep black and navy premium Korean finance infographic, glass cards, subtle blue glow, clean keynote slide",
    },
    {
        "label": "돈의 흐름 맵",
        "layout": "좌우 자금 흐름 화살표 + 하단 체크포인트",
        "prompt": "fund flow map, arrows, institutional money movement, dark professional dashboard, minimal but high contrast",
    },
    {
        "label": "질문형 분석 보드",
        "layout": "상단 질문 + 중앙 모순 구조 + 우측 결론 박스",
        "prompt": "premium explainer board, question driven Korean business slide, clean hierarchy, dramatic but not noisy",
    },
    {
        "label": "주말 큰그림 슬라이드",
        "layout": "큰 그림 키워드 3개 + 다음 관전 포인트",
        "prompt": "weekend big picture finance slide, calm editorial layout, warm dark gradient, clean Korean typography",
    },
    {
        "label": "숨은정보 해부도",
        "layout": "겉으로 보이는 숫자 vs 실제 봐야 할 정보",
        "prompt": "hidden insight breakdown, layered cards, microscope style metaphor, premium stock analysis infographic",
    },
    {
        "label": "타임라인 요약",
        "layout": "왼쪽 시간 흐름 + 오른쪽 판단 기준",
        "prompt": "timeline style finance infographic, restrained dark theme, crisp readable Korean text, chart accents",
    },
]


def _compact_text(value: str, limit: int = 72) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip("., ") + "…"


def _script_blocks(script: str, limit: int = 6) -> List[str]:
    raw_blocks = []
    for part in str(script or "").replace("\r\n", "\n").split("---<"):
        cleaned = " ".join(line.strip() for line in part.splitlines() if line.strip())
        if len(cleaned) >= 12:
            raw_blocks.append(cleaned)
    if not raw_blocks and script.strip():
        sentences = [s.strip() for s in script.replace("\n", " ").split(".") if len(s.strip()) >= 12]
        raw_blocks = sentences[:limit]
    return raw_blocks[:limit]


def _infer_infographic_title(stock_name: str, block: str, idx: int) -> str:
    if idx == 0:
        return f"{stock_name}, 겉보다 속을 봐야 합니다"
    if "외국인" in block or "기관" in block or "개인" in block:
        return "돈의 방향이 갈린 지점"
    if "실적" in block or "영업이익" in block or "매출" in block:
        return "실적 숫자 다음에 볼 것"
    if "환율" in block or "반도체" in block or "글로벌" in block:
        return "시장 배경까지 같이 봐야 합니다"
    if "공매도" in block or "신용" in block:
        return "수급 뒤에 숨어 있는 부담"
    return f"핵심 장면 {idx + 1}"


def _infographic_concepts(job_id: str, payload: Dict[str, Any]):
    stock_name = (payload.get("stock_name") or _last.get("stock_name") or "삼성전자").strip()
    script = payload.get("script") or _last.get("script") or ""
    count = max(3, min(int(payload.get("count") or 6), 8))
    if not script.strip():
        raise ValueError("완성 대본이 없습니다. 먼저 대본을 생성하세요.")

    _set_job(job_id, status="running", progress=25, department="design", message="인포그래픽팀이 대본 장면을 나누는 중")
    blocks = _script_blocks(script, limit=count)
    if not blocks:
        raise ValueError("인포그래픽으로 나눌 대본 문단을 찾지 못했습니다.")

    concepts = []
    for idx, block in enumerate(blocks[:count]):
        style = INFOGRAPHIC_STYLES[idx % len(INFOGRAPHIC_STYLES)]
        concepts.append({
            "id": f"info-{idx + 1}",
            "selected": idx < min(4, len(blocks)),
            "scene_no": idx + 1,
            "title": _infer_infographic_title(stock_name, block, idx),
            "main": _compact_text(block, 46),
            "support": _compact_text(block, 96),
            "layout": style["layout"],
            "style": style["label"],
            "style_prompt": style["prompt"],
            "source_text": block,
        })

    with _lock:
        _last.update({"infographic_concepts": concepts})
    return {"infographic_concepts": concepts}


def _build_infographic_prompt(stock_name: str, concept: Dict[str, Any]) -> str:
    return f"""
Create ONE premium 16:9 Korean finance infographic slide.

Subject: {stock_name}
Style: {concept.get('style_prompt') or 'premium dark Korean finance infographic'}
Layout: {concept.get('layout') or 'large title, main point card, three support cards'}

Use ONLY these Korean text elements. Do not add extra claims or invented numbers.
TITLE: {concept.get('title') or ''}
MAIN: {concept.get('main') or ''}
SUPPORT: {concept.get('support') or ''}

Design rules:
- Deep black or navy matte background, premium office dashboard mood.
- Large readable Korean typography. Text must be sharp and high contrast.
- Keep text short. Do not paste long script sentences.
- Use 3 to 5 visual blocks maximum.
- Add tasteful chart lines, arrows, cards, flow maps, or data widgets only as visual support.
- No logos, no watermarks, no random English labels, no fake tickers, no extra numbers.
- Calm professional finance tone. No disaster, war, blood, explosion, or game clutter.
- Leave safe margins so YouTube crop does not cut text.

Source scene for context only, not for direct copy:
{_compact_text(concept.get('source_text') or '', 500)}
""".strip()


def _generate_infographic_image(stock_name: str, concept: Dict[str, Any], output_dir: str):
    client = mr._make_openai_client()
    prompt = _build_infographic_prompt(stock_name, concept)
    errors = []
    candidates_fn = getattr(mr, "_openai_image_model_candidates", None)
    candidates = candidates_fn(None) if callable(candidates_fn) else ["gpt-image-1.5", "gpt-image-1"]
    for model_name in candidates:
        try:
            response = client.images.generate(
                model=model_name,
                prompt=prompt,
                size="1536x1024",
                n=1,
            )
            path = mr._save_openai_image_response(
                response,
                output_dir,
                stock_name,
                model_name,
                suffix=f"인포그래픽_{concept.get('scene_no') or ''}_{concept.get('style') or ''}",
            )
            return {
                "path": path,
                "url": _path_to_output_url(path),
                "model": model_name,
                "prompt": prompt,
                "title": concept.get("title", ""),
                "main": concept.get("main", ""),
                "style": concept.get("style", ""),
                "scene_no": concept.get("scene_no"),
            }
        except Exception as exc:  # noqa: BLE001 - 모델 fallback용
            errors.append(f"{model_name}: {exc}")
            msg = str(exc).lower()
            if "404" not in msg and "not_found" not in msg and "not found" not in msg:
                break
    raise RuntimeError("인포그래픽 이미지 생성 실패\n" + "\n".join(errors[-3:]))


def _infographic_slides(job_id: str, payload: Dict[str, Any]):
    stock_name = (payload.get("stock_name") or _last.get("stock_name") or "삼성전자").strip()
    output_dir = payload.get("output_dir") or str(OUTPUT_DIR)
    selected = payload.get("infographic_concepts") or []
    if not selected:
        selected = [c for c in (_last.get("infographic_concepts") or []) if c.get("selected")]
    if not selected:
        raise ValueError("선택된 인포그래픽 후보가 없습니다. 먼저 인포 기획을 눌러주세요.")

    selected = selected[:4]
    _set_job(job_id, status="running", progress=18, department="design", message="인포그래픽팀 이미지 시안 생성 시작")
    items = []
    for idx, concept in enumerate(selected):
        pct = 18 + int((idx / max(1, len(selected))) * 68)
        _set_job(job_id, progress=pct, department="design", message=f"인포그래픽 {idx + 1}/{len(selected)} 생성중")
        items.append(_generate_infographic_image(stock_name, concept, output_dir))

    with _lock:
        _last.update({"infographic_slides": items})
    return {"infographic_items": items}


def _one_click_package(job_id: str, payload: Dict[str, Any]):
    """영상팀 전 단계까지 한 번에 준비한다.

    비용과 시간이 큰 실제 이미지 생성은 제외하고,
    자료·대본·썸네일 문구·썸네일 컨셉·인포그래픽 기획까지만 자동 진행한다.
    """
    stock_name = (payload.get("stock_name") or "삼성전자").strip()
    stock_code = (payload.get("stock_code") or "005930").strip().upper()
    format_name = payload.get("format_name") or next(iter(mr.SCRIPT_FORMATS))
    custom_topic = payload.get("custom_topic") or ""
    engine = payload.get("engine") or "chain"
    output_dir = payload.get("output_dir") or str(OUTPUT_DIR)

    _set_job(job_id, status="running", progress=5, department="planning", message="원클릭 제작실 접수")

    collect_payload = {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "format_name": format_name,
        "custom_topic": custom_topic,
    }
    _set_job(job_id, progress=12, department="research", message="1/5 리서치팀 자료 수집")
    collected = _collect(job_id, collect_payload)
    raw_data = collected.get("raw_data", "")

    script_payload = {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "format_name": format_name,
        "custom_topic": custom_topic,
        "engine": engine,
        "output_dir": output_dir,
        "raw_data": raw_data,
    }
    _set_job(job_id, progress=35, department="writing", message="2/5 작가팀 대본 작성")
    scripted = _script(job_id, script_payload)
    script = scripted.get("script", "")

    thumb_payload = {
        "stock_name": stock_name,
        "script": script,
        "raw_data": raw_data,
    }
    _set_job(job_id, progress=66, department="design", message="3/5 디자인팀 썸네일 문구 작성")
    thumb_copy = _thumbnail_copy(job_id, thumb_payload)
    thumbnail_copy = thumb_copy.get("thumbnail_copy", "")

    concept_payload = {
        "stock_name": stock_name,
        "script": script,
        "raw_data": raw_data,
        "thumbnail_copy": thumbnail_copy,
        "count": 8,
    }
    _set_job(job_id, progress=78, department="design", message="4/5 CTR 컨셉 후보 정리")
    thumb_concepts = _thumbnail_concepts(job_id, concept_payload)

    info_payload = {
        "stock_name": stock_name,
        "script": script,
        "count": 6,
    }
    _set_job(job_id, progress=90, department="design", message="5/5 인포그래픽 장면 기획")
    info_concepts = _infographic_concepts(job_id, info_payload)

    summary = {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "format_name": format_name,
        "script_chars": len(script),
        "raw_chars": len(raw_data),
        "thumbnail_concept_count": len(thumb_concepts.get("concepts", []) or []),
        "infographic_concept_count": len(info_concepts.get("infographic_concepts", []) or []),
        "script_path": scripted.get("path"),
        "thumbnail_copy_path": thumb_copy.get("path"),
        "next_steps": [
            "썸네일 컨셉을 고른 뒤 이미지 시안을 누르세요.",
            "인포그래픽 장면을 고른 뒤 인포 이미지를 누르세요.",
            "출고 폴더에서 저장된 대본과 문구 파일을 확인하세요.",
        ],
    }
    return {
        "summary": summary,
        "raw_data": raw_data,
        "script": script,
        "path": scripted.get("path"),
        "thumbnail_copy": thumbnail_copy,
        "concepts": thumb_concepts.get("concepts", []),
        "infographic_concepts": info_concepts.get("infographic_concepts", []),
    }


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:path>")
def static_file(path):
    return send_from_directory(WEB_DIR, path)


@app.route("/api/config")
def api_config():
    return _json_ok(
        presets=PRESETS,
        formats=list(mr.SCRIPT_FORMATS.keys()),
        departments=DEPARTMENTS,
        output_dir=str(OUTPUT_DIR),
        external_tools={
            "infographic_source": r"C:\Users\pc\Documents\Codex\2026-07-03\d\work\stock_editor_app.py",
            "gpt_thumbnail_source": r"C:\Users\pc\Documents\Codex\2026-07-08\new-chat\outputs\thumbnail-generator",
        },
    )


@app.route("/api/job/<job_id>")
def api_job(job_id):
    with _lock:
        job = dict(_jobs.get(job_id) or {})
    if not job:
        return _json_error("작업을 찾을 수 없습니다.", 404)
    return _json_ok(job=job)


@app.route("/api/collect", methods=["POST"])
def api_collect():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("collect", "자료 수집", _collect, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/script", methods=["POST"])
def api_script():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("script", "대본 생성", _script, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/one-click", methods=["POST"])
def api_one_click():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("one_click", "원클릭 제작실", _one_click_package, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/thumbnail-copy", methods=["POST"])
def api_thumbnail_copy():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("thumbnail_copy", "썸네일 문구", _thumbnail_copy, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/thumbnail-concepts", methods=["POST"])
def api_thumbnail_concepts():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("thumbnail_concepts", "썸네일 컨셉 회의", _thumbnail_concepts, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/thumbnail-images", methods=["POST"])
def api_thumbnail_images():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("thumbnail_images", "썸네일 이미지", _thumbnail_images, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/infographic-concepts", methods=["POST"])
def api_infographic_concepts():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("infographic_concepts", "인포그래픽 기획", _infographic_concepts, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/infographic-slides", methods=["POST"])
def api_infographic_slides():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("infographic_slides", "인포그래픽 이미지", _infographic_slides, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/last")
def api_last():
    with _lock:
        data = dict(_last)
    return _json_ok(last=data)


@app.route("/api/output-file")
def api_output_file():
    raw = request.args.get("path", "")
    if not raw:
        return _json_error("파일 경로가 없습니다.", 400)
    path = Path(raw).resolve()
    try:
        output_root = OUTPUT_DIR.resolve()
        project_root = ROOT.resolve()
        is_allowed = str(path).startswith(str(output_root)) or str(path).startswith(str(project_root))
    except Exception:
        is_allowed = False
    if not is_allowed or not path.exists() or not path.is_file():
        return _json_error("열 수 없는 파일입니다.", 404)
    return send_file(path)


@app.route("/api/open-output", methods=["POST"])
def api_open_output():
    os.startfile(str(OUTPUT_DIR))
    return _json_ok(path=str(OUTPUT_DIR))


def main():
    port = int(os.environ.get("AI_COMPANY_PORT", "8787"))
    url = f"http://127.0.0.1:{port}/"
    if os.environ.get("AI_COMPANY_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"AI 제작사 로컬 웹앱: {url}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
