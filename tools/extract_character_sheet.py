from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web_company" / "assets" / "ai_crew_actions.png"
OUTPUT = ROOT / "web_company" / "assets" / "ai_crew_actions_cutout.png"


def is_background(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= 224 and g >= 214 and b >= 194 and max(rgb) - min(rgb) <= 66


def main() -> None:
    image = Image.open(SOURCE).convert("RGBA")
    width, height = image.size
    rgb = image.convert("RGB")
    pixels = rgb.load()

    removable = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def seed(x: int, y: int) -> None:
        index = y * width + x
        if not removable[index] and is_background(pixels[x, y]):
            removable[index] = 1
            queue.append((x, y))

    for x in range(width):
        seed(x, 0)
        seed(x, height - 1)
    for y in range(height):
        seed(0, y)
        seed(width - 1, y)

    while queue:
        x, y = queue.popleft()
        if x > 0:
            seed(x - 1, y)
        if x + 1 < width:
            seed(x + 1, y)
        if y > 0:
            seed(x, y - 1)
        if y + 1 < height:
            seed(x, y + 1)

    alpha = Image.new("L", (width, height), 255)
    alpha.putdata([0 if value else 255 for value in removable])
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.45))
    image.putalpha(alpha)
    image.save(OUTPUT, optimize=True)
    print(f"saved: {OUTPUT}")


if __name__ == "__main__":
    main()
