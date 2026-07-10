# -*- coding: utf-8 -*-
"""Office 인포그래픽 엔진.

기존 stock_editor_app.py의 인포그래픽 제작 철학을 오피스용으로 분리한 모듈이다.
대본 생성에는 관여하지 않고, 완성 대본/장면을 프리미엄 인포그래픽 이미지 프롬프트로 바꾼다.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List


INFOGRAPHIC_COLOR_THEMES: Dict[str, Dict[str, str]] = {
    "editorial_cream": {
        "label": "크림 에디토리얼",
        "desc": "Warm ivory and soft cream dominant background, deep ink-gray typography, muted terracotta accent, tiny dusty olive markers. Premium printed business magazine mood.",
        "render_note": "Use subtle uncoated-paper grain, fine editorial rule lines, restrained flat shapes, soft natural shadows. Avoid neon, glassmorphism, dark cinematic photography, and glossy badges.",
    },
    "soft_blueprint": {
        "label": "소프트 블루프린트",
        "desc": "Very pale cool-gray to white background, deep slate-navy text, desaturated cobalt-blue single accent, light powder-blue panels. Airy, precise, professional.",
        "render_note": "Favor precise editorial diagrams, thin technical lines, quiet geometric structure, subtle grid alignment, and lots of white space.",
    },
    "illustrated_pastel": {
        "label": "파스텔 일러스트",
        "desc": "Soft warm-white background with muted sky blue, sage green, warm apricot, dusty coral, anchored by dark charcoal text. Gentle and cohesive.",
        "render_note": "Create a cohesive modern editorial illustration with rounded sophisticated forms, paper texture, layered depth, and friendly storytelling.",
    },
    "coral_teal_editorial": {
        "label": "코랄 + 틸 에디토리얼",
        "desc": "Clean off-white background, deep teal structure, warm coral highlight, muted sand support fill, charcoal text. Refined editorial contrast.",
        "render_note": "Use art-directed editorial illustration, strong simple silhouettes, controlled color blocking, matte natural lighting.",
    },
    "navy_gold": {
        "label": "네이비 + 골드",
        "desc": "Deep navy / charcoal-navy dominant background, warm gold/amber accent for key numbers and callouts, white/cream support. Premium finance-grade.",
        "render_note": "",
    },
    "yellow_black": {
        "label": "옐로우 + 블랙",
        "desc": "Bold golden-yellow and near-black as two dominant colors, white strokes/text on black, black text on yellow. High-contrast and energetic.",
        "render_note": "",
    },
    "blue_white": {
        "label": "블루 + 화이트 (테크)",
        "desc": "Clean fintech blue with white and light gray. Soft glossy 3D highlights and subtle blue gradient lighting. Modern tech-platform feel.",
        "render_note": "",
    },
    "gradient_purple": {
        "label": "퍼플-마젠타 그라데이션",
        "desc": "Gradient from sky-blue through blue to purple and magenta, dark gray thin-line icons, clean white background. Modern SaaS dashboard feel.",
        "render_note": "",
    },
    "green_white": {
        "label": "그린 + 화이트",
        "desc": "Deep forest/emerald green and white dominant colors, soft mint/sage secondary tone. Calm, stable, trustworthy.",
        "render_note": "",
    },
    "red_black": {
        "label": "레드 + 블랙",
        "desc": "Bold red and near-black dominant colors, white text and icon strokes. Urgent high-impact alert tone.",
        "render_note": "",
    },
    "dark_lineart_city": {
        "label": "다크 라인아트 (도시)",
        "desc": "Near-black to deep charcoal with blurred moody photographic night-city or glass-skyscraper backdrop. White thin line-art and one soft accent.",
        "render_note": "Use a blurred moody photographic background across the full frame. Render icons and connecting lines as ultra-thin white line-art, with small dot nodes and subtle accent glow.",
    },
    "dark_gold_luxury": {
        "label": "다크 골드 럭셔리",
        "desc": "Near-black background with dramatic museum-quality lighting, warm gold/brass single accent, white/light-gray body text. Premium documentary title-card feel.",
        "render_note": "Favor dramatic editorial dark photography or photorealistic 3D-render mood, deep shadows, one dramatic light source, small precious gold accents.",
    },
    "dark_blue_glass": {
        "label": "다크 블루 글래스 (핀테크)",
        "desc": "Deep navy-to-black gradient with glossy translucent glass/metal 3D-render shapes, soft blue glow, frosted-glass panels, minimal white/light-blue typography.",
        "render_note": "Build the centerpiece as glossy photorealistic 3D glass/metal panels or abstract glowing geometry against dark navy-to-black gradient.",
    },
}


INFOGRAPHIC_LAYOUTS: Dict[str, tuple[str, str]] = {
    "visual_explainer": ("A00 — VISUAL EXPLAINER INFOGRAPHIC", """
