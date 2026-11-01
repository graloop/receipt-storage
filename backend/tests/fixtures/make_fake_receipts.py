# Draws the three invented receipts in this folder, so nothing here ever needs a
# real one. The shops, items and amounts are made up, and each image says FAKE.
# Run it again only to change them:
#   .venv/bin/python tests/fixtures/make_fake_receipts.py

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent

RECEIPTS = {
    "fake-grocery.png": [
        "ÉPICERIE DU COIN (FAUX)",
        "123, rue de l'Exemple, Montréal",
        "",
        "Date : 2026-03-14",
        "",
        "2 x Pommes              5,98",
        "Pain de blé             4,49",
        "Café moulu             12,99",
        "",
        "Sous-total             23,46",
        "TPS / TVQ               1,45",
        "TOTAL                  24,91 $",
    ],
    "fake-restaurant.png": [
        "TEST BISTRO",
        "45 Sample Street, Toronto",
        "",
        "Date: 2026-04-02",
        "",
        "1 Soup of the day       8.00",
        "1 Burger               19.50",
        "1 Coffee                3.50",
        "",
        "Tax                     4.03",
        "Tip                     5.00",
        "TOTAL                  40.03 CAD",
    ],
    "fake-electronics.png": [
        "NOWHERE ELECTRONICS",
        "Order 000-TEST-0001",
        "",
        "Date: 2026-05-20",
        "",
        "1 Wireless headphones  89.99",
        "1 USB-C cable          14.99",
        "",
        "Shipping                0.00",
        "Tax                    15.75",
        "TOTAL                 120.73 USD",
    ],
}


def draw_receipt(lines: list[str]) -> Image.Image:
    # Courier lines the amounts up like a till, and has the accents Pillow's default font lacks.
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Courier New Bold.ttf", size=26)
    line_height = 44
    image = Image.new("RGB", (640, 150 + line_height * len(lines)), "#fbfaf5")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((40, 40 + i * line_height), line, fill="#222222", font=font)
    draw.text((40, image.height - 70), "*** FAKE RECEIPT, FOR TESTS ***", fill="#b91c1c", font=font)
    return image


if __name__ == "__main__":
    for name, lines in RECEIPTS.items():
        draw_receipt(lines).save(HERE / name)
