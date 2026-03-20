# Jackdaw Sentry Branding

## Source Of Truth

- Canonical source SVG:
  `assets/branding/jackdaw-sentry/source/jackdaw_sentry_vector_HD.svg`

## Generated Outputs

The generated pack in `assets/branding/jackdaw-sentry/generated/` includes:

- `favicon.ico`
- PNG favicons at `16`, `32`, `48`, and `64`
- `apple-touch-icon.png`
- `icon-192.png` and `icon-512.png`
- `maskable-192.png` and `maskable-512.png`
- `logo-lockup-light.svg` and `logo-lockup-dark.svg`
- `og-card.png`
- `favicon.svg`

## Regeneration

Run:

```bash
python scripts/branding/generate_brand_assets.py
```

The script regenerates the asset pack and syncs the runtime icons into:

- `frontend/`
- `frontend/app/public/`

## Usage Notes

- Use `logo-lockup-light.svg` on light backgrounds.
- Use `logo-lockup-dark.svg` on dark backgrounds.
- Preserve the padding already baked into the exported artboards.
- Do not hand-edit generated assets; regenerate them from the source SVG.
