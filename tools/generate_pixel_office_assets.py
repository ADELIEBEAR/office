from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web_company" / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)


WORKERS = [
    {"hair": "#2b2529", "skin": "#f2c29d", "top": "#263f72", "bottom": "#273247", "style": "long"},
    {"hair": "#553624", "skin": "#eebc96", "top": "#b64f4e", "bottom": "#343a4e", "style": "short"},
    {"hair": "#1f2026", "skin": "#f0c5a3", "top": "#47705a", "bottom": "#28354d", "style": "bob"},
    {"hair": "#6b3f2b", "skin": "#f5c9a5", "top": "#d27a38", "bottom": "#37415b", "style": "pony"},
    {"hair": "#30251f", "skin": "#dca77e", "top": "#375f83", "bottom": "#2d3548", "style": "short"},
    {"hair": "#16171c", "skin": "#edbd98", "top": "#765a91", "bottom": "#2d3448", "style": "bob"},
    {"hair": "#8a5a36", "skin": "#f2c6a2", "top": "#ad6d58", "bottom": "#384158", "style": "pony"},
    {"hair": "#342b25", "skin": "#dba47f", "top": "#39727b", "bottom": "#29364a", "style": "long"},
    {"hair": "#c08a4b", "skin": "#f3c8a4", "top": "#596b9e", "bottom": "#333c50", "style": "short"},
]


def rect(d: ImageDraw.ImageDraw, xy, fill):
    d.rectangle(tuple(int(v) for v in xy), fill=fill)


