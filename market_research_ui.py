# -*- coding: utf-8 -*-
"""
주식 대본 자료조사기 & OpenAI 자동 생성 UI - 기능 복구 확장판

실행:
    python market_research_ui.py

필요 패키지:
    pip install requests pykrx yfinance pandas lxml openai
"""
import os
import sys
import threading
import queue
import calendar
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from concurrent.futures import ThreadPoolExecutor, as_completed

import market_research as mr

_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

PRESETS = {
    "직접 입력": "",
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "LG에너지솔루션": "373220",
    "LG전자": "066570",
    "현대차": "005380",
    "NAVER": "035420",
    "카카오": "035720",
    "셀트리온": "068270",
    "POSCO홀딩스": "005490",
    "테슬라": "TSLA",
}


class _CTkTabAdapter:
    """customtkinter 탭뷰를 기존 ttk.Notebook 호출 방식에 맞춰주는 얇은 어댑터."""
    def __init__(self, tabview):
        self.tabview = tabview
        self.frame_to_title = {}
        self.title_to_frame = {}
        self.first_frame = None

    def add_tab(self, title):
        frame = self.tabview.add(title)
        self.frame_to_title[frame] = title
        self.title_to_frame[title] = frame
        if self.first_frame is None:
            self.first_frame = frame
        return frame

    def select(self, frame=None):
        if frame is None:
            title = self.tabview.get()
            return self.title_to_frame.get(title, self.first_frame)
        title = self.frame_to_title.get(frame)
        if title:
            self.tabview.set(title)
        return frame

    def tab(self, frame, option=None):
        title = self.frame_to_title.get(frame, "")
        if option == "text":
            return title
        return {"text": title}


