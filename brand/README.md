# Brand images

| File | Size | Use |
| --- | --- | --- |
| `source.png` | 400x400 | Original logo, as uploaded |
| `icon.png` | 256x256 | Integration icon |
| `icon@2x.png` | 512x512 | Integration icon, high resolution |

`icon.png` and `icon@2x.png` are generated from `source.png`: the transparent
margin is trimmed, the logo is centred on a square transparent canvas and
resized. The source is 400x400, so `icon@2x.png` is slightly upscaled; replace
`source.png` with a larger version (512x512 or more) for a sharper result.

The logo is a trademark of ANWB. This integration is unofficial and not
endorsed by ANWB; the logo is only used to make the integration recognisable.

## Showing the icon in Home Assistant and HACS

Since Home Assistant 2026.3, custom integrations can ship their own brand
images. Copies of `icon.png` and `icon@2x.png` are in
`custom_components/anwb_laadpas/brand/`, so the icon shows up on the
integration pages once the integration is installed and Home Assistant has
restarted. A hard refresh of the browser (Ctrl+F5) may be needed.

On older Home Assistant versions, and in store previews before installation,
icons come from [brands.home-assistant.io](https://brands.home-assistant.io).
To get the icon there as well, open a pull request to
[home-assistant/brands](https://github.com/home-assistant/brands) that adds
both files to `custom_integrations/anwb_laadpas/`.

After regenerating, copy both files to `custom_components/anwb_laadpas/brand/`
too.

## Regenerating the icons

```bash
python - <<'PY'
from PIL import Image
im = Image.open("brand/source.png").convert("RGBA")
im = im.crop(im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox())
side = max(im.size)
sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
    sq.resize((size, size), Image.LANCZOS).save(f"brand/{name}", optimize=True)
PY
```
