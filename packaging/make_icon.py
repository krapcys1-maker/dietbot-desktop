"""Rysuje ikonę aplikacji (miska z sałatą) do pliku packaging/dietbot.ico."""

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=(46, 125, 79, 255))
# liście
d.ellipse((250, 250, 560, 560), fill=(160, 214, 120, 255))
d.ellipse((450, 200, 780, 530), fill=(120, 190, 95, 255))
d.ellipse((360, 330, 640, 610), fill=(196, 232, 150, 255))
d.ellipse((600, 360, 760, 520), fill=(230, 90, 70, 255))  # pomidor
# miska
d.pieslice((190, 330, S - 190, S - 170), start=0, end=180, fill=(250, 248, 240, 255))
d.rectangle((190, 580, S - 190, 600), fill=(250, 248, 240, 255))
d.pieslice((250, 390, S - 250, S - 230), start=0, end=180, fill=(232, 226, 210, 255))
d.rounded_rectangle((420, 830, S - 420, 870), radius=20, fill=(250, 248, 240, 255))

out = Path(__file__).with_name("dietbot.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
print(out)