def draw_worker(cfg: dict, frame: int) -> Image.Image:
    base = Image.new("RGBA", (16, 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    idle_bob = 1 if frame == 1 else 0
    working = frame in (2, 3)
    walking = frame in (4, 5)
    y = 2 + idle_bob

    # 머리카락 실루엣
    if cfg["style"] == "long":
        rect(d, (3, y + 1, 12, y + 11), cfg["hair"])
        rect(d, (2, y + 5, 3, y + 12), cfg["hair"])
        rect(d, (12, y + 5, 13, y + 12), cfg["hair"])
    elif cfg["style"] == "bob":
        rect(d, (3, y + 1, 12, y + 9), cfg["hair"])
        rect(d, (2, y + 5, 3, y + 9), cfg["hair"])
        rect(d, (12, y + 5, 13, y + 9), cfg["hair"])
    elif cfg["style"] == "pony":
        rect(d, (3, y + 1, 12, y + 8), cfg["hair"])
        rect(d, (12, y + 4, 14, y + 9), cfg["hair"])
    else:
        rect(d, (3, y + 1, 12, y + 7), cfg["hair"])
        rect(d, (2, y + 3, 4, y + 5), cfg["hair"])

    # 얼굴과 앞머리
    rect(d, (4, y + 4, 11, y + 10), cfg["skin"])
    rect(d, (3, y + 2, 11, y + 4), cfg["hair"])
    rect(d, (3, y + 4, 4, y + 6), cfg["hair"])
    rect(d, (11, y + 3, 12, y + 5), cfg["hair"])
    rect(d, (5, y + 6, 5, y + 7), "#25304a")
    rect(d, (10, y + 6, 10, y + 7), "#25304a")
    rect(d, (7, y + 9, 8, y + 9), "#b65e5e")

    body_y = y + 11
    if working:
        # 의자에 앉아 키보드를 두드리는 프레임
        rect(d, (4, body_y, 11, body_y + 6), cfg["top"])
        rect(d, (2, body_y + 2 + (frame - 2), 5, body_y + 3 + (frame - 2)), cfg["skin"])
        rect(d, (10, body_y + 3 - (frame - 2), 13, body_y + 4 - (frame - 2)), cfg["skin"])
        rect(d, (4, body_y + 7, 7, body_y + 9), cfg["bottom"])
        rect(d, (8, body_y + 7, 11, body_y + 9), cfg["bottom"])
        rect(d, (3, body_y + 9, 7, body_y + 10), "#5d4939")
        rect(d, (8, body_y + 9, 12, body_y + 10), "#5d4939")
    else:
        rect(d, (4, body_y, 11, body_y + 6), cfg["top"])
        rect(d, (3, body_y + 1, 4, body_y + 6), cfg["skin"])
        rect(d, (11, body_y + 1, 12, body_y + 6), cfg["skin"])
        if walking:
            shift = 1 if frame == 4 else -1
            rect(d, (5 + shift, body_y + 7, 7 + shift, body_y + 10), cfg["bottom"])
            rect(d, (8 - shift, body_y + 7, 10 - shift, body_y + 10), cfg["bottom"])
            rect(d, (4 + shift, body_y + 10, 7 + shift, body_y + 11), "#4b3c32")
            rect(d, (8 - shift, body_y + 10, 11 - shift, body_y + 11), "#4b3c32")
        else:
            rect(d, (5, body_y + 7, 7, body_y + 10), cfg["bottom"])
            rect(d, (8, body_y + 7, 10, body_y + 10), cfg["bottom"])
            rect(d, (4, body_y + 10, 7, body_y + 11), "#4b3c32")
            rect(d, (8, body_y + 10, 11, body_y + 11), "#4b3c32")

    # 사원증/셔츠 포인트
    rect(d, (7, body_y + 1, 8, body_y + 3), "#f3ead9")
    return base.resize((32, 48), Image.Resampling.NEAREST)


def generate_workers() -> None:
    sheet = Image.new("RGBA", (32 * 6, 48 * len(WORKERS)), (0, 0, 0, 0))
    for row, cfg in enumerate(WORKERS):
        for frame in range(6):
            sheet.alpha_composite(draw_worker(cfg, frame), (frame * 32, row * 48))
    sheet.save(ASSETS / "pixel_workers.png", optimize=True)


def draw_desk(d: ImageDraw.ImageDraw, x: int, y: int, w: int = 74) -> None:
    rect(d, (x, y, x + w, y + 24), "#774a29")
    rect(d, (x + 3, y + 3, x + w - 3, y + 8), "#a66c36")
    rect(d, (x + 5, y + 23, x + 10, y + 34), "#4e3322")
    rect(d, (x + w - 10, y + 23, x + w - 5, y + 34), "#4e3322")
    rect(d, (x + 26, y - 12, x + 50, y + 4), "#263448")
    rect(d, (x + 29, y - 9, x + 47, y + 1), "#78a8c4")
    rect(d, (x + 36, y + 4, x + 40, y + 8), "#1f2836")
    rect(d, (x + 28, y + 11, x + 48, y + 14), "#ddd6c9")
    rect(d, (x + 14, y + 9, x + 20, y + 16), "#eee5d3")
    rect(d, (x + 15, y + 8, x + 19, y + 9), "#7e4b35")
    rect(d, (x + 29, y + 28, x + 49, y + 38), "#2f3948")


def draw_plant(d: ImageDraw.ImageDraw, x: int, y: int) -> None:
    rect(d, (x + 5, y + 14, x + 14, y + 25), "#b66f3d")
    rect(d, (x + 7, y + 11, x + 12, y + 15), "#365f43")
    rect(d, (x + 1, y + 6, x + 8, y + 13), "#4f8753")
    rect(d, (x + 11, y + 4, x + 18, y + 13), "#3f7647")
    rect(d, (x + 6, y, x + 12, y + 10), "#5b9659")


def generate_office() -> None:
    im = Image.new("RGB", (480, 270), "#d6c2a2")
    d = ImageDraw.Draw(im)
    # 벽과 바닥
    rect(d, (0, 0, 479, 20), "#243041")
    rect(d, (0, 20, 326, 269), "#9a6336")
    for y in range(20, 270, 12):
        d.line((0, y, 326, y), fill="#7d4e2d", width=1)
    for x in range(0, 327, 40):
        d.line((x, 20, x, 269), fill="#a97545", width=1)
    rect(d, (327, 20, 479, 269), "#263548")
    rect(d, (336, 28, 471, 132), "#39506a")
    for x in range(338, 472, 16):
        d.line((x, 28, x, 132), fill="#425b77")
    for y in range(30, 133, 16):
        d.line((336, y, 471, y), fill="#425b77")
    # 회의실
    rect(d, (350, 42, 455, 118), "#304257")
    rect(d, (362, 60, 441, 88), "#8f5e35")
    rect(d, (368, 66, 435, 82), "#a97848")
    for cx in (370, 390, 414, 434):
        rect(d, (cx, 52, cx + 10, 59), "#1e2a39")
        rect(d, (cx, 90, cx + 10, 97), "#1e2a39")
    # 라운지
    rect(d, (342, 150, 468, 253), "#496174")
    rect(d, (350, 166, 416, 190), "#b87950")
    rect(d, (354, 159, 412, 173), "#d59b68")
    rect(d, (377, 204, 438, 228), "#825332")
    rect(d, (383, 209, 432, 222), "#b07848")
    # 개인 업무석
    for x, y in ((22, 64), (116, 64), (210, 64), (22, 158), (116, 158), (210, 158)):
        draw_desk(d, x, y)
    # 서가와 커피존
    rect(d, (278, 40, 319, 122), "#654128")
    for y in (48, 70, 92):
        rect(d, (283, y, 314, y + 4), "#a97745")
        for x in range(285, 312, 6):
            rect(d, (x, y - 10, x + 3, y - 1), ("#6a879e" if x % 12 else "#b85d55"))
    rect(d, (278, 150, 319, 204), "#5d4636")
    rect(d, (285, 157, 312, 176), "#202a37")
    rect(d, (290, 161, 307, 171), "#8199a7")
    # 식물과 소품
    for x, y in ((5, 28), (304, 226), (451, 122), (332, 230), (90, 124)):
        draw_plant(d, x, y)
    rect(d, (329, 20, 335, 269), "#172331")
    rect(d, (327, 132, 479, 138), "#172331")
    # 출입구
    rect(d, (452, 206, 479, 269), "#1c2835")
    rect(d, (458, 215, 475, 265), "#62758a")
    im.resize((960, 540), Image.Resampling.NEAREST).save(ASSETS / "pixel_office_floor.png", optimize=True)


if __name__ == "__main__":
    generate_workers()
    generate_office()
    print("generated pixel_workers.png and pixel_office_floor.png")