- Explain the scene primarily through ONE large polished editorial illustration or clean 3D/vector scene.
- The main illustration occupies about 60-70% of the frame.
- Arrange only 2-4 short Korean callouts around it with clean leader lines or subtle numbered markers.
- Visual storytelling first; never turn the image into a dense report, dashboard, or card-news page.
"""),
    "clean_slide": ("A0 — CLEAN INFORMATION SLIDE", """
- Build a clean premium presentation slide with calm editorial hierarchy, not a dashboard or poster.
- Keep generous safe margins: at least 8% left/right, 7% top, and 12% clear bottom margin.
- Top zone: one large Korean headline, one short summary, optionally one real key number.
- Bottom zone: exactly 3 well-spaced information blocks in one row or balanced 2+1 arrangement.
- Use thin dividers, subtle tonal panels, restrained shadows, and ample negative space.
"""),
    "grid": ("A — ICON STAT GRID", """
- Top 30%: bold headline band with the key point and one accent-highlighted keyword/number.
- Main area: 3 or 4 equal-sized mini-cards, each with an icon badge, one large number/keyword, and a short Korean label.
- Keep the spacing grid consistent and premium.
"""),
    "roadmap": ("B — ROADMAP / NUMBERED TIMELINE", """
- Horizontal or vertical roadmap with 3 or 4 numbered nodes connected by a thin line.
- Each node has one short bold Korean label and a tiny supporting phrase.
- Use a dominant-color panel on one side and a neutral panel on the other for contrast.
"""),
    "radial": ("C — RADIAL RING SEGMENTS", """
- Central circular hub with a short title/icon, surrounded by 3-4 ring or petal segments.
- Each segment has a small icon badge and a real number only if the source contains it.
- If the source has no real percentage, use short keywords instead of invented numbers.
"""),
    "hexagon": ("D — HEXAGON NODE NETWORK", """
- Central circle connected to 4-6 hexagon or rounded-square nodes around it.
- Each node contains one meaningful icon and a short Korean label.
- Use accent color for connector lines and a few node borders.
"""),
    "gradient_timeline": ("E — GRADIENT ICON TIMELINE", """
- Vertical or diagonal sequence of 3-5 circular icon nodes with partial accent arcs.
- Alternate short Korean text left/right of each node.
- Each icon must represent a real step or concept from the scene.
"""),
    "photo_hub": ("F — PHOTO-BLENDED CIRCULAR HUB", """
- Glossy circular or cylindrical hub in the center, with a photographic or photorealistic 3D accent inside or behind it.
- 4-6 small data points radiate around the hub with tiny icons and 1-2 word Korean labels.
- Photo/3D accent is the visual centerpiece, tinted to match the palette.
"""),
    "photo_split": ("G — PHOTO + DIAGONAL SPLIT PANEL", """
- Bold diagonal geometric color-block divides the frame.
- One side has a photographic accent, the other side has a large headline plus one stat card or 2 bullets.
- Premium editorial cover-slide feel.
"""),
    "typographic_hero": ("H — TYPOGRAPHIC HERO", """
- No icon badges, circles, hexagons, grids, or connecting lines.
- One massive bold Korean headline phrase fills most of the frame like a magazine pull-quote.
- At most one supporting number or short phrase in the accent color.
"""),
    "photo_fullbleed": ("I — FULL-BLEED PHOTO/3D WITH TYPE OVERLAY", """
- No icon badges, card panels, grids, or connecting lines.
- Photographic or photorealistic 3D-render image fills the entire 16:9 frame.
- A bold Korean headline and at most one short support line sit over the image with a subtle scrim for legibility.
"""),
    "hero_stat": ("J — SINGLE HERO NUMBER", """
- No multiple data points or connecting lines.
- One giant real number/percentage dominates 50-70% of the frame if the source contains it.
- If no real number exists, use one short powerful keyword instead.
"""),
    "editorial_collage": ("K — ASYMMETRIC EDITORIAL COLLAGE", """
