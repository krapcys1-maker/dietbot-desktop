"""Rysuje autorski znak Porcji (talerz, liść, kropka) do zgodnej wstecz nazwy ICO."""

from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((30, 30, S - 30, S - 30), radius=300, fill="#244D3D")
d.ellipse((213, 234, 811, 832), outline="#F9F5EA", width=43)
d.ellipse((292, 300, 590, 623), fill="#C4D98C")
d.polygon([(300, 567), (380, 373), (590, 290), (573, 462), (449, 599)], fill="#C4D98C")
d.line([(362, 575), (510, 405)], fill="#244D3D", width=30)
d.ellipse((555, 512, 768, 725), fill="#ECA67F")

out = Path(__file__).with_name("dietbot.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
print(out)
