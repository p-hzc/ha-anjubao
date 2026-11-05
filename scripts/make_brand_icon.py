"""Generate Home Assistant brand assets for the Anjubao integration.

Creates custom_components/anjubao/brand/icon.png (256x256),
icon@2x.png (512x512) and logo.png (landscape wordmark).

Run:  python scripts/make_brand_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BRAND_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "anjubao" / "brand"

TOP = (63, 91, 246)      # indigo
BOTTOM = (18, 184, 166)  # teal

SS = 4  # supersampling factor


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def gradient(size: int) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        grad.putpixel(
            (0, y),
            tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)),
        )
    return grad.resize((size, size))


def draw_glyph(img: Image.Image, scale: float) -> None:
    """White house + bulb, drawn at `scale` relative to a 1024 canvas."""
    d = ImageDraw.Draw(img)
    s = lambda v: round(v * scale)  # noqa: E731
    stroke = s(40)
    white = (255, 255, 255)

    def poly(points: list[tuple[int, int]]) -> None:
        """Stroke a polyline with rounded vertices (butt-ended segments + vertex dots)."""
        pts = [(s(x), s(y)) for x, y in points]
        for a, b in zip(pts, pts[1:]):
            d.line([a, b], fill=white, width=stroke)
        for x, y in pts:
            d.ellipse((x - stroke // 2, y - stroke // 2, x + stroke // 2, y + stroke // 2), fill=white)

    # house: roof, two walls, floor
    poly([(512, 250), (836, 500), (836, 790), (188, 790), (188, 500), (512, 250)])

    # bulb glass + screw base
    d.ellipse((s(414), s(447), s(610), s(643)), outline=white, width=stroke)
    d.rounded_rectangle((s(492), s(636), s(532), s(688)), radius=s(8), fill=white)
    d.rounded_rectangle((s(474), s(694), s(550), s(722)), radius=s(10), fill=white)

    # filament
    d.line(
        [(s(470), s(566)), (s(492), s(516)), (s(512), s(560)), (s(532), s(516)), (s(554), s(566))],
        fill=white,
        width=s(20),
        joint="curve",
    )


def build_icon(size: int) -> Image.Image:
    canvas = size * SS
    base = gradient(canvas).convert("RGBA")
    base.putalpha(rounded_mask(canvas, round(canvas * 0.22)))
    draw_glyph(base, canvas / 1024)
    return base.resize((size, size), Image.LANCZOS)


def build_logo(height: int = 256, size: float = 1.0) -> Image.Image:
    from PIL import ImageFont

    font_path = next(
        (p for p in ("C:/Windows/Fonts/msyhbd.ttc", "/System/Library/Fonts/PingFang.ttc") if Path(p).exists()),
        None,
    )
    h = round(height * size)
    icon = build_icon(h)
    if font_path is None:
        return icon

    title, subtitle = "安居家园", "Home Assistant"
    f_title = ImageFont.truetype(font_path, round(h * 0.30))
    f_sub = ImageFont.truetype(font_path, round(h * 0.13))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left = h + round(h * 0.14)
    text_w = max(
        probe.textbbox((0, 0), title, font=f_title)[2],
        probe.textbbox((0, 0), subtitle, font=f_sub)[2],
    )
    width = left + text_w + round(h * 0.12)

    logo = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    logo.alpha_composite(icon, (0, 0))
    d = ImageDraw.Draw(logo)
    d.text((left, round(h * 0.44)), title, font=f_title, fill=TOP, anchor="ls")
    d.text((left, round(h * 0.80)), subtitle, font=f_sub, fill=BOTTOM, anchor="ls")
    return logo


def main() -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    build_icon(256).save(BRAND_DIR / "icon.png")
    build_icon(512).save(BRAND_DIR / "icon@2x.png")
    build_logo(256).save(BRAND_DIR / "logo.png")
    build_logo(512).save(BRAND_DIR / "logo@2x.png")
    for p in sorted(BRAND_DIR.iterdir()):
        print(f"{p.name}: {Image.open(p).size}")


if __name__ == "__main__":
    main()
