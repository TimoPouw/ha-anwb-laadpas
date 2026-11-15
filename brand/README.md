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

Home Assistant and HACS load integration icons from
[brands.home-assistant.io](https://brands.home-assistant.io), which is built
from the [home-assistant/brands](https://github.com/home-assistant/brands)
repository. To get the icon there:

1. Fork `home-assistant/brands`.
2. Add `icon.png` and `icon@2x.png` from this folder to
   `custom_integrations/anwb_laadpas/` in your fork.
3. Open a pull request. The folder name must match the `domain` in
   `custom_components/anwb_laadpas/manifest.json`.

Once it is merged, the icon shows up in Home Assistant and HACS. Until then the
integration shows a placeholder icon.

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
