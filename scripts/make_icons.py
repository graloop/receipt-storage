# Draws the home-screen icons from the logo's infinity loop (the same curve as
# index.html). Run it again only if the logo changes:
#   backend/.venv/bin/python scripts/make_icons.py

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent.parent / "backend" / "frontend" / "icons"
# The logo's path: a start point, then one (control, control, end) per curve.
START = (56, 28)
CURVES = [
    ((66, 12), (78, 4), (86, 4)),
    ((99, 4), (108, 15), (108, 28)),
    ((108, 41), (99, 52), (86, 52)),
    ((78, 52), (66, 44), (56, 28)),
    ((46, 12), (34, 4), (26, 4)),
    ((13, 4), (4, 15), (4, 28)),
    ((4, 41), (13, 52), (26, 52)),
    ((34, 52), (46, 44), (56, 28)),
]


def loop_points():
    points, p0 = [], START
    for p1, p2, p3 in CURVES:
        for i in range(401):
            t = i / 400
            u = 1 - t
            points.append(
                tuple(u**3 * a + 3 * u**2 * t * b + 3 * u * t**2 * c + t**3 * d for a, b, c, d in zip(p0, p1, p2, p3, strict=False))
            )
        p0 = p3
    return points


def icon(size: int) -> Image.Image:
    # Drawn four times too big, then scaled down, for smooth edges.
    big = size * 4
    image = Image.new("RGB", (big, big), "#022c22")
    scale = big * 3.3 / 512
    offset_x, offset_y = big * 70 / 512, big * 148 / 512
    points = [(x * scale + offset_x, y * scale + offset_y) for x, y in loop_points()]
    radius = 4.5 * scale
    draw = ImageDraw.Draw(image)
    # A chain of dots makes a smoother thick curve than a thick line, which leaves notches at its joints.
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#34d399")
    return image.resize((size, size), Image.Resampling.LANCZOS)


for size in (192, 512):
    icon(size).save(OUT / f"icon-{size}.png")
