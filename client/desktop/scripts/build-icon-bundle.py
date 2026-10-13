"""Build multi-resolution Windows ICO files from the rendered V4 app tile."""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "desktop" / "assets" / "brand" / "png" / "hashmm-1024.png"
TARGETS = (
    ROOT / "desktop" / "build" / "icon.ico",
    ROOT / "installer-native" / "icon.ico",
)
SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
         (64, 64), (96, 96), (128, 128), (256, 256)]


def main() -> None:
    image = Image.open(SOURCE).convert("RGBA")
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="ICO", sizes=SIZES, bitmap_format="png")
    print("assembled multi-resolution ICO: " + ", ".join(str(path) for path in TARGETS))


if __name__ == "__main__":
    main()