- No symmetric grids or evenly spaced connecting lines.
- Use 2-3 overlapping photographic or shape crops, mixed-scale typography, and off-grid premium magazine composition.
- Dynamic but still clean and legible.
"""),
}


LAYOUT_ORDER = [
    "grid", "roadmap", "radial", "hexagon", "gradient_timeline", "photo_hub", "photo_split",
    "typographic_hero", "photo_fullbleed", "hero_stat", "editorial_collage",
]

CINEMATIC_THEMES = {"dark_lineart_city", "dark_gold_luxury", "dark_blue_glass"}


def theme_options() -> List[Dict[str, str]]:
    return [{"key": key, "label": val["label"]} for key, val in INFOGRAPHIC_COLOR_THEMES.items()]


def layout_options() -> List[Dict[str, str]]:
    return [{"key": "auto", "label": "자동 순환"}] + [
        {"key": key, "label": title.split("—", 1)[-1].strip()} for key, (title, _) in INFOGRAPHIC_LAYOUTS.items()
    ]


def normalize_scene_text_for_image(text: str) -> str:
    text = str(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\w\s가-힣ㄱ-ㅎㅏ-ㅣ.,%+\-–—:;!?()·/₩$&\[\]<>]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def image_numeric_display_rule() -> str:
    return """
NUMERIC DISPLAY RULE FOR IMAGE TEXT:
- If the source scene contains Korean spoken numbers, convert them into Arabic numerals on the image.
- Examples: "이십구만 천오백원" → "291,500원", "오백원 상승" → "500원 상승", "영점일팔퍼센트" → "0.18%".
- Never display Korean spoken-number words such as 이십구만, 천오백원, 오백원, 영점일팔퍼센트 as visible labels.
- For stock prices, percentages, rankings, dates, counts, and amounts, use clean digits with units.
""".strip()


def split_script_scenes(script: str, limit: int = 6) -> List[str]:
    chunks = []
    for part in str(script or "").replace("\r\n", "\n").split("---<"):
        cleaned = " ".join(line.strip() for line in part.splitlines() if line.strip())
        if len(cleaned) >= 12:
            chunks.append(cleaned)
    if not chunks and script.strip():
        sentences = [s.strip() for s in re.split(r"(?<=[.!?。！？요다죠니다까])\s+", script) if len(s.strip()) >= 12]
        chunks = sentences
    return chunks[: max(1, int(limit or 6))]


def _compact_text(value: str, limit: int = 80) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip("., ") + "…"


def infer_scene_title(stock_name: str, scene_text: str, idx: int) -> str:
    text = scene_text or ""
    if idx == 0:
        return f"{stock_name}, 겉보다 속을 봐야 합니다"
    if any(k in text for k in ("외국인", "기관", "개인", "수급", "매수", "매도")):
        return "돈의 방향이 갈린 지점"
    if any(k in text for k in ("실적", "영업이익", "매출", "가이던스")):
        return "실적 숫자 다음에 볼 것"
    if any(k in text for k in ("환율", "반도체", "글로벌", "미국", "엔비디아", "마이크론")):
        return "시장 배경까지 같이 봐야 합니다"
    if any(k in text for k in ("공매도", "신용", "잔고", "부담")):
        return "수급 뒤에 숨어 있는 부담"
    return f"핵심 장면 {idx + 1}"


def build_concepts(
    stock_name: str,
    script: str,
    count: int = 6,
    color_theme: str = "dark_lineart_city",
    custom_color: str = "",
    layout_concept: str = "photo_fullbleed",
    photo_accent: bool = True,
) -> List[Dict[str, Any]]:
    scenes = split_script_scenes(script, limit=count)
    concepts: List[Dict[str, Any]] = []
    for idx, scene in enumerate(scenes):
        resolved_layout = resolve_layout(layout_concept, idx + 1)
        theme_label = INFOGRAPHIC_COLOR_THEMES.get(color_theme, INFOGRAPHIC_COLOR_THEMES["dark_lineart_city"])["label"]
        layout_title = INFOGRAPHIC_LAYOUTS.get(resolved_layout, INFOGRAPHIC_LAYOUTS["grid"])[0]
        concepts.append({
            "id": f"info-{idx + 1}",
            "selected": idx < min(4, len(scenes)),
            "scene_no": idx + 1,
            "title": infer_scene_title(stock_name, scene, idx),
            "main": _compact_text(scene, 52),
            "support": _compact_text(scene, 108),
            "layout": layout_title,
            "layout_key": resolved_layout,
            "requested_layout": layout_concept,
            "style": theme_label,
            "theme_key": color_theme,
            "custom_color": custom_color,
            "photo_accent": bool(photo_accent),
            "source_text": scene,
        })
    return concepts


def resolve_layout(layout_concept: str, scene_index: int) -> str:
    key = (layout_concept or "photo_fullbleed").strip().lower()
    if key == "auto":
        return LAYOUT_ORDER[(max(1, int(scene_index or 1)) - 1) % len(LAYOUT_ORDER)]
    return key if key in INFOGRAPHIC_LAYOUTS else "grid"


def build_infographic_prompt(
    scene_text: str,
    date_str: str | None = None,
    scene_index: int = 1,
    total: int = 1,
    color_theme: str = "dark_lineart_city",
    custom_color_text: str = "",
    include_photo_accent: bool = True,
    layout_concept: str = "photo_fullbleed",
) -> str:
    source = normalize_scene_text_for_image(scene_text)
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")

    theme_key = (color_theme or "dark_lineart_city").strip().lower()
    if theme_key == "custom" and custom_color_text.strip():
        palette_desc = custom_color_text.strip()
        palette_label = "사용자 지정 색상"
        render_note = ""
    else:
        theme = INFOGRAPHIC_COLOR_THEMES.get(theme_key, INFOGRAPHIC_COLOR_THEMES["dark_lineart_city"])
        palette_desc = theme["desc"]
        palette_label = theme["label"]
        render_note = theme.get("render_note", "")

    if theme_key in CINEMATIC_THEMES:
        include_photo_accent = True

    photo_rule = (
        """
