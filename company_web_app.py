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
from typing import Any, Dict

from flask import Flask, jsonify, request, send_from_directory

import market_research as mr

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web_company"
OUTPUT_DIR = Path(getattr(mr, "OUTPUT_DIR", ROOT / "output"))

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
_jobs: Dict[str, Dict[str, Any]] = {}
_last: Dict[str, Any] = {"raw_data": "", "script": "", "thumbnail_copy": "", "stock_name": "삼성전자", "stock_code": "005930"}
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


def _thumbnail_images(job_id: str, payload: Dict[str, Any]):
    stock_name = (payload.get("stock_name") or _last.get("stock_name") or "삼성전자").strip()
    copy = payload.get("thumbnail_copy") or _last.get("thumbnail_copy") or ""
    raw_data = payload.get("raw_data") or _last.get("raw_data") or None
    count = int(payload.get("count") or 3)
    if not copy.strip():
        raise ValueError("썸네일 문구가 없습니다. 먼저 썸네일 문구를 생성하세요.")
    _set_job(job_id, status="running", progress=25, department="design", message="디자인실 이미지 시안 생성중")
    result = mr.generate_thumbnail_images_ai(stock_name, copy, raw_data=raw_data, output_dir=str(OUTPUT_DIR), count=count, save=True)
    return result


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


@app.route("/api/thumbnail-copy", methods=["POST"])
def api_thumbnail_copy():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("thumbnail_copy", "썸네일 문구", _thumbnail_copy, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/thumbnail-images", methods=["POST"])
def api_thumbnail_images():
    payload = request.get_json(force=True, silent=True) or {}
    job_id = _start_job("thumbnail_images", "썸네일 이미지", _thumbnail_images, payload)
    return _json_ok(job_id=job_id)


@app.route("/api/last")
def api_last():
    with _lock:
        data = dict(_last)
    return _json_ok(last=data)


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