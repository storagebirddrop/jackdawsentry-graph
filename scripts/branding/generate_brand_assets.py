#!/usr/bin/env python3
"""Generate the Jackdaw Sentry brand asset pack from the canonical SVG."""

from __future__ import annotations

import json
import shutil
from io import BytesIO
from pathlib import Path

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

try:
    import cairosvg
except ImportError as exc:  # pragma: no cover - operator guidance
    raise SystemExit(
        "cairosvg is required for brand generation. Install it in .venv and rerun."
    ) from exc


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "assets/branding/jackdaw-sentry/source/jackdaw_sentry_vector_HD.svg"
GENERATED = ROOT / "assets/branding/jackdaw-sentry/generated"
FRONTEND = ROOT / "frontend"
APP_PUBLIC = ROOT / "frontend/app/public"


def render_png(size: int) -> Image.Image:
    png_bytes = cairosvg.svg2png(
        url=str(SOURCE),
        output_width=size,
        output_height=size,
    )
    return Image.open(BytesIO(png_bytes)).convert("RGBA")


def write_png(name: str, size: int, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    image = render_png(size)
    output = directory / name
    image.save(output)
    return output


def build_lockup(background: str, foreground: str, filename: str) -> None:
    icon = render_png(192)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 240">
  <rect width="900" height="240" rx="28" fill="{background}"/>
  <image href="data:image/png;base64,{icon_to_base64(icon)}" x="32" y="24" width="192" height="192"/>
  <text x="260" y="108" fill="{foreground}" font-family="Segoe UI, Arial, sans-serif" font-size="54" font-weight="700">Jackdaw Sentry</text>
  <text x="260" y="156" fill="{foreground}" font-family="Segoe UI, Arial, sans-serif" font-size="28" opacity="0.8">Investigation Graph</text>
</svg>
"""
    (GENERATED / filename).write_text(svg, encoding="utf-8")


def icon_to_base64(image: Image.Image) -> str:
    import base64

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def build_og_card() -> None:
    canvas = Image.new("RGB", (1200, 630), color="#09111f")
    draw = ImageDraw.Draw(canvas)
    icon = render_png(260)
    canvas.paste(icon, (84, 180), icon)

    title_font = ImageFont.load_default()
    subtitle_font = ImageFont.load_default()
    draw.text((390, 220), "Jackdaw Sentry", fill="#f8fafc", font=title_font)
    draw.text((390, 270), "Investigation Graph", fill="#93c5fd", font=subtitle_font)
    draw.text((390, 340), "Standalone graph exploration for trace-first investigations", fill="#cbd5e1", font=subtitle_font)
    canvas.save(GENERATED / "og-card.png")


def sync_frontend_assets(outputs: dict[str, Path]) -> None:
    FRONTEND.mkdir(parents=True, exist_ok=True)
    APP_PUBLIC.mkdir(parents=True, exist_ok=True)

    shutil.copy2(SOURCE, GENERATED / "favicon.svg")
    shutil.copy2(GENERATED / "favicon.svg", FRONTEND / "favicon.svg")
    shutil.copy2(GENERATED / "favicon.svg", APP_PUBLIC / "favicon.svg")

    frontend_targets = {
        "favicon.ico": FRONTEND / "favicon.ico",
        "favicon-16x16.png": FRONTEND / "favicon-16x16.png",
        "favicon-32x32.png": FRONTEND / "favicon-32x32.png",
        "favicon-48x48.png": FRONTEND / "favicon-48x48.png",
        "favicon-64x64.png": FRONTEND / "favicon-64x64.png",
        "apple-touch-icon.png": FRONTEND / "apple-touch-icon.png",
    }
    app_targets = {
        "favicon.ico": APP_PUBLIC / "favicon.ico",
        "favicon-16x16.png": APP_PUBLIC / "favicon-16x16.png",
        "favicon-32x32.png": APP_PUBLIC / "favicon-32x32.png",
        "favicon-48x48.png": APP_PUBLIC / "favicon-48x48.png",
        "favicon-64x64.png": APP_PUBLIC / "favicon-64x64.png",
        "apple-touch-icon.png": APP_PUBLIC / "apple-touch-icon.png",
        "icon-192.png": APP_PUBLIC / "icon-192.png",
        "icon-512.png": APP_PUBLIC / "icon-512.png",
        "maskable-192.png": APP_PUBLIC / "maskable-192.png",
        "maskable-512.png": APP_PUBLIC / "maskable-512.png",
        "og-card.png": APP_PUBLIC / "og-card.png",
    }

    for name, target in frontend_targets.items():
        shutil.copy2(outputs[name], target)
    for name, target in app_targets.items():
        shutil.copy2(outputs[name], target)

    manifest = {
        "name": "Jackdaw Sentry Graph",
        "short_name": "JDS Graph",
        "id": "/app/",
        "start_url": "/app/",
        "scope": "/app/",
        "icons": [
            {
                "src": "./icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": "./icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
            },
            {
                "src": "./maskable-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": "./maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "theme_color": "#09111f",
        "background_color": "#09111f",
        "display": "standalone",
    }
    (APP_PUBLIC / "site.webmanifest").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    GENERATED.mkdir(parents=True, exist_ok=True)

    outputs: dict[str, Path] = {}
    for size in (16, 32, 48, 64):
        outputs[f"favicon-{size}x{size}.png"] = write_png(
            f"favicon-{size}x{size}.png",
            size,
            GENERATED,
        )

    outputs["apple-touch-icon.png"] = write_png("apple-touch-icon.png", 180, GENERATED)
    outputs["icon-192.png"] = write_png("icon-192.png", 192, GENERATED)
    outputs["icon-512.png"] = write_png("icon-512.png", 512, GENERATED)
    outputs["maskable-192.png"] = write_png("maskable-192.png", 192, GENERATED)
    outputs["maskable-512.png"] = write_png("maskable-512.png", 512, GENERATED)

    ico_image = render_png(64)
    ico_path = GENERATED / "favicon.ico"
    ico_image.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )
    outputs["favicon.ico"] = ico_path

    build_lockup("#ffffff", "#09111f", "logo-lockup-light.svg")
    build_lockup("#09111f", "#f8fafc", "logo-lockup-dark.svg")
    build_og_card()
    outputs["og-card.png"] = GENERATED / "og-card.png"

    sync_frontend_assets(outputs)
    print("Brand assets generated.")


if __name__ == "__main__":
    main()
