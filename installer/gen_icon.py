"""Gera o ícone do Maestro (maestro.ico) — quadrado arredondado azul com um 'M'."""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "maestro.ico")


def _font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf", "seguisb.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([10, 10, 246, 246], radius=52, fill=(78, 115, 223, 255))
    d.rounded_rectangle([10, 10, 246, 246], radius=52, outline=(44, 62, 148, 255), width=6)
    font = _font(150)
    bbox = d.textbbox((0, 0), "M", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((256 - w) / 2 - bbox[0], (256 - h) / 2 - bbox[1]), "M",
           font=font, fill=(255, 255, 255, 255))
    img.save(OUT, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(f"Ícone gerado: {OUT}")


if __name__ == "__main__":
    main()