class App(ctk.CTk if ctk is not None else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("주식 AI 제작사 - v2.33 AI Company Office")
        self.geometry("1240x760")
        self.minsize(1050, 680)

        self.result_queue = queue.Queue()
        self.last_raw = None
        self.last_name = ""
        self.last_code = ""
        self.last_saved_path = None
        self.action_buttons = []
        self._text_widgets = []

        self._build_ui()
        self._apply_theme()
        self._check_credentials()
        self.after(100, self._poll_queue)

    def _build_ui(self):
        if ctk is not None:
            self._build_ctk_ui()
            return

        top = ttk.Frame(self, padding=(18, 16, 18, 8), style="App.TFrame")
        top.pack(fill="x")

        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)
        top.columnconfigure(5, weight=1)

        header = ttk.Frame(top, style="Hero.TFrame", padding=(22, 18))
        header.grid(row=0, column=0, columnspan=8, sticky="we", pady=(0, 12))
        hero_left = ttk.Frame(header, style="Hero.TFrame")
        hero_left.pack(side="left", fill="x", expand=True)
        ttk.Label(hero_left, text="오늘의 영상 준비", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(hero_left, text="오늘 대본, 가볍게 시작해볼까요?", style="Title.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(
            hero_left,
            text="종목만 고르면 자료 수집부터 대본, 썸네일까지 차근차근 도와드릴게요.",
            style="HeaderHint.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        hero_right = ttk.Frame(header, style="Hero.TFrame")
        hero_right.pack(side="right")
        ttk.Label(hero_right, text="추천 흐름", style="BadgeTitle.TLabel").pack(anchor="e")
        ttk.Label(hero_right, text="수집하고, 쓰고, 썸네일까지", style="Badge.TLabel").pack(anchor="e", pady=(6, 0))

        ttk.Label(top, text="종목", style="Field.TLabel").grid(row=1, column=0, sticky="w")
        self.preset_var = tk.StringVar(value="삼성전자")
        preset_box = ttk.Combobox(top, textvariable=self.preset_var, values=list(PRESETS.keys()), width=18, state="readonly")
        preset_box.grid(row=1, column=1, padx=(6, 14), sticky="we")
        preset_box.bind("<<ComboboxSelected>>", self._on_preset)

        ttk.Label(top, text="종목명", style="Field.TLabel").grid(row=1, column=2, sticky="w")
        self.name_var = tk.StringVar(value="삼성전자")
        ttk.Entry(top, textvariable=self.name_var, width=16).grid(row=1, column=3, padx=(6, 14), sticky="we")

        ttk.Label(top, text="코드", style="Field.TLabel").grid(row=1, column=4, sticky="w")
        self.code_var = tk.StringVar(value="005930")
        ttk.Entry(top, textvariable=self.code_var, width=10).grid(row=1, column=5, padx=(6, 14), sticky="we")

        ttk.Label(top, text="포맷", style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.format_var = tk.StringVar(value="정프로용 개인채널 (친근형, 약 12,000자)")
        fmt_box = ttk.Combobox(top, textvariable=self.format_var, values=list(mr.SCRIPT_FORMATS.keys()), width=42, state="readonly")
        fmt_box.grid(row=2, column=1, columnspan=3, sticky="we", padx=(6, 14), pady=(10, 0))

        ttk.Label(top, text="저장 폴더", style="Field.TLabel").grid(row=2, column=4, sticky="w", pady=(10, 0))
        self.out_var = tk.StringVar(value=mr.OUTPUT_DIR)
        ttk.Entry(top, textvariable=self.out_var, width=44).grid(row=2, column=5, columnspan=3, sticky="we", padx=(6, 0), pady=(10, 0))

        ttk.Label(top, text="주제/메모", style="Field.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.custom_topic_var = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.custom_topic_var, width=90).grid(
            row=3, column=1, columnspan=7, sticky="we", padx=(6, 0), pady=(10, 0))

        ttk.Label(
            top,
            text="비워두면 알아서 써요. 실적발표·공시·뉴스처럼 꼭 넣을 내용만 살짝 적어주세요.",
            style="Hint.TLabel",
        ).grid(row=4, column=1, columnspan=7, sticky="w", pady=(4, 0))

        ttk.Label(top, text="대본 날짜", style="Field.TLabel").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.script_date_var = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.script_date_var, width=16).grid(
            row=5, column=1, sticky="we", padx=(6, 14), pady=(10, 0))
        quick_dates = ttk.Frame(top)
        quick_dates.grid(row=5, column=2, columnspan=6, sticky="we", pady=(10, 0))
        ttk.Button(quick_dates, text="달력", width=8,
                   command=self._open_calendar_popup).pack(side="left", padx=(0, 6))
        ttk.Button(quick_dates, text="오늘", width=8,
                   command=lambda: self._set_quick_script_date("today")).pack(side="left", padx=(0, 6))
        ttk.Button(quick_dates, text="비우기", width=8,
                   command=self._clear_script_date).pack(side="left")

        ttk.Label(top, text="관심종목", style="Field.TLabel").grid(row=6, column=0, sticky="w", pady=(10, 0))
        my_stocks_default = (str(mr._cfg.get("MY_STOCKS", "")).strip()
                             or "005930:삼성전자,000660:SK하이닉스,066570:LG전자")
        self.my_stocks_var = tk.StringVar(value=my_stocks_default)
        ttk.Entry(top, textvariable=self.my_stocks_var, width=80).grid(
            row=6, column=1, columnspan=6, sticky="we", padx=(6, 8), pady=(10, 0))
        ttk.Button(top, text="저장", width=8,
                   command=self._save_my_stocks).grid(row=6, column=7, sticky="e", pady=(10, 0))

        work = ttk.Frame(self, padding=(18, 8, 18, 10), style="App.TFrame")
        work.pack(fill="x")
        work.columnconfigure(0, weight=3)
        work.columnconfigure(1, weight=2)
        work.columnconfigure(2, weight=2)

        main = ttk.LabelFrame(work, text="  오늘 바로 만들기  ", padding=14, style="PrimaryCard.TLabelframe")
        main.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        main.columnconfigure((0, 1, 2), weight=1)
        self._add_button(main, "① 자료 수집", self._start_collect, accent=True).grid(row=0, column=0, padx=6, pady=6, sticky="nsew")
        self._add_button(main, "② 대본 생성", self._start_ai_generation, accent=True).grid(row=0, column=1, padx=6, pady=6, sticky="nsew")
        self._add_button(main, "③ 썸네일 문구", self._start_thumbnail_copy, accent=True).grid(row=0, column=2, padx=6, pady=6, sticky="nsew")
        self._add_button(main, "전체 자료수집", self._start_batch_collect).grid(row=1, column=0, padx=6, pady=6, sticky="nsew")
        self._add_button(main, "전체 대본생성", self._start_batch_scripts).grid(row=1, column=1, columnspan=2, padx=6, pady=6, sticky="nsew")
        ttk.Label(
            main,
            text="종목이 정해졌다면 여기만 왼쪽부터 눌러도 충분해요.",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=(6, 0))

        angle = ttk.LabelFrame(work, text="  다른 각도 뽑기  ", padding=14, style="Card.TLabelframe")
        angle.grid(row=0, column=1, sticky="nsew", padx=8)
        angle.columnconfigure(0, weight=1)
        self._add_button(angle, "흐름 후보 보기", self._start_angles).grid(row=0, column=0, padx=4, pady=4, sticky="we")
        self._add_button(angle, "선택 흐름으로 대본", self._start_script_with_angle).grid(row=1, column=0, padx=4, pady=4, sticky="we")
        ttk.Label(angle, text="같은 종목도 다른 이야기로 풀고 싶을 때", style="Hint.TLabel").grid(row=2, column=0, sticky="w", padx=5, pady=(4, 0))

        discover = ttk.LabelFrame(work, text="  소재를 못 정했을 때  ", padding=14, style="Card.TLabelframe")
        discover.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        discover.columnconfigure((0, 1), weight=1)
        self._add_button(discover, "내 종목 자동", self._start_auto_pipeline_watchlist, accent=True).grid(row=0, column=0, padx=4, pady=4, sticky="we")
        self._add_button(discover, "시장 전체 자동", self._start_auto_pipeline, accent=True).grid(row=0, column=1, padx=4, pady=4, sticky="we")
        self._add_button(discover, "내 종목 스캔", self._start_watchlist_scan).grid(row=1, column=0, padx=4, pady=4, sticky="we")
        self._add_button(discover, "시장 스캔", self._start_scan).grid(row=1, column=1, padx=4, pady=4, sticky="we")
        self._add_button(discover, "AI 소재 선정", self._start_topic_ai).grid(row=2, column=0, padx=4, pady=4, sticky="we")
        self._add_button(discover, "1순위로 대본", self._start_script_from_topic).grid(row=2, column=1, padx=4, pady=4, sticky="we")

        self.status_var = tk.StringVar(value="AI 제작사 대기 중입니다. 종목을 주시면 리서치팀부터 움직입니다.")
        self.status_label = ttk.Label(self, textvariable=self.status_var,
                                      style="Status.TLabel", padding=(18, 8))
        self.status_label.pack(fill="x")
        self.progress = ttk.Progressbar(self, mode="indeterminate")

        nb = ttk.Notebook(self, padding=(18, 8, 18, 0), style="Toss.TNotebook")
        nb.pack(fill="both", expand=True)

        self._build_company_map_tab(nb)
        self._build_guide_tab(nb)
        self.ai_result_text = self._make_text_tab(nb, "완성 대본")
        self.thumbnail_text = self._make_text_tab(nb, "🎯 썸네일")
        self.data_text = self._make_text_tab(nb, "수집 데이터")
        self.topic_text = self._make_text_tab(nb, "소재/흐름")
        self.script_prompt_text = self._make_text_tab(nb, "대본 프롬프트")
        self.ai_report_text = self._make_text_tab(nb, "분석 리포트")
        self.report_prompt_text = self._make_text_tab(nb, "리포트 프롬프트")
        self.scan_text = self._make_text_tab(nb, "스캔 원문")
        self.telegram_text = self._make_text_tab(nb, "텔레그램")
        self.log_text = self._make_text_tab(nb, "🧾 로그")

        bottom = ttk.Frame(self, padding=(18, 10, 18, 14), style="Bottom.TFrame")
        bottom.pack(fill="x")
        ttk.Button(bottom, text="AI 대본 복사", command=lambda: self._copy(self.ai_result_text)).pack(side="left", padx=(0, 6))
        ttk.Button(bottom, text="현재 탭 저장", command=self._save_current_tab).pack(side="left", padx=6)
        ttk.Button(bottom, text="결과 파일 열기", command=self._open_last_result_file).pack(side="left", padx=6)
        ttk.Button(bottom, text="저장 폴더 열기", command=self._open_output_folder).pack(side="left", padx=6)
        ttk.Button(bottom, text="수집 데이터 저장", command=lambda: self._save_widget(self.data_text, "수집데이터")).pack(side="left", padx=6)
        ttk.Button(bottom, text="텔레그램 미리보기", command=self._preview_telegram).pack(side="left", padx=6)
        ttk.Button(bottom, text="텔레그램 보내기", command=self._send_telegram).pack(side="left", padx=6)
        ttk.Button(bottom, text="대본+썸네일 복사", command=self._copy_script_and_thumbnail).pack(side="left", padx=6)
        ttk.Button(bottom, text="주제 붙여넣기", command=self._paste_topic_memo).pack(side="left", padx=6)
        ttk.Button(bottom, text="주제 비우기", command=self._clear_topic_memo).pack(side="left", padx=6)
        ttk.Button(bottom, text="설정 열기", command=self._open_config).pack(side="right", padx=(6, 0))
        ttk.Button(bottom, text="시스템 진단", command=self._start_diagnostics).pack(side="right", padx=6)

        self.notebook = nb

    def _build_ctk_ui(self):
        """customtkinter 기반 AI 제작사형 오피스 UI."""
        self._using_ctk = True
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#eef3fb")

        BG = "#eef3fb"
        CARD = "#fbfdff"
        SOFT = "#eaf2ff"
        TEXT = "#111827"
        MUTED = "#64748b"
        BLUE = "#3b82f6"
        BLUE_HOVER = "#2563eb"
        LIGHT_BTN = "#eef3f8"

        root = ctk.CTkFrame(self, fg_color=BG)
        root.pack(fill="both", expand=True)

        header = ctk.CTkFrame(root, fg_color="#101827", corner_radius=24)
        header.pack(fill="x", padx=12, pady=(10, 6))
        header.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.grid(row=0, column=0, sticky="we", padx=18, pady=10)
        ctk.CTkLabel(left, text="AI PRODUCTION COMPANY", text_color="#93c5fd",
                     font=("Malgun Gothic", 12, "bold")).pack(anchor="w")
        ctk.CTkLabel(left, text="오늘 영상, 회사 하나 굴리듯이 만들까요?",
                     text_color="#f8fafc", font=("Malgun Gothic", 22, "bold")).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(left, text="리서치팀, 작가팀, 썸네일팀, 검수팀이 한 번에 움직이는 제작실입니다.",
                     text_color="#cbd5e1", font=("Malgun Gothic", 12)).pack(anchor="w", pady=(5, 0))

        badge = ctk.CTkFrame(header, fg_color="#172235", corner_radius=20, border_width=1, border_color="#26354d")
        badge.grid(row=0, column=1, sticky="e", padx=18, pady=10)
        ctk.CTkLabel(badge, text="운영 시스템", text_color="#94a3b8",
                     font=("Malgun Gothic", 11, "bold")).pack(anchor="e", padx=14, pady=(8, 1))
        ctk.CTkLabel(badge, text="AI 제작팀 가동", text_color="#60a5fa",
                     font=("Malgun Gothic", 12, "bold")).pack(anchor="e", padx=14, pady=(0, 8))

        form = ctk.CTkFrame(root, fg_color=CARD, corner_radius=22, border_width=1, border_color="#e6edf7")
        form.pack(fill="x", padx=12, pady=(0, 6))
        for i in range(4):
            form.grid_columnconfigure(i, weight=1)

        def field_label(parent, text, row, col, **kw):
            ctk.CTkLabel(parent, text=text, text_color=MUTED,
                         font=("Malgun Gothic", 11, "bold")).grid(
                row=row, column=col, sticky="w", padx=10, pady=kw.get("pady", (8, 3)))

        def entry_style(widget, row, col, **kw):
            widget.grid(row=row, column=col, sticky="we", padx=10, pady=kw.get("pady", (0, 6)),
                        columnspan=kw.get("columnspan", 1))

        field_label(form, "종목", 0, 0, pady=(10, 3))
        self.preset_var = tk.StringVar(value="삼성전자")
        entry_style(ctk.CTkComboBox(form, variable=self.preset_var, values=list(PRESETS.keys()),
                                    command=lambda _v: self._on_preset(None),
                                    fg_color="#ffffff", border_color="#e6edf5",
                                    button_color="#f1f5f9", button_hover_color="#e2e8f0",
                                    text_color=TEXT, dropdown_fg_color="#ffffff", dropdown_text_color=TEXT,
                                    corner_radius=12, height=32), 1, 0)

        field_label(form, "종목명", 0, 1, pady=(10, 3))
        self.name_var = tk.StringVar(value="삼성전자")
        entry_style(ctk.CTkEntry(form, textvariable=self.name_var, fg_color="#ffffff",
                                 border_color="#e6edf5", corner_radius=14, text_color=TEXT,
                                 height=32), 1, 1)

        field_label(form, "코드 / 티커", 0, 2, pady=(10, 3))
        self.code_var = tk.StringVar(value="005930")
        entry_style(ctk.CTkEntry(form, textvariable=self.code_var, fg_color="#ffffff",
                                 border_color="#e6edf5", corner_radius=14, text_color=TEXT,
                                 height=32), 1, 2)

        field_label(form, "대본 날짜", 0, 3, pady=(10, 3))
        self.script_date_var = tk.StringVar(value="")
        date_row = ctk.CTkFrame(form, fg_color="transparent")
        date_row.grid(row=1, column=3, sticky="we", padx=10, pady=(0, 6))
        date_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(date_row, textvariable=self.script_date_var, fg_color="#ffffff",
                     border_color="#e6edf5", corner_radius=14, text_color=TEXT,
                     height=32, placeholder_text="예: 2026-07-10").grid(row=0, column=0, sticky="we", padx=(0, 6))
        self._add_ctk_button(date_row, "달력", self._open_calendar_popup,
                             fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=32).grid(row=0, column=1, sticky="e", padx=(0, 4))
        self._add_ctk_button(date_row, "오늘", lambda: self._set_quick_script_date("today"),
                             fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=32).grid(row=0, column=2, sticky="e", padx=(0, 4))
        self._add_ctk_button(date_row, "비움", self._clear_script_date, fg=LIGHT_BTN, hover="#e2e8f0",
                             text_color=TEXT, height=32).grid(row=0, column=3, sticky="e")

        field_label(form, "포맷", 2, 0)
        self.format_var = tk.StringVar(value="정프로용 개인채널 (친근형, 약 12,000자)")
        entry_style(ctk.CTkComboBox(form, variable=self.format_var, values=list(mr.SCRIPT_FORMATS.keys()),
                                    fg_color="#ffffff", border_color="#e6edf5",
                                    button_color="#f1f5f9", button_hover_color="#e2e8f0",
                                    text_color=TEXT, dropdown_fg_color="#ffffff", dropdown_text_color=TEXT,
                                    corner_radius=12, height=32), 3, 0, columnspan=2)

        field_label(form, "저장 폴더", 2, 2)
        self.out_var = tk.StringVar(value=mr.OUTPUT_DIR)
        entry_style(ctk.CTkEntry(form, textvariable=self.out_var, fg_color="#ffffff",
                                 border_color="#e6edf5", corner_radius=14, text_color=TEXT,
                                 height=32), 3, 2, columnspan=2)

        field_label(form, "주제/메모", 4, 0)
        self.custom_topic_var = tk.StringVar(value="")
        entry_style(ctk.CTkEntry(form, textvariable=self.custom_topic_var, fg_color="#ffffff",
                                 border_color="#e6edf5", corner_radius=14, text_color=TEXT,
                                 height=32,
                                 placeholder_text="비워두면 알아서 써요. 꼭 넣을 내용만 살짝 적어주세요."), 5, 0, columnspan=4)

        field_label(form, "관심종목", 6, 0)
        my_stocks_default = (str(mr._cfg.get("MY_STOCKS", "")).strip()
                             or "005930:삼성전자,000660:SK하이닉스,066570:LG전자")
        self.my_stocks_var = tk.StringVar(value=my_stocks_default)
        entry_style(ctk.CTkEntry(form, textvariable=self.my_stocks_var, fg_color="#ffffff",
                                 border_color="#e6edf5", corner_radius=14, text_color=TEXT,
                                 height=32), 7, 0, columnspan=3)
        self._add_ctk_button(form, "저장", self._save_my_stocks, fg=LIGHT_BTN, hover="#e2e8f0",
                             text_color=TEXT, height=32).grid(row=7, column=3, sticky="we", padx=10, pady=(0, 8))

        body = ctk.CTkFrame(root, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        body.grid_columnconfigure(0, weight=0, minsize=365)
        body.grid_columnconfigure(1, weight=1, minsize=680)
        body.grid_rowconfigure(0, weight=1)

        left_panel = ctk.CTkScrollableFrame(
            body,
            fg_color="transparent",
            width=365,
            scrollbar_button_color="#cbd5e1",
            scrollbar_button_hover_color="#94a3b8",
        )
        left_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 8))

        right_panel = ctk.CTkFrame(body, fg_color="transparent")
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_rowconfigure(0, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        work = ctk.CTkFrame(left_panel, fg_color="transparent")
        work.pack(fill="x", pady=(0, 8))
        work.grid_columnconfigure(0, weight=1)

        company = ctk.CTkFrame(work, fg_color="#0f172a", corner_radius=24, border_width=1, border_color="#26354d")
        company.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        company.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(company, text="AI 제작사 운영중", text_color="#f8fafc",
                     font=("Malgun Gothic", 15, "bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(company, text="종목 하나를 넣으면 부서별로 쪼개서 처리합니다.", text_color="#cbd5e1",
                     font=("Malgun Gothic", 10)).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))

        departments = ctk.CTkFrame(company, fg_color="#172235", corner_radius=16)
        departments.grid(row=2, column=0, sticky="we", padx=12, pady=(0, 12))
        departments.grid_columnconfigure((0, 1), weight=1)
        dept_items = [
            ("리서치팀", "뉴스·공시·수급"),
            ("작가팀", "구어체 대본"),
            ("디자인팀", "썸네일 문구/이미지"),
            ("검수팀", "숫자·국면 체크"),
        ]
        for idx, pair in enumerate(dept_items):
            r, c = divmod(idx, 2)
            cell = ctk.CTkFrame(departments, fg_color="#1f2a3d", corner_radius=12)
            cell.grid(row=r, column=c, sticky="we", padx=(8 if c == 0 else 4, 8 if c == 1 else 4), pady=(8 if r == 0 else 4, 8))
            ctk.CTkLabel(cell, text=pair[0], text_color="#bfdbfe", font=("Malgun Gothic", 10, "bold")).pack(anchor="w", padx=9, pady=(7, 0))
            ctk.CTkLabel(cell, text=pair[1], text_color="#94a3b8", font=("Malgun Gothic", 9)).pack(anchor="w", padx=9, pady=(0, 7))
        main = self._ctk_card(work, "🏢 오늘 제작 의뢰")
        main.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        main.grid_columnconfigure((0, 1), weight=1)
        self._add_ctk_button(main, "① 자료 수집", self._start_collect, fg=BLUE, hover=BLUE_HOVER, height=40).grid(row=1, column=0, padx=(8, 4), pady=(8, 5), sticky="we")
        self._add_ctk_button(main, "② 90점 대본", self._start_ai_generation, fg=BLUE, hover=BLUE_HOVER, height=40).grid(row=1, column=1, padx=(4, 8), pady=(8, 5), sticky="we")
        self._add_ctk_button(main, "⚡ 빠른 초안", self._start_fast_ai_generation, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=36).grid(row=2, column=0, padx=(8, 4), pady=(0, 5), sticky="we")
        self._add_ctk_button(main, "✍ 썸네일 문구", self._start_thumbnail_copy, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=36).grid(row=2, column=1, padx=(4, 8), pady=(0, 5), sticky="we")
        self._add_ctk_button(main, "🎨 썸네일 이미지", self._start_thumbnail_image, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=36).grid(row=3, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="we")

        batch = self._ctk_card(work, "🏭 멀티 제작 라인")
        batch.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        batch.grid_columnconfigure((0, 1), weight=1)
        self._add_ctk_button(batch, "전체 자료", self._start_batch_collect, fg=BLUE, hover=BLUE_HOVER, height=38).grid(row=1, column=0, padx=(8, 4), pady=(8, 5), sticky="we")
        self._add_ctk_button(batch, "90점 전체", self._start_batch_scripts, fg=BLUE, hover=BLUE_HOVER, height=38).grid(row=1, column=1, padx=(4, 8), pady=(8, 5), sticky="we")
        self._add_ctk_button(batch, "빠른 전체", self._start_batch_fast_scripts, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=2, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="we")
        ctk.CTkLabel(batch, text="90점 전체는 품질 우선, 빠른 전체는 초안 속도 우선입니다.",
                     text_color=MUTED, font=("Malgun Gothic", 10)).grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))

        angle = self._ctk_card(work, "🧭 기획 회의실")
        angle.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        angle.grid_columnconfigure((0, 1), weight=1)
        self._add_ctk_button(angle, "후보 보기", self._start_angles, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=1, column=0, padx=(8, 4), pady=(8, 8), sticky="we")
        self._add_ctk_button(angle, "선택 대본", self._start_script_with_angle, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=1, column=1, padx=(4, 8), pady=(8, 8), sticky="we")

        discover = self._ctk_card(work, "🔎 리서치 본부")
        discover.grid(row=4, column=0, sticky="nsew")
        discover.grid_columnconfigure((0, 1), weight=1)
        self._add_ctk_button(discover, "내 종목 자동", self._start_auto_pipeline_watchlist, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=1, column=0, padx=(8, 4), pady=(8, 5), sticky="we")
        self._add_ctk_button(discover, "시장 자동", self._start_auto_pipeline, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=1, column=1, padx=(4, 8), pady=(8, 5), sticky="we")
        self._add_ctk_button(discover, "내 종목 스캔", self._start_watchlist_scan, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=2, column=0, padx=(8, 4), pady=5, sticky="we")
        self._add_ctk_button(discover, "시장 스캔", self._start_scan, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=2, column=1, padx=(4, 8), pady=5, sticky="we")
        self._add_ctk_button(discover, "소재 선정", self._start_topic_ai, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=3, column=0, padx=(8, 4), pady=(5, 8), sticky="we")
        self._add_ctk_button(discover, "1순위 대본", self._start_script_from_topic, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=35).grid(row=3, column=1, padx=(4, 8), pady=(5, 8), sticky="we")

        self.status_var = tk.StringVar(value="AI 제작사 대기 중입니다. 종목을 주시면 리서치팀부터 움직입니다.")
        self.status_label = ctk.CTkLabel(left_panel, textvariable=self.status_var, text_color="#1d4ed8",
                                         fg_color="#dbeafe", corner_radius=18,
                                         font=("Malgun Gothic", 12, "bold"), anchor="w")
        self.status_label.pack(fill="x", pady=(0, 6), ipady=6)
        self.progress = ctk.CTkProgressBar(left_panel, mode="indeterminate", progress_color="#60a5fa")

        tabview = ctk.CTkTabview(right_panel, fg_color=CARD, segmented_button_fg_color="#edf2f7",
                                 segmented_button_selected_color=BLUE,
                                 segmented_button_selected_hover_color=BLUE_HOVER,
                                 segmented_button_unselected_color="#f1f4f8",
                                 segmented_button_unselected_hover_color="#e6edf5",
                                 text_color=TEXT, corner_radius=20, anchor="w")
        tabview.grid(row=0, column=0, sticky="nsew")
        nb = _CTkTabAdapter(tabview)

        self._build_company_map_tab(nb)
        self.ai_result_text = self._make_text_tab(nb, "📄 대본")
        self.thumbnail_text = self._make_text_tab(nb, "🎯 썸네일")
        self.data_text = self._make_text_tab(nb, "📊 자료")
        self.topic_text = self._make_text_tab(nb, "💡 소재")
        self.log_text = self._make_text_tab(nb, "🧾 로그")
        self._build_guide_tab(nb)

        hidden_text_parent = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.script_prompt_text = self._make_hidden_text_box(hidden_text_parent)
        self.ai_report_text = self._make_hidden_text_box(hidden_text_parent)
        self.report_prompt_text = self._make_hidden_text_box(hidden_text_parent)
        self.scan_text = self._make_hidden_text_box(hidden_text_parent)
        self.telegram_text = self._make_hidden_text_box(hidden_text_parent)
        try:
            nb.select(self.ai_result_text)
        except Exception:
            pass

        bottom = ctk.CTkFrame(root, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=(0, 8))
        for text, cmd in [
            ("대본 복사", lambda: self._copy(self.ai_result_text)),
            ("탭 저장", self._save_current_tab),
            ("파일 열기", self._open_last_result_file),
            ("폴더 열기", self._open_output_folder),
            ("자료 저장", lambda: self._save_widget(self.data_text, "수집데이터")),
            ("대본+썸네일", self._copy_script_and_thumbnail),
            ("주제 붙여넣기", self._paste_topic_memo),
            ("주제 비우기", self._clear_topic_memo),
            ("텔레그램 보기", self._preview_telegram),
            ("텔레그램 전송", self._send_telegram),
        ]:
            self._add_ctk_button(bottom, text, cmd, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=30).pack(side="left", padx=(0, 3))
        self._add_ctk_button(bottom, "설정", self._open_config, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=30).pack(side="right", padx=(4, 0))
        self._add_ctk_button(bottom, "진단", self._start_diagnostics, fg=LIGHT_BTN, hover="#e2e8f0", text_color=TEXT, height=30).pack(side="right", padx=4)

        self.notebook = nb

    def _ctk_card(self, parent, title):
        frame = ctk.CTkFrame(parent, fg_color="#fbfdff", corner_radius=20, border_width=1, border_color="#e6edf7")
        ctk.CTkLabel(frame, text=title, text_color="#111827",
                     font=("Malgun Gothic", 13, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(9, 0))
        return frame

    def _make_hidden_text_box(self, parent):
        text = ctk.CTkTextbox(parent, wrap="word", font=("Malgun Gothic", 12),
                              fg_color="#ffffff", text_color="#202632",
                              border_width=1, border_color="#edf0f4",
                              corner_radius=16)
        self._text_widgets.append(text)
        return text

    def _add_ctk_button(self, parent, text, command, fg="#4f8cff", hover="#2f72f0", text_color="#ffffff", height=38):
        btn = ctk.CTkButton(parent, text=text, command=command, fg_color=fg,
                            hover_color=hover, text_color=text_color, height=height,
                            corner_radius=13, font=("Malgun Gothic", 11, "bold"))
        self.action_buttons.append(btn)
        return btn

    def _custom_topic(self):
        base = self.custom_topic_var.get().strip()
        date_context = self._script_date_context()
        if date_context and base:
            return f"{date_context}\n\n[사용자 메모]\n{base}".strip()
        return (date_context or base).strip()

    def _clear_script_date(self):
        if hasattr(self, "script_date_var"):
            self.script_date_var.set("")
            self.status_var.set("대본 날짜를 비웠습니다. 오늘 기준으로 작성됩니다.")
            self._log("대본 날짜 비움")

    def _set_quick_script_date(self, mode):
        from datetime import date, timedelta
        today = date.today()
        if mode == "today":
            target = today
            label = "오늘"
        elif mode == "tomorrow":
            target = today + timedelta(days=1)
            label = "내일"
        elif mode == "next_monday":
            days = (0 - today.weekday()) % 7
            if days == 0:
                days = 7
            target = today + timedelta(days=days)
            label = "다음 월요일"
        elif mode == "this_friday":
            days = (4 - today.weekday()) % 7
            target = today + timedelta(days=days)
            label = "이번 금요일" if days else "오늘 금요일"
        else:
            target = today
            label = "오늘"
        self.script_date_var.set(target.isoformat())
        self.status_var.set(f"대본 날짜를 {label}({target.isoformat()})로 설정했습니다.")
        self._log(f"대본 날짜 설정: {label}({target.isoformat()})")

    def _open_calendar_popup(self):
        """대본 기준 날짜를 달력에서 고르는 작은 창"""
        from datetime import date

        raw = getattr(self, "script_date_var", tk.StringVar(value="")).get().strip()
        base = date.today()
        if raw:
            try:
                y, m, d = map(int, raw.replace(".", "-").replace("/", "-").split("-"))
                base = date(y, m, d)
            except Exception:
                base = date.today()

        popup = tk.Toplevel(self)
        popup.title("대본 날짜 선택")
        popup.resizable(False, False)
        popup.transient(self)
        popup.grab_set()
        popup.configure(bg="#ffffff")

        try:
            x = self.winfo_rootx() + 220
            y = self.winfo_rooty() + 170
            popup.geometry(f"+{x}+{y}")
        except Exception:
            pass

        holder = ttk.Frame(popup, padding=14)
        holder.grid(row=0, column=0, sticky="nsew")
        self._draw_calendar_popup(popup, holder, base.year, base.month)

    def _draw_calendar_popup(self, popup, holder, year, month):
        from datetime import date

        for child in holder.winfo_children():
            child.destroy()

        def move_month(delta):
            new_month = month + delta
            new_year = year
            if new_month < 1:
                new_month = 12
                new_year -= 1
            elif new_month > 12:
                new_month = 1
                new_year += 1
            self._draw_calendar_popup(popup, holder, new_year, new_month)

        def pick_day(day):
            selected = date(year, month, day).isoformat()
            self.script_date_var.set(selected)
            self.status_var.set(f"대본 날짜를 {selected}로 설정했습니다.")
            self._log(f"달력에서 대본 날짜 선택: {selected}")
            popup.destroy()

        header = ttk.Frame(holder)
        header.grid(row=0, column=0, columnspan=7, sticky="we", pady=(0, 10))
        ttk.Button(header, text="‹", width=4, command=lambda: move_month(-1)).pack(side="left")
        ttk.Label(header, text=f"{year}년 {month}월", anchor="center",
                  font=("Malgun Gothic", 12, "bold")).pack(side="left", expand=True, fill="x", padx=10)
        ttk.Button(header, text="›", width=4, command=lambda: move_month(1)).pack(side="right")

        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        for col, name in enumerate(weekdays):
            ttk.Label(holder, text=name, anchor="center",
                      font=("Malgun Gothic", 9, "bold")).grid(row=1, column=col, padx=2, pady=(0, 4), sticky="we")

        today = date.today()
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
        for r, week in enumerate(weeks, start=2):
            for c, day in enumerate(week):
                if day == 0:
                    ttk.Label(holder, text="", width=5).grid(row=r, column=c, padx=2, pady=2)
                    continue
                label = f"{day}"
                if year == today.year and month == today.month and day == today.day:
                    label = f"오늘\n{day}"
                ttk.Button(holder, text=label, width=5,
                           command=lambda d=day: pick_day(d)).grid(row=r, column=c, padx=2, pady=2)

        bottom = ttk.Frame(holder)
        bottom.grid(row=8, column=0, columnspan=7, sticky="we", pady=(12, 0))
        ttk.Button(bottom, text="오늘", command=lambda: (self._set_quick_script_date("today"), popup.destroy())).pack(side="left")
        ttk.Button(bottom, text="비우기", command=lambda: (self._clear_script_date(), popup.destroy())).pack(side="left", padx=6)
        ttk.Button(bottom, text="닫기", command=popup.destroy).pack(side="right")

    def _script_date_context(self):
        raw = getattr(self, "script_date_var", tk.StringVar(value="")).get().strip()
        if not raw:
            return ""
        import re
        from datetime import date
        norm = raw.replace(".", "-").replace("/", "-").strip()
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", norm)
        if not m:
            return f"""
[대본 기준 날짜]
- 사용자가 대본 날짜로 "{raw}"를 입력했다.
- 날짜 형식이 불명확하므로 실제 날짜·주가·수급을 새로 만들지 말고, 사용자 메모와 수집 데이터 기준으로만 작성하라.
- 최종 대본에는 "날짜 형식이 불명확하다", "사용자가 입력했다" 같은 제작 과정 표현을 쓰지 마라.
""".strip()
        y, mo, d = map(int, m.groups())
        try:
            target = date(y, mo, d)
        except ValueError:
            return f"""
[대본 기준 날짜]
- 사용자가 대본 날짜로 "{raw}"를 입력했다.
- 유효하지 않은 날짜이므로 실제 날짜·주가·수급을 새로 만들지 말고, 사용자 메모와 수집 데이터 기준으로만 작성하라.
- 최종 대본에는 "유효하지 않은 날짜" 같은 제작 과정 표현을 쓰지 마라.
""".strip()
        today = date.today()
        weekday_names = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        weekday_name = weekday_names[target.weekday()]
        is_weekend = target.weekday() >= 5
        if target > today:
            weekend_extra = ""
            if is_weekend:
                weekend_extra = f"""
- 대본 기준 날짜는 {weekday_name}이다. 한국 정규장이 열리는 날처럼 쓰지 마라.
- 주말용 대본으로 처리하라. 수집일 당일 브리핑이 아니라, 주말에 볼 큰 질문을 정리하는 대본이어야 한다.
- 화요일에 미리 뽑더라도 화요일 장중·오늘 하루 이야기가 중심이 되면 실패다.
- 현재까지 수집된 당일 데이터는 주말 기준 최신 자료가 아니다. 금요일 확정 전 임시 스냅샷으로만 취급하라.
- 화요일·수요일·목요일 자료로 이번 주 결론을 낸 것처럼 쓰지 마라. 주말 기준 최신 자료는 금요일 확정 데이터라는 점을 전제로 둔다.
- 구성 비중은 주간 뉴스 헤드라인 35%, 다음 주 일정·실적발표·경제지표 35%, 이번 주가 남긴 질문 30% 정도로 잡아라.
- "오늘 장이 열리면", "오늘 장중", "오늘 현재가", "오늘 종가", "오늘 수급"처럼 해당 날짜에 장이 열려야 가능한 표현을 쓰지 마라.
""".strip()
            return f"""
[대본 기준 날짜]
- 대본 기준 날짜: {target.isoformat()} ({weekday_name}).
- 이 날짜는 아직 오지 않은 미래 날짜다. 따라서 그날의 주가, 현재가, 종가, 장중 흐름, 수급, 공매도, 외국인 지분율이 이미 확인된 것처럼 말하지 마라.
- 현재까지 수집된 확정 데이터와 사용자가 적은 일정·메모만 근거로, 그날 시청자가 봐야 할 큰 흐름과 중요 일정, 가능한 시나리오를 중심으로 대본을 써라.
- 주제는 작게 쪼개지 말고 크게 잡아라. 예: 실적발표가 시장의 돈 방향을 바꿀 수 있는지, 금리·환율·섹터 흐름이 종목에 어떤 압력을 줄지, 그날 첫 확인 기준은 무엇인지.
- 최종 대본에는 "미래 날짜", "아직 오지 않은 날짜", "예약 작성", "사전에 작성" 같은 표현을 쓰지 마라.
- 마치 해당 날짜에 시청자에게 자연스럽게 설명하는 대본처럼 쓰되, 확인되지 않은 당일 결과를 본 척하지 마라.
{weekend_extra}
""".strip()
        return f"""
[대본 기준 날짜]
- 대본 기준 날짜: {target.isoformat()} ({weekday_name}).
- 수집 데이터의 시점과 기준 날짜가 다를 수 있으므로, 날짜가 다른 데이터는 확정된 과거 데이터로만 다뤄라.
- 최종 대본에는 "사용자가 날짜를 입력했다" 같은 제작 과정 표현을 쓰지 마라.
""".strip()

    def _build_company_map_tab(self, notebook):
        """AI 제작사 흐름을 한눈에 보여주는 오피스 맵 탭."""
        title = "🏢 회사맵" if isinstance(notebook, _CTkTabAdapter) else "🏢 회사맵"
        if isinstance(notebook, _CTkTabAdapter):
            frame = notebook.add_tab(title)
            frame.configure(fg_color="#0f172a")
        else:
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=title)

        canvas = tk.Canvas(frame, bg="#0f172a", highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=6, pady=6)

        def draw_map(event=None):
            canvas.delete("all")
            w = max(canvas.winfo_width(), 900)
            h = max(canvas.winfo_height(), 520)
            canvas.create_rectangle(0, 0, w, h, fill="#0f172a", outline="")
            canvas.create_text(28, 26, anchor="w", text="AI PRODUCTION COMPANY MAP",
                               fill="#93c5fd", font=("Malgun Gothic", 12, "bold"))
            canvas.create_text(28, 55, anchor="w", text="종목 하나가 들어오면, 부서들이 순서대로 움직여 영상 하나를 완성합니다.",
                               fill="#cbd5e1", font=("Malgun Gothic", 11))
            canvas.create_text(w - 28, 28, anchor="e", text="운영 상태  ·  대기중",
                               fill="#bfdbfe", font=("Malgun Gothic", 10, "bold"))

            rooms = [
                ("기획실", "주제/각도 선정", "소재를 고르고 오늘의 질문을 잡습니다.", 0.07, 0.16, 0.32, 0.37, "#f59e0b"),
                ("리서치 본부", "뉴스·공시·수급", "원자료를 모으고 가격/국면을 분리합니다.", 0.08, 0.48, 0.36, 0.74, "#22c55e"),
                ("작가팀", "구어체 대본", "후킹, 흐름, CTA 연결까지 방송 말투로 씁니다.", 0.42, 0.18, 0.66, 0.43, "#60a5fa"),
                ("검수팀", "숫자·표현 체크", "창작 수치, 장중/마감 혼동, 과장 표현을 걸러냅니다.", 0.43, 0.56, 0.68, 0.80, "#a78bfa"),
                ("디자인실", "썸네일 문구/이미지", "대본의 핵심 갈등을 클릭 문구와 이미지로 바꿉니다.", 0.74, 0.23, 0.94, 0.52, "#f472b6"),
                ("출고 데스크", "저장·복사·폴더", "대본과 썸네일을 바로 업로드 준비 상태로 넘깁니다.", 0.74, 0.62, 0.94, 0.84, "#2dd4bf"),
            ]

            centers = {}
            for name, role, desc, x1p, y1p, x2p, y2p, color in rooms:
                x1, y1, x2, y2 = int(w*x1p), int(h*y1p), int(w*x2p), int(h*y2p)
                centers[name] = ((x1+x2)//2, (y1+y2)//2)
                canvas.create_rectangle(x1+5, y1+7, x2+5, y2+7, fill="#07111f", outline="")
                canvas.create_rectangle(x1, y1, x2, y2, fill="#172235", outline="#334155", width=1, dash=(5, 4))
                canvas.create_rectangle(x1+14, y1+14, x1+52, y1+52, fill=color, outline="", width=0)
                canvas.create_text(x1+33, y1+33, text="AI", fill="#0f172a", font=("Malgun Gothic", 10, "bold"))
                canvas.create_text(x1+66, y1+18, anchor="nw", text=name, fill="#f8fafc", font=("Malgun Gothic", 13, "bold"))
                canvas.create_text(x1+66, y1+43, anchor="nw", text=role, fill=color, font=("Malgun Gothic", 10, "bold"))
                canvas.create_text(x1+18, y2-34, anchor="nw", text=desc, fill="#cbd5e1", font=("Malgun Gothic", 9), width=max(120, x2-x1-35))
                canvas.create_rectangle(x2-72, y1+14, x2-15, y1+38, fill="#0f172a", outline="#334155")
                canvas.create_text(x2-43, y1+26, text="대기", fill="#93c5fd", font=("Malgun Gothic", 9, "bold"))

            def connect(a, b, color="#64748b"):
                ax, ay = centers[a]
                bx, by = centers[b]
                canvas.create_line(ax, ay, bx, by, fill=color, width=2, dash=(6, 6), arrow=tk.LAST)

            connect("기획실", "작가팀", "#f59e0b")
            connect("리서치 본부", "작가팀", "#22c55e")
            connect("작가팀", "검수팀", "#60a5fa")
            connect("검수팀", "디자인실", "#a78bfa")
            connect("디자인실", "출고 데스크", "#f472b6")
            connect("검수팀", "출고 데스크", "#2dd4bf")

            canvas.create_rectangle(24, h-56, w-24, h-20, fill="#111827", outline="#253149")
            canvas.create_text(42, h-38, anchor="w", text="사용 흐름  ① 자료 수집  →  ② 90점 대본  →  ③ 썸네일  →  파일 열기/복사",
                               fill="#bfdbfe", font=("Malgun Gothic", 10, "bold"))

        canvas.bind("<Configure>", draw_map)
        canvas.after(80, draw_map)
        self.company_map_canvas = canvas
    def _build_guide_tab(self, notebook):
        """첫 탭: 상황별 사용 순서 안내"""
        guide = """
╔══════════════════════════════════════════════════════════════╗
   📖 사용법 — 오늘 상황에 맞는 줄 하나만 따라가세요
╚══════════════════════════════════════════════════════════════╝

🅰  다룰 종목이 정해진 날 (예: "오늘은 무조건 삼성전자")
    상단에 종목명·코드 입력 → [① 자료 수집] → [② 대본 생성] → [③ 썸네일 문구]
    · 미국 주식도 됨: 종목코드에 TSLA, NVDA 처럼 티커 입력
    · 실적발표/공시/뉴스처럼 꼭 다룰 주제가 있으면 상단 '주제/메모'에 적고 실행

🅱  같은 종목을 매일 다루는데 대본이 비슷해질 때  ★추천 루틴★
    [① 자료 수집] → [흐름 후보 보기] → 흐름 후보 4개 확인 → [선택 흐름으로 대본]
    · 같은 자료에서 매일 다른 흐름(수급/내부자/공매도/밸류)로 뽑는 기능
    · 흐름 후보는 '소재/흐름' 탭에 표시됨

🅲  내가 정한 종목들 중에서 오늘 제일 이야기되는 걸로
    상단 '관심종목'에 후보 입력 → [내 종목 자동] 클릭 (끝)
    · 수동으로 하려면: [내 종목 스캔] → [AI 소재 선정] → [1순위로 대본]

🅳  오늘 뭘 다룰지 전혀 모를 때 (시장 전체에서 발굴)
    [시장 전체 자동] 클릭 (끝)
    · 수동으로 하려면: [시장 스캔] → [AI 소재 선정] → [1순위로 대본]

──────────────────────────────────────────────────────────────
📤 공유:  하단 [텔레그램 미리보기] → [텔레그램 보내기]
           (수집 데이터의 핵심만 추린 다이제스트가 알림센터로 감)

🖼 썸네일: 대본 생성 완료 → [③ 썸네일 문구] → 대본 맞춤 후보 8개 확인

💾 결과:  하단 [결과 파일 열기] = 방금 생성된 대본 파일
           하단 [저장 폴더 열기] = 지금까지의 모든 결과물

⚙️ 설정:  하단 [설정 열기] — API 키, 관심종목,
           텔레그램, 알림봇 감시목록 전부 여기서 관리

💡 팁
  · 대본 포맷(정프로용/이면추적/장마감 등)은 상단 드롭다운에서 선택
  · '관심종목'은 [저장]을 눌러야 다음 실행에도 유지됨
  · 알림봇은 별도 실행: python market_alert_bot.py (24시간 감시·브리핑)
  · 업로드 전 훅 숫자·장중/확정 데이터 표현만 30초 눈검수 — 마지막 안전핀
"""
        if isinstance(notebook, _CTkTabAdapter):
            frame = notebook.add_tab("사용법")
            text = ctk.CTkTextbox(frame, wrap="word", font=("Malgun Gothic", 12),
                                  fg_color="#ffffff", text_color="#202632",
                                  border_width=1, border_color="#edf0f4",
                                  corner_radius=16)
            text.pack(fill="both", expand=True, padx=10, pady=10)
            text.insert("1.0", guide.strip("\n"))
            text.configure(state="disabled")
            self._text_widgets.append(text)
            return

        frame = ttk.Frame(notebook)
        notebook.add(frame, text="📖 사용법")
        text = tk.Text(frame, wrap="word", font=("Malgun Gothic", 10),
                       padx=14, pady=10, relief="flat")
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", guide.strip("\n"))
        text.configure(state="disabled")  # 읽기 전용
        self._text_widgets.append(text)

    def _apply_theme(self):
        if getattr(self, "_using_ctk", False):
            return
        """토스형을 조금 더 말랑하게 만든 밝은 파스텔 테마."""
        BG = "#fbfbfd"
        SURFACE = "#ffffff"
        PANEL = "#ffffff"
        PANEL_2 = "#f4f7fb"
        FIELD = "#ffffff"
        FG = "#202632"
        MUT = "#5f6b7a"
        SUBTLE = "#9aa3b2"
        ACC = "#5b8def"
        ACC_2 = "#3578f6"
        GREEN = "#20c997"
        BORDER = "#edf0f4"

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.configure(bg=BG)
        style.configure(".", background=BG, foreground=FG,
                        fieldbackground=FIELD, bordercolor=BORDER,
                        lightcolor=BG, darkcolor=BG, troughcolor=PANEL_2)
        style.configure("App.TFrame", background=BG)
        style.configure("TFrame", background=BG)
        style.configure("Bottom.TFrame", background=BG)
        style.configure("Hero.TFrame", background=SURFACE, relief="flat")

        style.configure("TLabel", background=BG, foreground=FG, font=("Malgun Gothic", 10))
        style.configure("Field.TLabel", background=BG, foreground=MUT,
                        font=("Malgun Gothic", 9, "bold"))
        style.configure("Eyebrow.TLabel", background=SURFACE, foreground=ACC_2,
                        font=("Malgun Gothic", 10, "bold"))
        style.configure("Title.TLabel", background=SURFACE, foreground="#ffffff",
                        font=("Malgun Gothic", 22, "bold"))
        style.configure("Title.TLabel", background=SURFACE, foreground=FG,
                        font=("Malgun Gothic", 22, "bold"))
        style.configure("HeaderHint.TLabel", background=SURFACE, foreground=MUT,
                        font=("Malgun Gothic", 10))
        style.configure("BadgeTitle.TLabel", background=SURFACE, foreground=SUBTLE,
                        font=("Malgun Gothic", 9, "bold"))
        style.configure("Badge.TLabel", background="#f0f6ff", foreground=ACC_2,
                        padding=(16, 9), font=("Malgun Gothic", 10, "bold"))
        style.configure("Hint.TLabel", background=BG, foreground="#8b95a6",
                        font=("Malgun Gothic", 9, "bold"))
        style.configure("Status.TLabel", background="#f0f6ff", foreground=ACC_2,
                        font=("Malgun Gothic", 10, "bold"))

        style.configure("TLabelframe", background=BG, bordercolor=BORDER,
                        relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=BG, foreground=FG,
                        font=("Malgun Gothic", 10, "bold"))
        style.configure("Card.TLabelframe", background=BG, bordercolor=BORDER,
                        relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=BG, foreground=FG,
                        font=("Malgun Gothic", 10, "bold"))
        style.configure("PrimaryCard.TLabelframe", background=BG, bordercolor=ACC,
                        relief="solid", borderwidth=1)
        style.configure("PrimaryCard.TLabelframe.Label", background=BG, foreground=ACC_2,
                        font=("Malgun Gothic", 11, "bold"))

        style.configure("TButton", background=PANEL_2, foreground=FG,
                        borderwidth=0, focusthickness=0, padding=(16, 10),
                        font=("Malgun Gothic", 10, "bold"))
        style.map("TButton",
                  background=[("disabled", "#f4f7fb"), ("pressed", "#e2e8f0"), ("active", "#edf3fb")],
                  foreground=[("disabled", "#b0b8c1"), ("active", FG)])

        style.configure("Accent.TButton", background=ACC, foreground="#ffffff",
                        borderwidth=0, focusthickness=0, padding=(20, 15),
                        font=("Malgun Gothic", 12, "bold"))
        style.map("Accent.TButton",
                  background=[("disabled", "#d7e4ff"), ("pressed", "#3f73d8"), ("active", "#4f80e6")],
                  foreground=[("disabled", "#ffffff"), ("active", "#ffffff")])

        style.configure("Toss.TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
        style.configure("Toss.TNotebook.Tab", background=BG, foreground=SUBTLE,
                        padding=(16, 9), borderwidth=0,
                        font=("Malgun Gothic", 10, "bold"))
        style.map("Toss.TNotebook.Tab",
                  background=[("selected", "#ffffff"), ("active", "#f0f6ff")],
                  foreground=[("selected", ACC_2), ("active", ACC_2)])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=SUBTLE,
                        padding=(16, 9), borderwidth=0,
                        font=("Malgun Gothic", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", "#ffffff"), ("active", "#f0f6ff")],
                  foreground=[("selected", ACC_2), ("active", ACC_2)])

        style.configure("TEntry", fieldbackground=FIELD, foreground=FG,
                        insertcolor=FG, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, padding=8, relief="flat")
        style.configure("TCombobox", fieldbackground=FIELD, foreground=FG,
                        background=FIELD, arrowcolor=ACC, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=8, relief="flat")
        style.map("TCombobox",
                  fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", FG)],
                  selectbackground=[("readonly", FIELD)],
                  selectforeground=[("readonly", FG)])
        style.configure("Horizontal.TProgressbar", background=GREEN,
                        troughcolor=PANEL_2, borderwidth=0, thickness=5)

        self.option_add("*TCombobox*Listbox*Background", FIELD)
        self.option_add("*TCombobox*Listbox*Foreground", FG)
        self.option_add("*TCombobox*Listbox*selectBackground", ACC)
        self.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
        self.option_add("*Font", ("Malgun Gothic", 10))

        # tk.Text 위젯들은 ttk 테마 밖이라 직접 칠함 (양쪽 테마 공통)
        for t in self._text_widgets:
            t.configure(bg="#ffffff", fg=FG, insertbackground=FG,
                        selectbackground="#e7f0ff", selectforeground=FG,
                        relief="flat", borderwidth=0,
                        highlightbackground=BORDER, highlightcolor=ACC,
                        highlightthickness=1, padx=18, pady=14,
                        font=("Malgun Gothic", 11), spacing1=2, spacing3=4)
        try:
            self.status_label.configure(foreground=MUT)
        except (AttributeError, tk.TclError):
            pass

    def _add_button(self, parent, text, command, accent=False):
        btn = ttk.Button(parent, text=text, command=command,
                         style="Accent.TButton" if accent else "TButton")
        self.action_buttons.append(btn)
        return btn

    def _make_text_tab(self, notebook, title):
        if isinstance(notebook, _CTkTabAdapter):
            frame = notebook.add_tab(title)
            text = ctk.CTkTextbox(frame, wrap="word", font=("Malgun Gothic", 12),
                                  fg_color="#ffffff", text_color="#202632",
                                  border_width=1, border_color="#edf0f4",
                                  corner_radius=16)
            text.pack(fill="both", expand=True, padx=6, pady=6)
            self._text_widgets.append(text)
            return text

        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = tk.Text(frame, wrap="word", font=("Malgun Gothic", 10), padx=10, pady=8)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        self._text_widgets.append(text)
        return text

    def _check_credentials(self):
        missing = []
        if not (mr.KRX_ID and mr.KRX_PW):
            missing.append("KRX 계정")
        if not mr.DART_API_KEY:
            missing.append("DART 키")
        if not mr.OPENAI_API_KEY:
            missing.append("OpenAI API 키")
        if not (mr.TELEGRAM_BOT_TOKEN and mr.TELEGRAM_CHAT_ID):
            missing.append("텔레그램")
        if missing:
            self.status_var.set("설정 필요: " + ", ".join(missing) + " — 설정 버튼으로 config.txt를 열어 입력하세요.")

    def _on_preset(self, _event=None):
        name = self.preset_var.get()
        if name != "직접 입력":
            self.name_var.set(name)
            self.code_var.set(PRESETS[name])
        else:
            self.name_var.set("")
            self.code_var.set("")

    def _get_stock_inputs(self):
        name = self.name_var.get().strip()
        code = self.code_var.get().strip()
        if not name or not code:
            messagebox.showwarning("입력 필요", "종목명과 종목코드를 입력하세요.\n"
                                   "국내: 6자리 코드 (예: 005930) / 미국: 티커 (예: TSLA)")
            return None, None
        if code.isdigit():
            code = code.zfill(6)      # 국내: 앞자리 0 보정
        else:
            code = code.upper()       # 미국 티커: TSLA, NVDA, BRK-B 등
        self.code_var.set(code)
        return name, code

    def _start_collect(self):
        name, code = self._get_stock_inputs()
        if not name:
            return
        fmt_name = self.format_var.get()
        self._set_running(True, f"'{name}' 실시간 데이터 새로 수집 중입니다.")
        threading.Thread(target=self._collect, args=(name, code, fmt_name), daemon=True).start()

    def _collect(self, name, code, fmt_name):
        try:
            custom_topic = self._custom_topic()
            if mr.is_information_script_request(format_name=fmt_name, custom_topic=custom_topic):
                raw = mr.build_information_raw_data(name, code, force=True)
            elif mr.is_weekend_script_request(format_name=fmt_name, custom_topic=custom_topic):
                raw = mr.build_weekend_raw_data(name, code, force=True)
            else:
                raw = mr.build_raw_data(name, code, force=True)
            report_prompt = mr.REPORT_PROMPT_TEMPLATE.format(raw_data=raw)
            script_prompt, _ = mr.build_script_prompt(
                raw_data=raw,
                format_name=fmt_name,
                custom_topic=custom_topic,
            )
            digest = mr.make_telegram_digest(name, code, raw)
            self.result_queue.put(("collect_ok", raw, report_prompt, script_prompt, digest, name, code))
        except Exception as e:
            self.result_queue.put(("error", f"자료 수집 실패: {type(e).__name__}: {e}"))

    def _start_ai_generation(self):
        if not self._require_raw():
            return
        if not mr.OPENAI_API_KEY or not getattr(mr, "GEMINI_API_KEY", ""):
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY와 GEMINI_API_KEY를 모두 입력하세요.")
            return
        fmt_name = self.format_var.get()
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        custom_topic = self._custom_topic()
        self._set_running(True, "OpenAI 초안 → Gemini 다듬기 → OpenAI 점검 → Gemini 최종 출력 중입니다.")
        threading.Thread(target=self._generate_ai_script, args=(fmt_name, out_dir, custom_topic), daemon=True).start()

    def _start_fast_ai_generation(self):
        if not self._require_raw():
            return
        if not mr.OPENAI_API_KEY and not getattr(mr, "GEMINI_API_KEY", ""):
            messagebox.showerror("API key", "Set OPENAI_API_KEY or GEMINI_API_KEY in config.txt.")
            return
        fmt_name = self.format_var.get()
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        custom_topic = self._custom_topic()
        engine = "fast_openai" if mr.OPENAI_API_KEY else "fast_gemini"
        self._set_running(True, f"Fast script mode running. ({engine})")
        threading.Thread(target=self._generate_ai_script, args=(fmt_name, out_dir, custom_topic, engine), daemon=True).start()

    def _generate_ai_script(self, fmt_name, out_dir, custom_topic="", engine="chain"):
        try:
            raw_data = self.last_raw
            if mr.is_information_script_request(format_name=fmt_name, custom_topic=custom_topic):
                if "날짜무관 자료 수집" not in str(raw_data or ""):
                    raw_data = mr.build_information_raw_data(self.last_name, self.last_code, force=True)
                    self.last_raw = raw_data
            elif mr.is_weekend_script_request(format_name=fmt_name, custom_topic=custom_topic):
                if "주말용 사전작성 자료 수집" not in str(raw_data or ""):
                    raw_data = mr.build_weekend_raw_data(self.last_name, self.last_code, force=True)
                    self.last_raw = raw_data
            result = mr.generate_ai_script(
                stock_name=self.last_name,
                stock_code=self.last_code,
                format_name=fmt_name,
                raw_data=raw_data,
                output_dir=out_dir,
                save=True,
                custom_topic=custom_topic,
                engine=engine,
            )
            self.result_queue.put(("ai_script_ok", result["text"], result["path"], result.get("stats", {})))
        except Exception as e:
            self.result_queue.put(("error", f"AI 대본 생성 실패: {e}"))

    def _watchlist_items_for_batch(self, max_items=10):
        stocks_raw = self.my_stocks_var.get().strip()
        if not stocks_raw:
            messagebox.showinfo(
                "관심종목 없음",
                "'관심종목' 칸에 여러 종목을 입력하세요.\n예: 005930:삼성전자,000660:SK하이닉스,066570:LG전자",
            )
            return []
        try:
            items = mr._parse_watchlist_cfg(stocks_raw, max_top=max_items)
        except Exception:
            items = []
        if not items:
            messagebox.showwarning(
                "종목 인식 실패",
                "관심종목 형식을 확인하세요.\n예: 005930:삼성전자,000660:SK하이닉스,066570:LG전자",
            )
            return []
        return items[:max_items]

    def _start_batch_collect(self):
        items = self._watchlist_items_for_batch(max_items=10)
        if not items:
            return
        fmt_name = self.format_var.get()
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        custom_topic = self._custom_topic()
        names = ", ".join(name for _, name in items)
        self._set_running(True, f"관심종목 {len(items)}개 자료를 순서대로 수집합니다: {names}")
        threading.Thread(
            target=self._batch_collect,
            args=(items, fmt_name, out_dir, custom_topic),
            daemon=True,
        ).start()

    def _batch_collect(self, items, fmt_name, out_dir, custom_topic=""):
        results = []
        errors = []
        last = None
        total = len(items)
        workers = max(1, min(int(getattr(mr, "BATCH_PARALLEL_WORKERS", 2) or 2), total, 4))

        def collect_one(idx, code, name):
            self.result_queue.put(("batch_progress", f"[{idx}/{total}] {name}({code}) 자료 수집 중..."))
            if mr.is_information_script_request(format_name=fmt_name, custom_topic=custom_topic):
                raw = mr.build_information_raw_data(name, code, force=True)
            elif mr.is_weekend_script_request(format_name=fmt_name, custom_topic=custom_topic):
                raw = mr.build_weekend_raw_data(name, code, force=True)
            else:
                raw = mr.build_raw_data(name, code, force=True)
            path = mr.save_text_file(raw, f"{name}_수집데이터", output_dir=out_dir, prefix="수집데이터")
            return idx, name, code, raw, path

        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {
                ex.submit(collect_one, idx, code, name): (idx, code, name)
                for idx, (code, name) in enumerate(items, start=1)
            }
            for fut in as_completed(future_map):
                idx, code, name = future_map[fut]
                try:
                    _, name, code, raw, path = fut.result()
                    results.append((name, code, path))
                    last = (name, code, raw, path)
                    self.result_queue.put(("batch_progress", f"[{idx}/{total}] {name} 자료 저장 완료: {path}"))
                except Exception as e:
                    msg = f"{name}({code}) 자료 수집 실패: {type(e).__name__}: {e}"
                    errors.append(msg)
                    self.result_queue.put(("batch_progress", msg))
        lines = [f"[전체 자료수집 완료] 성공 {len(results)}개 / 실패 {len(errors)}개", ""]
        for name, code, path in results:
            lines.append(f"- {name}({code}) 저장: {path}")
        if errors:
            lines += ["", "[실패]"]
            lines.extend(f"- {e}" for e in errors)
        self.result_queue.put(("batch_collect_ok", "\n".join(lines), last))

    def _start_batch_fast_scripts(self):
        self._start_batch_scripts(fast=True)

    def _start_batch_scripts(self, fast=False):
        if not mr.OPENAI_API_KEY and not getattr(mr, "GEMINI_API_KEY", ""):
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY 또는 GEMINI_API_KEY를 입력하세요.")
            return
        items = self._watchlist_items_for_batch(max_items=10)
        if not items:
            return
        fmt_name = self.format_var.get()
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        custom_topic = self._custom_topic()
        names = ", ".join(name for _, name in items)
        if not messagebox.askyesno(
            "전체 대본 생성",
            f"관심종목 {len(items)}개 대본을 병렬 생성합니다.\n\n"
            f"대상: {names}\n포맷: {fmt_name}\n\n"
            f"엔진: {getattr(mr, 'BATCH_ENGINE_MODE', 'mixed')} / 동시 작업: {getattr(mr, 'BATCH_PARALLEL_WORKERS', 2)}개\n"
            "OpenAI/Gemini 사용량이 발생합니다.\n계속할까요?",
        ):
            return
        self._set_running(True, f"관심종목 {len(items)}개 대본을 병렬 생성합니다.")
        threading.Thread(
            target=self._batch_scripts,
            args=(items, fmt_name, out_dir, custom_topic, fast),
            daemon=True,
        ).start()

    def _batch_scripts(self, items, fmt_name, out_dir, custom_topic="", fast=False):
        results = []
        errors = []
        last = None
        total = len(items)
        workers = max(1, min(int(getattr(mr, "BATCH_PARALLEL_WORKERS", 2) or 2), total, 4))
        engine_mode = "fast" if fast else str(getattr(mr, "BATCH_ENGINE_MODE", "chain") or "chain").strip().lower()

        def pick_engine(idx):
            has_openai = bool(getattr(mr, "OPENAI_API_KEY", ""))
            has_gemini = bool(getattr(mr, "GEMINI_API_KEY", ""))
            if engine_mode in ("fast", "quick", "speed"):
                return "fast_openai" if has_openai else "fast_gemini"
            if engine_mode in ("chain", "dual", "both") and has_openai and has_gemini:
                return "chain"
            if engine_mode == "gemini" and has_gemini:
                return "gemini"
            if engine_mode == "openai" or not has_gemini:
                return "openai"
            if not has_openai:
                return "gemini"
            return "chain" if (has_openai and has_gemini) else ("gemini" if has_gemini else "openai")

        def opposite_engine(engine):
            if engine in ("fast_openai", "fast_gemini"):
                return None
            if engine == "chain":
                return "gemini" if getattr(mr, "GEMINI_API_KEY", "") else ("openai" if getattr(mr, "OPENAI_API_KEY", "") else None)
            if engine == "gemini" and getattr(mr, "OPENAI_API_KEY", ""):
                return "openai"
            if engine == "openai" and getattr(mr, "GEMINI_API_KEY", ""):
                return "gemini"
            return None

        def script_one(idx, code, name):
            engine = pick_engine(idx)
            self.result_queue.put(("batch_progress", f"[{idx}/{total}] {name}({code}) 자료 수집 중..."))
            if mr.is_information_script_request(format_name=fmt_name, custom_topic=custom_topic):
                raw = mr.build_information_raw_data(name, code, force=True)
            elif mr.is_weekend_script_request(format_name=fmt_name, custom_topic=custom_topic):
                raw = mr.build_weekend_raw_data(name, code, force=True)
            else:
                raw = mr.build_raw_data(name, code, force=True)
            self.result_queue.put(("batch_progress", f"[{idx}/{total}] {name} 대본 생성 중... ({engine})"))
            try:
                result = mr.generate_ai_script(
                    stock_name=name,
                    stock_code=code,
                    format_name=fmt_name,
                    raw_data=raw,
                    output_dir=out_dir,
                    save=True,
                    custom_topic=custom_topic,
                    engine=engine,
                )
            except Exception as e:
                fallback = opposite_engine(engine)
                if not fallback:
                    raise
                self.result_queue.put(("batch_progress", f"[{idx}/{total}] {name} {engine} 실패 → {fallback}로 재시도: {e}"))
                result = mr.generate_ai_script(
                    stock_name=name,
                    stock_code=code,
                    format_name=fmt_name,
                    raw_data=raw,
                    output_dir=out_dir,
                    save=True,
                    custom_topic=custom_topic,
                    engine=fallback,
                )
                engine = fallback
            stats = result.get("stats", {}) or {}
            chars = stats.get("chars", len(result.get("text", "")))
            sep_count = stats.get("separators", str(result.get("text", "")).count("---<"))
            path = result.get("path", "")
            return idx, name, code, raw, result.get("text", ""), path, stats, engine, chars, sep_count

        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {
                ex.submit(script_one, idx, code, name): (idx, code, name)
                for idx, (code, name) in enumerate(items, start=1)
            }
            for fut in as_completed(future_map):
                idx, code, name = future_map[fut]
                try:
                    _, name, code, raw, text, path, stats, engine, chars, sep_count = fut.result()
                    results.append((name, code, path, chars, sep_count, engine))
                    last = (name, code, raw, text, path, stats)
                    self.result_queue.put(("batch_progress", f"[{idx}/{total}] {name} 대본 완료({engine}): 약 {chars:,}자 / 저장: {path}"))
                except Exception as e:
                    msg = f"{name}({code}) 대본 생성 실패: {type(e).__name__}: {e}"
                    errors.append(msg)
                    self.result_queue.put(("batch_progress", msg))
        lines = [f"[전체 대본생성 완료] 성공 {len(results)}개 / 실패 {len(errors)}개", ""]
        for name, code, path, chars, sep_count, engine in results:
            lines.append(f"- {name}({code}) [{engine}] 약 {chars:,}자 / 구분자 {sep_count}개 / 저장: {path}")
        if errors:
            lines += ["", "[실패]"]
            lines.extend(f"- {e}" for e in errors)
        self.result_queue.put(("batch_scripts_ok", "\n".join(lines), last))

    def _start_ai_report(self):
        if not self._require_raw():
            return
        if not mr.OPENAI_API_KEY:
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY를 입력하세요.")
            return
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        self._set_running(True, "OpenAI가 분석 리포트를 작성 중입니다.")
        threading.Thread(target=self._generate_ai_report, args=(out_dir,), daemon=True).start()

    def _generate_ai_report(self, out_dir):
        try:
            result = mr.generate_ai_report(
                stock_name=self.last_name,
                stock_code=self.last_code,
                raw_data=self.last_raw,
                output_dir=out_dir,
                save=True,
            )
            self.result_queue.put(("ai_report_ok", result["text"], result["path"]))
        except Exception as e:
            self.result_queue.put(("error", f"AI 리포트 생성 실패: {e}"))

    def _start_thumbnail_copy(self):
        script_text = self.ai_result_text.get("1.0", "end").strip()
        if not script_text:
            messagebox.showinfo(
                "완성 대본 필요",
                "먼저 AI 대본을 생성한 뒤 썸네일 문구를 추천받으세요.",
            )
            return
        if not mr.OPENAI_API_KEY:
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY를 입력하세요.")
            return
        name = self.last_name or self.name_var.get().strip() or "해당 종목"
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        self._set_running(True, "완성 대본에 맞는 썸네일 문구를 만드는 중입니다.")
        threading.Thread(
            target=self._generate_thumbnail_copy,
            args=(name, script_text, out_dir),
            daemon=True,
        ).start()

    def _generate_thumbnail_copy(self, name, script_text, out_dir):
        try:
            result = mr.generate_thumbnail_copy(
                stock_name=name,
                script_text=script_text,
                raw_data=self.last_raw,
                output_dir=out_dir,
                save=True,
            )
            self.result_queue.put(
                ("thumbnail_ok", result["text"], result["path"])
            )
        except Exception as e:
            self.result_queue.put(("error", f"썸네일 문구 생성 실패: {e}"))

    def _start_thumbnail_image(self):
        thumbnail_copy = self.thumbnail_text.get("1.0", "end").strip()
        if not thumbnail_copy:
            messagebox.showinfo(
                "썸네일 문구 필요",
                "먼저 [③ 썸네일 문구]를 눌러 문구 후보를 만든 뒤 이미지를 생성하세요.",
            )
            return
        if not mr.OPENAI_API_KEY:
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY를 입력하세요.")
            return
        name = self.last_name or self.name_var.get().strip() or "해당 종목"
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        self._set_running(True, "OpenAI 이미지 모델로 썸네일 3장을 만드는 중입니다.")
        threading.Thread(
            target=self._generate_thumbnail_image,
            args=(name, thumbnail_copy, out_dir),
            daemon=True,
        ).start()

    def _generate_thumbnail_image(self, name, thumbnail_copy, out_dir):
        try:
            result = mr.generate_thumbnail_images_ai(
                stock_name=name,
                thumbnail_copy=thumbnail_copy,
                raw_data=self.last_raw,
                output_dir=out_dir,
                count=3,
                save=True,
            )
            self.result_queue.put(("thumbnail_image_ok", result["path"], result))
        except Exception as e:
            self.result_queue.put(("error", f"썸네일 이미지 생성 실패: {e}"))

    def _start_scan(self):
        self._set_running(True, "시장 소재 스캔 중입니다.")
        threading.Thread(target=self._scan_market, daemon=True).start()

    def _scan_market(self):
        try:
            text = mr.scan_market_candidates()
            self.result_queue.put(("scan_ok", text))
        except Exception as e:
            self.result_queue.put(("error", f"소재 스캔 실패: {type(e).__name__}: {e}"))

    def _save_my_stocks(self):
        value = self.my_stocks_var.get().strip()
        if not value:
            messagebox.showwarning("목록 비어있음", "저장할 관심종목이 없습니다.")
            return
        if mr.update_config_value("MY_STOCKS", value):
            self.status_var.set("관심종목이 config.txt에 저장되었습니다. 다음 실행 때도 유지됩니다.")
        else:
            messagebox.showerror("저장 실패", "config.txt에 저장하지 못했습니다.")

    def _start_watchlist_scan(self):
        stocks_raw = self.my_stocks_var.get().strip()
        if not stocks_raw:
            messagebox.showinfo("관심종목 없음",
                                "'관심종목' 칸에 종목을 입력하세요.\n"
                                "예: 005930:삼성전자,068270:셀트리온")
            return
        n = len([p for p in stocks_raw.split(",") if p.strip()]) if not stocks_raw.upper().startswith("TOP") else stocks_raw
        self._set_running(True, f"관심종목 스캔 중... (대상: {n}, 종목당 2~3초)")
        threading.Thread(target=self._watchlist_scan, args=(stocks_raw,), daemon=True).start()

    def _watchlist_scan(self, stocks_raw):
        try:
            text = mr.scan_watchlist_candidates(stocks_raw=stocks_raw)
            self.result_queue.put(("scan_ok", text))
        except Exception as e:
            self.result_queue.put(("error", f"관심종목 스캔 실패: {type(e).__name__}: {e}"))

    def _start_topic_ai(self):
        if not mr.OPENAI_API_KEY:
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY를 입력하세요.")
            return
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        # 화면에 스캔 결과(시장 스캔이든 관심종목 스캔이든)가 있으면 그걸 사용
        scan_text = self.scan_text.get("1.0", "end").strip()
        msg = ("OpenAI가 화면의 스캔 결과에서 소재 후보를 고르는 중입니다."
               if scan_text else "OpenAI가 시장 스캔 후 소재 후보를 고르는 중입니다.")
        self._set_running(True, msg)
        threading.Thread(target=self._topic_ai, args=(out_dir, scan_text),
                         daemon=True).start()

    def _topic_ai(self, out_dir, scan_text=None):
        try:
            result = mr.generate_topic_ideas(output_dir=out_dir, save=True,
                                             scan_text=scan_text or None)
            self.result_queue.put(("topic_ok", result["scan"], result["text"], result["path"]))
        except Exception as e:
            self.result_queue.put(("error", f"AI 소재 선정 실패: {e}"))

    def _start_auto_pipeline(self):
        """스캔 → AI 소재 선정 → 1순위 심층수집 → AI 대본까지 원버튼"""
        if not mr.OPENAI_API_KEY:
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY를 입력하세요.")
            return
        fmt_name = self.format_var.get()
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        if not messagebox.askyesno(
                "완전 자동 실행",
                "다음 3단계를 자동으로 진행합니다:\n\n"
                "1) 시장 스캔 + OpenAI 소재 선정\n"
                "2) 1순위 종목 자동 심층수집\n"
                f"3) 대본 생성 ({fmt_name})\n\n"
                "OpenAI 호출 2회, 총 2~5분 걸릴 수 있습니다. 진행할까요?"):
            return
        self._set_running(True, "1/3 — 시장 스캔 및 OpenAI 소재 선정 중...")
        custom_topic = self._custom_topic()
        threading.Thread(target=self._auto_pipeline, args=(fmt_name, out_dir, custom_topic),
                         daemon=True).start()

    def _auto_pipeline(self, fmt_name, out_dir, custom_topic=""):
        try:
            topic = mr.generate_topic_ideas(output_dir=out_dir, save=True)
            self.result_queue.put(("auto_stage", topic["scan"], topic["text"]))
            script = mr.generate_script_from_topic(
                topic["text"], format_name=fmt_name, output_dir=out_dir, save=True,
                custom_topic=custom_topic)
            self.result_queue.put((
                "auto_ok", script["text"], script["path"], script.get("stats", {}),
                script.get("stock_name", ""), script.get("stock_code", ""),
                script.get("raw_data", ""),
            ))
        except Exception as e:
            self.result_queue.put(("error", f"자동 파이프라인 실패: {e}"))

    def _start_auto_pipeline_watchlist(self):
        """내 관심종목 안에서만 소재를 골라 대본까지 원버튼"""
        if not mr.OPENAI_API_KEY:
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY를 입력하세요.")
            return
        stocks_raw = self.my_stocks_var.get().strip()
        if not stocks_raw:
            messagebox.showinfo("관심종목 없음",
                                "'관심종목' 칸에 종목을 입력하세요.\n"
                                "예: 005930:삼성전자,068270:셀트리온")
            return
        fmt_name = self.format_var.get()
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        if not messagebox.askyesno(
                "내 종목 자동 실행",
                "다음 3단계를 자동으로 진행합니다:\n\n"
                "1) '관심종목' 스캔 (⚡반직관 신호 탐지)\n"
                "2) OpenAI가 그중 가장 눈에 띄는 종목 선정\n"
                f"3) 심층수집 후 대본 생성 ({fmt_name})\n\n"
                "소재는 입력한 종목 안에서만 선택됩니다. 진행할까요?"):
            return
        self._set_running(True, "1/3 — 관심종목 스캔 중... (종목당 2~3초)")
        custom_topic = self._custom_topic()
        threading.Thread(target=self._auto_pipeline_watchlist,
                         args=(stocks_raw, fmt_name, out_dir, custom_topic), daemon=True).start()

    def _auto_pipeline_watchlist(self, stocks_raw, fmt_name, out_dir, custom_topic=""):
        try:
            scan_text = mr.scan_watchlist_candidates(stocks_raw=stocks_raw)
            topic = mr.generate_topic_ideas(output_dir=out_dir, save=True,
                                            scan_text=scan_text)
            self.result_queue.put(("auto_stage", topic["scan"], topic["text"]))
            script = mr.generate_script_from_topic(
                topic["text"], format_name=fmt_name, output_dir=out_dir, save=True,
                custom_topic=custom_topic)
            self.result_queue.put((
                "auto_ok", script["text"], script["path"], script.get("stats", {}),
                script.get("stock_name", ""), script.get("stock_code", ""),
                script.get("raw_data", ""),
            ))
        except Exception as e:
            self.result_queue.put(("error", f"내 종목 자동 파이프라인 실패: {e}"))

    def _start_script_from_topic(self):
        """'AI 소재 선정' 탭에 있는 결과의 1순위 종목으로 대본 생성"""
        if not mr.OPENAI_API_KEY or not getattr(mr, "GEMINI_API_KEY", ""):
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY와 GEMINI_API_KEY를 모두 입력하세요.")
            return
        topic_text = self.topic_text.get("1.0", "end").strip()
        if not topic_text:
            messagebox.showinfo("소재 없음",
                                "'AI 소재 선정' 탭이 비어 있습니다. 먼저 [AI 소재 선정]을 실행하세요.")
            return
        name, code = mr.extract_top_pick(topic_text)
        if name and not code:
            code = mr.find_stock_code_by_name(name)
        if not name or not code:
            messagebox.showwarning(
                "1순위 인식 실패",
                "선정 결과에서 1순위 종목을 자동 인식하지 못했습니다.\n"
                "종목명·코드를 상단에 직접 입력하고 [자료 수집] → [대본 생성]을 이용하세요.")
            return
        fmt_name = self.format_var.get()
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        if not messagebox.askyesno(
                "1순위로 대본 생성",
                f"1순위: {name}({code})\n\n"
                f"이 종목으로 심층수집 후 대본을 생성할까요?\n포맷: {fmt_name}"):
            return
        self._set_running(True, f"'{name}({code})' 심층수집 및 대본 생성 중...")
        custom_topic = self._custom_topic()
        threading.Thread(target=self._script_from_topic,
                         args=(topic_text, fmt_name, out_dir, custom_topic), daemon=True).start()

    def _script_from_topic(self, topic_text, fmt_name, out_dir, custom_topic=""):
        try:
            script = mr.generate_script_from_topic(
                topic_text, format_name=fmt_name, output_dir=out_dir, save=True,
                custom_topic=custom_topic)
            self.result_queue.put((
                "auto_ok", script["text"], script["path"], script.get("stats", {}),
                script.get("stock_name", ""), script.get("stock_code", ""),
                script.get("raw_data", ""),
            ))
        except Exception as e:
            self.result_queue.put(("error", f"1순위 대본 생성 실패: {e}"))

    def _start_angles(self):
        """수집된 자료에서 서로 다른 방송 흐름 후보 4개 추출"""
        if not mr.OPENAI_API_KEY:
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY를 입력하세요.")
            return
        if not self.last_raw:
            messagebox.showinfo("자료 없음",
                                "먼저 종목을 입력하고 [자료 수집]을 실행하세요.\n"
                                "수집된 자료에서 흐름 후보를 뽑습니다.")
            return
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        self._set_running(True, f"'{self.last_name}' 자료에서 방송 흐름 후보 4개 추출 중...")
        threading.Thread(target=self._angles,
                         args=(self.last_name, self.last_code, self.last_raw, out_dir),
                         daemon=True).start()

    def _angles(self, name, code, raw, out_dir):
        try:
            result = mr.generate_angles(name, code, raw_data=raw,
                                        output_dir=out_dir, save=True)
            self.result_queue.put(("angles_ok", result["text"]))
        except Exception as e:
            self.result_queue.put(("error", f"흐름 후보 추출 실패: {e}"))

    def _start_script_with_angle(self):
        """추출된 흐름 후보 중 하나를 골라 내부 참고용으로 대본 생성"""
        if not mr.OPENAI_API_KEY:
            messagebox.showerror("API 키 누락", "config.txt에 OPENAI_API_KEY를 입력하세요.")
            return
        angles_text = self.topic_text.get("1.0", "end").strip()
        if not angles_text or ("[흐름" not in angles_text and "[각도" not in angles_text):
            messagebox.showinfo("흐름 후보 없음",
                                "먼저 [자료→흐름 후보]를 실행하세요.\n"
                                "'AI 소재 선정' 탭에 흐름 후보가 표시됩니다.")
            return
        if not self.last_raw:
            messagebox.showinfo("자료 없음", "수집된 자료가 없습니다. [자료 수집]부터 실행하세요.")
            return
        default_no = mr.extract_recommended_angle_no(angles_text, default=1)
        no = simpledialog.askinteger(
            "흐름 선택",
            f"몇 번 흐름으로 대본을 만들까요? (1~4)\nAI 추천: {default_no}번",
            initialvalue=default_no, minvalue=1, maxvalue=4, parent=self)
        if not no:
            return
        angle = mr.extract_angle(angles_text, no)
        if not angle:
            messagebox.showwarning("흐름 인식 실패",
                                   f"{no}번 흐름을 결과에서 찾지 못했습니다. "
                                   "[자료→흐름 후보]을 다시 실행해 보세요.")
            return
        fmt_name = self.format_var.get()
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        first_line = angle.splitlines()[0][:60]
        if not messagebox.askyesno("흐름 확인",
                                   f"선택한 흐름:\n{first_line}\n\n"
                                   f"이 흐름을 참고해서 대본을 생성할까요?\n포맷: {fmt_name}"):
            return
        self._set_running(True, f"{no}번 선택 흐름 참고로 대본 생성 중... (2~4분)")
        custom_topic = self._custom_topic()
        threading.Thread(target=self._script_with_angle,
                         args=(angle, fmt_name, out_dir, custom_topic), daemon=True).start()

    def _script_with_angle(self, angle, fmt_name, out_dir, custom_topic=""):
        try:
            script = mr.generate_ai_script(
                stock_name=self.last_name, stock_code=self.last_code,
                format_name=fmt_name, raw_data=self.last_raw,
                output_dir=out_dir, save=True, angle=angle,
                custom_topic=custom_topic)
            self.result_queue.put((
                "auto_ok", script["text"], script["path"], script.get("stats", {}),
                self.last_name, self.last_code, script.get("raw_data", ""),
            ))
        except Exception as e:
            self.result_queue.put(("error", f"선택 흐름 대본 생성 실패: {e}"))

    def _start_diagnostics(self):
        self._set_running(True, "시스템 진단 중... (키·계정·네트워크 실제 확인, 10~20초)")
        threading.Thread(target=self._diagnostics, daemon=True).start()

    def _diagnostics(self):
        try:
            text = mr.run_diagnostics()
            self.result_queue.put(("scan_ok", text))
        except Exception as e:
            self.result_queue.put(("error", f"진단 실패: {type(e).__name__}: {e}"))

    def _show_number_warnings(self, stats):
        """숫자 검증 팝업은 띄우지 않는다. 위험 블록 삭제는 생성 단계에서 조용히 처리한다."""
        return

    def _show_report_tone_warnings(self, stats):
        """구어체 경고 팝업은 띄우지 않는다. 말투는 프롬프트에서 사전에 강제한다."""
        return

    def _preview_telegram(self):
        if not self._require_raw():
            return
        digest = mr.make_telegram_digest(self.last_name, self.last_code, self.last_raw)
        self._set_text(self.telegram_text, digest)
        self.status_var.set("텔레그램 다이제스트 미리보기 생성 완료.")

    def _send_telegram(self):
        if not self._require_raw():
            return
        digest = self.telegram_text.get("1.0", "end").strip()
        if not digest:
            digest = mr.make_telegram_digest(self.last_name, self.last_code, self.last_raw)
            self._set_text(self.telegram_text, digest)
        ok, msg = mr.send_telegram(digest)
        if ok:
            self.status_var.set(msg)
            messagebox.showinfo("텔레그램", msg)
        else:
            messagebox.showerror("텔레그램", msg)

    def _poll_queue(self):
        try:
            item = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_queue)
            return

        status = item[0]
        if status not in ("auto_stage", "batch_progress"):  # 중간 단계 알림은 진행 상태(버튼 잠금)를 유지
            self._set_running(False)

        if status == "batch_progress":
            _, msg = item
            self.status_var.set(msg)
            self._log(msg)
        elif status == "auto_stage":
            _, scan, topic = item
            self._set_text(self.scan_text, scan)
            self._set_text(self.topic_text, topic)
            name, code = mr.extract_top_pick(topic)
            picked = f"{name}({code})" if name and code else (name or "인식 중")
            self.status_var.set(f"2/3 — 1순위 '{picked}' 심층수집 및 대본 생성 중... "
                                "(2~4분 소요)")
            self._log(f"AI 소재 선정 완료. 1순위 '{picked}' 심층수집으로 넘어갑니다.")
        elif status == "auto_ok":
            _, text, path, stats, name, code, raw = item
            self._set_text(self.ai_result_text, text)
            self.notebook.select(self.ai_result_text.master)
            self.last_saved_path = path
            if raw:
                self._set_text(self.data_text, raw)
                self.last_raw = raw
                self.last_name = name
                self.last_code = code
                self._set_text(self.telegram_text,
                               mr.make_telegram_digest(name, code, raw))
                self.name_var.set(name)
                self.code_var.set(code)
            chars = stats.get("chars", len(text)) if isinstance(stats, dict) else len(text)
            sep_count = stats.get("separators", text.count("---<")) if isinstance(stats, dict) else text.count("---<")
            self.status_var.set(f"완전자동 완료: {name}({code}) / 약 {chars:,}자 / "
                                f"구분자 {sep_count}개 / 저장: {path}")
            self._log(f"완전자동 완료: {name}({code}) / 약 {chars:,}자 / 저장: {path}")
            self._show_number_warnings(stats)
            self._show_report_tone_warnings(stats)
            if messagebox.askyesno(
                    "자동 대본 완성",
                    f"스캔→선정→대본 자동 생성이 완료되었습니다.\n\n"
                    f"소재: {name}({code})\n약 {chars:,}자 / 구분자 {sep_count}개\n\n"
                    f"저장 파일:\n{path}\n\n지금 결과 파일을 열까요?"):
                self._open_path(path)
        elif status == "collect_ok":
            _, raw, report_prompt, script_prompt, digest, name, code = item
            self._set_text(self.data_text, raw)
            self._set_text(self.report_prompt_text, report_prompt)
            self._set_text(self.script_prompt_text, script_prompt)
            self._set_text(self.telegram_text, digest)
            self.last_raw = raw
            self.last_name = name
            self.last_code = code
            self.status_var.set("데이터 수집 완료. 대본/리포트/텔레그램/소재 기능을 바로 사용할 수 있습니다.")
            self._log(f"자료 수집 완료: {name}({code})")
        elif status == "batch_collect_ok":
            _, summary, last = item
            self._set_text(self.data_text, summary)
            self.notebook.select(self.data_text.master)
            self.status_var.set("관심종목 전체 자료수집 완료. 로그와 자료 탭을 확인하세요.")
            self._log("관심종목 전체 자료수집 완료")
            if last:
                name, code, raw, path = last
                self.last_raw = raw
                self.last_name = name
                self.last_code = code
                self.name_var.set(name)
                self.code_var.set(code)
                self.last_saved_path = path
                self._set_text(self.telegram_text, mr.make_telegram_digest(name, code, raw))
            if messagebox.askyesno("전체 자료수집 완료", f"{summary}\n\n저장 폴더를 열까요?"):
                self._open_output_folder()
        elif status == "ai_script_ok":
            _, text, path, stats = item
            self._set_text(self.ai_result_text, text)
            self.notebook.select(self.ai_result_text.master)
            self.last_saved_path = path
            chars = stats.get("chars", len(text)) if isinstance(stats, dict) else len(text)
            sep_count = stats.get("separators", text.count("---<")) if isinstance(stats, dict) else text.count("---<")
            self.status_var.set(f"AI 대본 작성 완료: 약 {chars:,}자 / 구분자 {sep_count}개 / 저장 파일: {path}")
            self._log(f"대본 생성 완료: 약 {chars:,}자 / 구분자 {sep_count}개 / 저장: {path}")
            self._show_number_warnings(stats)
            self._show_report_tone_warnings(stats)
            if messagebox.askyesno("대본 완성", f"AI 대본 생성이 완료되었습니다.\n\n약 {chars:,}자 / 구분자 {sep_count}개\n\n저장 파일:\n{path}\n\n지금 결과 파일을 열까요?"):
                self._open_path(path)
        elif status == "batch_scripts_ok":
            _, summary, last = item
            self._set_text(self.ai_result_text, summary)
            self.notebook.select(self.ai_result_text.master)
            self.status_var.set("관심종목 전체 대본생성 완료. 대본 탭과 로그를 확인하세요.")
            self._log("관심종목 전체 대본생성 완료")
            if last:
                name, code, raw, text, path, stats = last
                self.last_raw = raw
                self.last_name = name
                self.last_code = code
                self.last_saved_path = path
                self.name_var.set(name)
                self.code_var.set(code)
                self._set_text(self.data_text, raw)
                self._set_text(self.telegram_text, mr.make_telegram_digest(name, code, raw))
            if messagebox.askyesno("전체 대본생성 완료", f"{summary}\n\n저장 폴더를 열까요?"):
                self._open_output_folder()
        elif status == "ai_report_ok":
            _, text, path = item
            self._set_text(self.ai_report_text, text)
            self.notebook.select(self.ai_report_text.master)
            self.last_saved_path = path
            self.status_var.set(f"AI 리포트 작성 완료: {path}")
            self._log(f"AI 리포트 작성 완료: {path}")
            if messagebox.askyesno("리포트 완성", f"AI 리포트 생성이 완료되었습니다.\n\n저장 파일:\n{path}\n\n지금 결과 파일을 열까요?"):
                self._open_path(path)
        elif status == "thumbnail_ok":
            _, text, path = item
            self._set_text(self.thumbnail_text, text)
            self.notebook.select(self.thumbnail_text.master)
            self.last_saved_path = path
            self.status_var.set(f"썸네일 문구 추천 완료: {path}")
            self._log(f"썸네일 문구 추천 완료: {path}")
        elif status == "thumbnail_image_ok":
            _, path, info = item
            self.last_saved_path = path
            main = info.get("main", "") if isinstance(info, dict) else ""
            model = info.get("model", "") if isinstance(info, dict) else ""
            paths = info.get("paths", [path]) if isinstance(info, dict) else [path]
            items = info.get("items", []) if isinstance(info, dict) else []
            if paths:
                self.last_saved_path = paths[0]
            lines = []
            for idx, p in enumerate(paths, 1):
                label = ""
                if idx - 1 < len(items):
                    label = items[idx - 1].get("style", "")
                lines.append(f"{idx}. {label} {p}".strip())
            path_text = "\n".join(lines) if lines else path
            self.status_var.set(f"AI 썸네일 이미지 {len(paths)}장 생성 완료")
            self._log(f"AI 썸네일 이미지 {len(paths)}장 생성 완료 / 모델: {model} / 첫 파일: {self.last_saved_path}")
            if messagebox.askyesno("AI 썸네일 이미지 완성", f"OpenAI 이미지 모델로 썸네일 {len(paths)}장이 생성되었습니다.\n\n모델: {model}\n대표 문구: {main}\n\n저장 파일:\n{path_text}\n\n저장 폴더를 열까요?"):
                self._open_output_folder()
        elif status == "scan_ok":
            _, text = item
            self._set_text(self.scan_text, text)
            self.status_var.set("소재 스캔 완료.")
            self._log("소재 스캔 완료")
        elif status == "angles_ok":
            _, angles = item
            self._set_text(self.topic_text, angles)
            self.notebook.select(self.topic_text.master)
            rec = mr.extract_recommended_angle_no(angles, default=1)
            self.status_var.set(f"흐름 후보 4개 추출 완료 (AI 추천: {rec}번). "
                                "[선택 흐름으로 대본]을 눌러 번호를 선택하세요.")
            self._log(f"흐름 후보 4개 추출 완료. AI 추천: {rec}번")
        elif status == "topic_ok":
            _, scan, text, path = item
            self._set_text(self.scan_text, scan)
            self._set_text(self.topic_text, text)
            self.notebook.select(self.topic_text.master)
            self.last_saved_path = path
            self.status_var.set(f"AI 소재 선정 완료: {path}")
            self._log(f"AI 소재 선정 완료: {path}")
            if messagebox.askyesno("소재 선정 완료", f"AI 소재 후보가 저장되었습니다.\n\n저장 파일:\n{path}\n\n지금 결과 파일을 열까요?"):
                self._open_path(path)
        else:
            _, msg = item
            self.status_var.set("작업 실패")
            self._log(f"작업 실패: {msg}")
            messagebox.showerror("오류", msg)

        self.after(100, self._poll_queue)

    def _require_raw(self):
        if not self.last_raw:
            messagebox.showwarning("자료 필요", "먼저 '자료 수집'을 눌러 데이터를 확보하세요.")
            return False
        return True

    def _set_running(self, running, message=None):
        state = "disabled" if running else "normal"
        for btn in self.action_buttons:
            try:
                btn.configure(state=state)
            except tk.TclError:
                btn.config(state=state)
        if running:
            if message:
                self.status_var.set(message)
                self._log(message)
            self.progress.pack(fill="x", padx=12, pady=(0, 4))
            try:
                self.progress.start(12)
            except TypeError:
                self.progress.start()
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _log(self, message):
        """로그 탭에 시간과 함께 작업 기록을 쌓는다."""
        widget = getattr(self, "log_text", None)
        if widget is None:
            return
        try:
            from datetime import datetime
            line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
            try:
                widget.configure(state="normal")
            except Exception:
                pass
            widget.insert("end", line)
            widget.see("end")
        except Exception:
            pass

    def _set_text(self, widget, content):
        widget.delete("1.0", "end")
        widget.insert("1.0", content or "")

    def _copy(self, widget):
        content = widget.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("복사", "복사할 내용이 없습니다.")
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self.status_var.set("클립보드에 복사했습니다.")
        self._log("클립보드 복사 완료")

    def _copy_script_and_thumbnail(self):
        script = self.ai_result_text.get("1.0", "end").strip()
        thumb = self.thumbnail_text.get("1.0", "end").strip()
        if not script and not thumb:
            messagebox.showinfo("복사", "복사할 대본이나 썸네일 문구가 없습니다.")
            return
        parts = []
        if script:
            parts.append("[완성 대본]\n" + script)
        if thumb:
            parts.append("[썸네일 문구]\n" + thumb)
        self.clipboard_clear()
        self.clipboard_append("\n\n".join(parts))
        self.status_var.set("완성 대본과 썸네일 문구를 함께 복사했습니다.")
        self._log("완성 대본과 썸네일 문구 함께 복사 완료")

    def _paste_topic_memo(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if not text:
            messagebox.showinfo("주제/메모", "클립보드에 붙여넣을 내용이 없습니다.")
            return
        self.custom_topic_var.set(text)
        self.status_var.set("클립보드 내용을 주제/메모에 붙여넣었습니다.")
        self._log("주제/메모 붙여넣기 완료")

    def _clear_topic_memo(self):
        self.custom_topic_var.set("")
        self.status_var.set("주제/메모를 비웠습니다. 이제 비워둔 상태로 자동 작성됩니다.")
        self._log("주제/메모 비움")

    def _save_widget(self, widget, prefix):
        content = widget.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("저장", "저장할 내용이 없습니다.")
            return
        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        name = self.last_name or self.name_var.get().strip() or prefix
        path = mr.save_text_file(content, name, output_dir=out_dir, prefix=prefix)
        self.last_saved_path = path
        self.status_var.set(f"저장 완료: {path}")
        self._log(f"파일 저장 완료: {path}")
        if messagebox.askyesno("저장 완료", f"저장 파일:\n{path}\n\n지금 파일을 열까요?"):
            self._open_path(path)

    def _save_current_tab(self):
        current = self.notebook.select()
        title = self.notebook.tab(current, "text")
        widget = None
        parent = current if not isinstance(current, str) else self.nametowidget(current)
        for child in parent.winfo_children():
            if isinstance(child, tk.Text) or (ctk is not None and isinstance(child, ctk.CTkTextbox)):
                widget = child
                break
        if widget is None:
            messagebox.showinfo("저장", "저장할 텍스트 탭을 찾지 못했습니다.")
            return
        self._save_widget(widget, title.replace(" ", "_"))

    def _open_path(self, path):
        if not path:
            messagebox.showinfo("결과 파일", "아직 열린 결과 파일이 없습니다. 먼저 대본 생성이나 저장을 실행하세요.")
            return
        path = os.path.abspath(path)
        if not os.path.exists(path):
            messagebox.showerror("오류", f"파일을 찾을 수 없습니다.\n{path}")
            return
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("오류", f"파일을 열 수 없습니다.\n{path}\n\n{e}")

    def _open_last_result_file(self):
        if self.last_saved_path and os.path.exists(self.last_saved_path):
            self._open_path(self.last_saved_path)
            return

        out_dir = self.out_var.get().strip() or mr.OUTPUT_DIR
        if not os.path.isdir(out_dir):
            messagebox.showinfo("결과 파일", "아직 output 폴더가 없습니다. 먼저 대본 생성이나 저장을 실행하세요.")
            return

        txt_files = [
            os.path.join(out_dir, f)
            for f in os.listdir(out_dir)
            if f.lower().endswith(".txt")
        ]
        if not txt_files:
            messagebox.showinfo("결과 파일", "output 폴더에 열 수 있는 TXT 결과 파일이 없습니다.")
            return

        latest = max(txt_files, key=os.path.getmtime)
        self.last_saved_path = latest
        self._open_path(latest)

    def _open_config(self):
        self._open_path(mr.CONFIG_PATH)

    def _open_output_folder(self):
        path = self.out_var.get().strip() or mr.OUTPUT_DIR
        os.makedirs(path, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("오류", f"저장 폴더를 열 수 없습니다.\n{e}")


if __name__ == "__main__":
    App().mainloop()







