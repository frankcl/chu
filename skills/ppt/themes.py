"""PPT theme palettes — the single source of truth shared by the renderer and UI.

`build.py` imports `THEMES` from here to style decks; the API server imports
`theme_previews()` to feed the frontend's template-preview cards, so the previews
the user sees always match what `build.py` actually draws (gradient/solid
background + colored title band + body text + footer bar).

Pure data + tiny helpers only — no heavy imports — so both a subprocess script
run and an importlib path-load stay cheap.
"""

# Each theme is a palette of hex colors (no leading '#') + an optional font.
# A value of None means "leave the template/default styling untouched" — so the
# "default" theme reproduces the prior look exactly (deep-blue table header,
# no forced title/body color, no background fill, theme font).
THEMES = {
    "default": {
        "title": None, "text": None, "background": None, "bg2": None, "font": None,
        "band": None, "band_text": None, "footer": None,
        "table_header": "1F4E79", "table_header_text": "FFFFFF", "table_body": None,
    },
    "business-blue": {
        "title": "1F4E79", "text": "222222", "background": "FFFFFF", "bg2": "EAF1FB",
        "font": "Calibri", "band": "1F4E79", "band_text": "FFFFFF", "footer": "1F4E79",
        "table_header": "2E75B6", "table_header_text": "FFFFFF", "table_body": "FFFFFF",
    },
    # Dark deck: bands/footer use a clearly brighter blue than the near-black
    # background so the header/footer read as distinct bars (the old 0F3460 band
    # sat too close to the 16213E background). Table cells get an explicit dark
    # fill so the light body text is not painted onto python-pptx's default light
    # table style (which made table text unreadable).
    "tech-dark": {
        "title": "FFFFFF", "text": "EAEAEA", "background": "1A1A2E", "bg2": "16213E",
        "font": "Calibri", "band": "2E6CB5", "band_text": "FFFFFF", "footer": "2E6CB5",
        "table_header": "2E6CB5", "table_header_text": "FFFFFF", "table_body": "16213E",
    },
    # Light/clean deck: a solid dark-gray band stands out on the white background
    # (the old near-white F2F2F2 band was effectively invisible).
    "minimal": {
        "title": "111111", "text": "333333", "background": "FFFFFF", "bg2": None,
        "font": "Calibri Light", "band": "333333", "band_text": "FFFFFF", "footer": "333333",
        "table_header": "333333", "table_header_text": "FFFFFF", "table_body": "FFFFFF",
    },
}

# Friendly display names (shown under each preview card). Order = picker order.
THEME_LABELS = {
    "default": "默认 · 简洁",
    "business-blue": "商务蓝",
    "tech-dark": "科技深色",
    "minimal": "极简",
}

# A bit of representative content so the mockup reads like a real slide.
_PREVIEW_SAMPLE = {
    "title": "标题示例",
    "body": ["正文示例第一行……", "正文示例第二行……"],
}

# Colors the preview card needs (the visible style); a subset of the full palette.
_PREVIEW_COLOR_KEYS = ("background", "bg2", "band", "band_text", "text", "title", "footer")


def theme_previews() -> list[dict]:
    """Return per-theme data for the frontend preview cards, in picker order.

    Each entry: {name, label, colors{...}, sample{title, body[]}}. Colors are hex
    strings without '#', or None where the theme leaves that element unstyled
    (e.g. the plain `default` theme has no band/background/footer).
    """
    out = []
    for name, label in THEME_LABELS.items():
        palette = THEMES.get(name, {})
        out.append({
            "name": name,
            "label": label,
            "colors": {k: palette.get(k) for k in _PREVIEW_COLOR_KEYS},
            "sample": dict(_PREVIEW_SAMPLE),
        })
    return out