PHOTO / 3D ACCENT INTEGRATION:
- Blend in ONE tasteful photographic or photorealistic 3D-render accent element relevant to the scene.
- It may be a blurred city/business district, semiconductor fab, data center, stock-chart screen, smartphone, glossy 3D platform, or anonymous business silhouette.
- It must support the information structure, not dominate the text.
- Apply a color overlay or duotone treatment to match the chosen palette.
"""
        if include_photo_accent else
        """
PHOTO / 3D ACCENT INTEGRATION:
- Keep this image icon/vector-based only. Do not add photographic or photorealistic elements.
"""
    ).strip()

    layout_key = resolve_layout(layout_concept, scene_index)
    layout_title, layout_body = INFOGRAPHIC_LAYOUTS.get(layout_key, INFOGRAPHIC_LAYOUTS["grid"])

    render_note_block = f"\n{render_note.strip()}\n" if render_note.strip() else ""

    return f"""
Create ONE clean 16:9 Korean infographic image. Style direction: a polished, premium Korean finance/business infographic slide, similar to an Envato / Creative Market keynote template.
This must look like a senior presentation designer made it, not like a cluttered news page, card-news post, or plain text slide.

SOURCE SCENE TO TRANSFORM:
{source}

{image_numeric_display_rule()}

CRITICAL — TEXT ON THE IMAGE:
- The ONLY visible text allowed is short Korean content drawn from the source scene, plus digits/%/currency/allowed abbreviations.
- Never render prompt instructions, English captions, color palette names, layout names, watermarks, fake UI labels, lorem ipsum, or meta words.
- Do not paste long script sentences. Convert the scene into a short headline plus 2-4 compact labels.
- If Korean text is hard to render, simplify the phrase. Do not corrupt it into boxes, question marks, Hebrew/Arabic/random glyphs, or mojibake.

FIXED CHANNEL COLOR PALETTE:
Palette label for internal direction only: {palette_label}
{palette_desc}
{render_note_block}
{photo_rule}

LAYOUT {layout_title}:
{layout_body.strip()}

LAYOUT CONSISTENCY:
- Reuse this exact structural layout for the same video unless layout_concept is auto.
- Only the content, icon metaphors, numbers, and labels should change per scene.

COMPOSITION / QUALITY BAR:
- 16:9 horizontal composition, YouTube-safe margins, no cropped bottom text.
- 3 to 5 visual information blocks maximum.
- Use clear hierarchy: one headline, one central visual structure, a few supporting labels.
- Use real numbers only when present in the source. Never invent percentages, prices, dates, rankings, or amounts.
- Korean typography must be crisp, large, and readable on a YouTube video.
- Avoid cheap stock icons, childish cartoons, disaster metaphors, war metaphors, blood/explosion imagery, and messy dashboards.
- Scene date, if needed: {date_str}. Scene {scene_index} of {total}; do not display the scene number.
""".strip()


def build_prompt_from_concept(concept: Dict[str, Any], total: int = 1) -> str:
    return build_infographic_prompt(
        concept.get("source_text") or concept.get("support") or concept.get("main") or "",
        scene_index=int(concept.get("scene_no") or 1),
        total=total,
        color_theme=concept.get("theme_key") or "dark_lineart_city",
        custom_color_text=concept.get("custom_color") or "",
        include_photo_accent=bool(concept.get("photo_accent", True)),
        layout_concept=concept.get("requested_layout") or concept.get("layout_key") or "photo_fullbleed",
    )
