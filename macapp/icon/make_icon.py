#!/usr/bin/env python3
"""
make_icon.py - render UseMeUp's icons from the one design below.

    pip install cairosvg pillow
    python3 macapp/icon/make_icon.py

Writes, next to this file:
  favicon.svg               the page favicon (the mark alone, no background)
  favicon-32.png            fallback for browsers without SVG favicons
  apple-touch-icon.png      180 px on a white square, for a page pinned on a
                            phone or iPad (iOS fills transparency with black)
  AppIcon.iconset/          the Mac app icon at every size iconutil wants

The design is "the U drains": the letter U is the meter. Green is what has
been used, the grey arm is what is left, and the amber dot is where you are
now. Colours are the menu bar's green and amber.

No dark background anywhere: green on near-black read poorly. The favicon
is the bare mark, cropped to fill the tab slot; the app icon sits on a white
tile, because macOS boxes icons that bring no tile of their own in grey.

build.sh turns AppIcon.iconset into AppIcon.icns with iconutil, so the PNGs
are committed and a build needs no Python imaging libraries. Rerun this only
when the design changes.
"""
import io
import os

import cairosvg
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))

GREEN, AMBER, TRACK, TILE = "#1F9D55", "#F5B82E", "#9AA0A8", "#FFFFFF"

# The mark on a 64-unit grid, without its background.
MARK = f'''
<path d="M18 13 V35 a14 14 0 0 0 28 0 V13" fill="none" stroke="{TRACK}" stroke-width="11" stroke-linecap="round"/>
<path d="M18 13 V35 a14 14 0 0 0 26.2 6.8" fill="none" stroke="{GREEN}" stroke-width="11" stroke-linecap="round"/>
<circle cx="44.2" cy="41.8" r="6.6" fill="{AMBER}"/>'''

# Favicon: the bare mark on a transparent canvas. The viewBox is cropped to
# the mark (x 12.5-51.5, y 7.5-55.5) so the U fills the 16 px tab slot.
FAVICON = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="7.5 7 49 49">'
           f'{MARK}</svg>')

# Touch icon: iOS masks the corners itself and paints transparency black, so
# this one is a full white square.
TOUCH = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
         f'<rect width="64" height="64" fill="{TILE}"/>{MARK}</svg>')

# Mac app icon: Apple's grid puts an 824 px rounded square inside a 1024 px
# canvas, leaving room for the shadow. The mark scales with the square.
S = 824 / 64
APP = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">'
       f'<rect x="100" y="100" width="824" height="824" rx="185" fill="{TILE}"/>'
       f'<g transform="translate(100 100) scale({S})">{MARK}</g></svg>')


def png(svg, size):
    return Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg.encode(),
                                                   output_width=size, output_height=size))).convert("RGBA")


def app_master():
    """1024 px app icon with the soft drop shadow macOS icons carry."""
    art = png(APP, 1024)
    shadow = Image.new("RGBA", art.size, (0, 0, 0, 0))
    alpha = art.split()[3].point(lambda a: int(a * 0.35))
    shadow.putalpha(alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    canvas = Image.new("RGBA", art.size, (0, 0, 0, 0))
    canvas.alpha_composite(shadow, (0, 10))
    canvas.alpha_composite(art)
    return canvas


def main():
    with open(os.path.join(HERE, "favicon.svg"), "w") as f:
        f.write(FAVICON)
    png(FAVICON, 32).save(os.path.join(HERE, "favicon-32.png"), optimize=True)
    png(TOUCH, 180).save(os.path.join(HERE, "apple-touch-icon.png"), optimize=True)

    master = app_master()
    out = os.path.join(HERE, "AppIcon.iconset")
    os.makedirs(out, exist_ok=True)
    for pt in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = pt * scale
            name = "icon_%dx%d%s.png" % (pt, pt, "@2x" if scale == 2 else "")
            master.resize((px, px), Image.LANCZOS).save(os.path.join(out, name), optimize=True)
    master.save(os.path.join(HERE, "AppIcon-1024.png"), optimize=True)
    print("wrote favicon.svg, favicon-32.png, apple-touch-icon.png, AppIcon.iconset, AppIcon-1024.png")


if __name__ == "__main__":
    main()
