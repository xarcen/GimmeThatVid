"""Derive the app's image assets from GimmeThatVid.png.

Writes:
  gimmethatvid/ui/logo.png       artwork with the black turned into transparency
  gimmethatvid/assets/icon.ico   rounded black tile, all the sizes Windows asks for
  gimmethatvid/assets/icon.png   256 px version of that tile

The source is glowing artwork on pure black, so "unscreening" it (alpha = the
brightest channel, colour divided back out) keeps the glow soft instead of
cutting a hard edge around it.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "GimmeThatVid.png"
UI_DIR = ROOT / "gimmethatvid" / "ui"
ASSET_DIR = ROOT / "gimmethatvid" / "assets"

ICON_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def crop_to_artwork(image, padding):
    """Square crop centred on everything brighter than the black background."""
    bbox = image.convert("L").point(lambda v: 255 if v > 6 else 0).getbbox()
    left, top, right, bottom = bbox
    cx, cy = (left + right) / 2, (top + bottom) / 2
    side = max(right - left, bottom - top) * (1 + 2 * padding)
    box = tuple(round(v) for v in (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2))
    return image.crop(box)          # areas outside the source come back black


def unscreen(image):
    rgb = np.asarray(image.convert("RGB")).astype(np.float32)
    alpha = rgb.max(axis=2)
    alpha[alpha < 6] = 0                                   # drop compression noise
    colour = np.clip(rgb * 255.0 / np.maximum(alpha, 1.0)[..., None], 0, 255)
    return Image.fromarray(np.dstack([colour, alpha]).astype(np.uint8), "RGBA")


def rounded_tile(artwork, size=1024, radius_ratio=0.225, supersample=4):
    big = size * supersample
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=round(big * radius_ratio), fill=255
    )
    mask = mask.resize((size, size), Image.LANCZOS)

    tile = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    art = artwork.convert("RGB").resize((size, size), Image.LANCZOS)
    tile.paste(art, (0, 0))
    tile.putalpha(mask)
    return tile


def main():
    source = Image.open(SOURCE)
    UI_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)

    logo = unscreen(crop_to_artwork(source, padding=0.10)).resize((512, 512), Image.LANCZOS)
    logo.save(UI_DIR / "logo.png", optimize=True)

    # Less padding on the icon: at 16-32 px every pixel of artwork counts.
    tile = rounded_tile(crop_to_artwork(source, padding=0.04))
    tile.resize((256, 256), Image.LANCZOS).save(ASSET_DIR / "icon.png", optimize=True)
    tile.save(ASSET_DIR / "icon.ico", sizes=[(s, s) for s in ICON_SIZES])

    for path in (UI_DIR / "logo.png", ASSET_DIR / "icon.png", ASSET_DIR / "icon.ico"):
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
