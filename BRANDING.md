# CitiFoxCharger visual identity

The original CitiFoxCharger mark combines three visual ideas in one symbol:

- A compact electric city car in three-quarter view, recalling the Citigo used by this installation.
- A red fox tail that wraps behind the car and continues into a charging cable.
- An EV plug with a lime lightning bolt for charging and electricity.

The user-selected design uses smooth surfaces and curved contours and a recognizable compact car silhouette. The separate animated dashboard car illustration is unchanged. This is a personal application identity, not an official Škoda or SMARTFOX logo or a claim of manufacturer endorsement. Existing manufacturer marks were not copied into the assets.

## Files

| Asset | File | Format / dimensions | Use |
|---|---|---|---|
| Standalone symbol | [icon.png](citifoxcharger/web/brand/icon.png) | Transparent RGBA PNG, 1254 × 1254 | Header, browser favicon and touch icon; downloadable app artwork. |
| Horizontal logo | [logo.png](citifoxcharger/web/brand/logo.png) | Transparent RGBA PNG, 2172 × 724 | Documentation and larger logo placements. |

The PNGs are generated artwork, not editable SVG sources. Keep the original aspect ratio. The header renders the symbol at 36 × 36 CSS pixels, or 30 × 30 in the compact mobile header, to preserve the tablet layout. The wordmark in the header remains live text for clarity and theme contrast; the horizontal artwork is used in the README.

The palette uses Škoda-inspired emerald and electric greens, SMARTFOX-inspired red, and ivory highlights. The original artwork does not use manufacturer logos or lettering. The transparent symbol works with both existing light and dark dashboard surfaces. The horizontal logo's dark wordmark is intended primarily for light backgrounds.

Assets are served only through the backend's explicit `/icon.png` and `/logo.png` static routes with the `image/png` content type. The HTML declares the icon with `rel="icon"` and `rel="apple-touch-icon"`; no external image host or font service is used. These links provide artwork for browsers and saved shortcuts; they do not install a PWA, Windows executable or shortcut automatically.

The user-selected smooth horizontal logo was recolored from copper to SMARTFOX-inspired red, retaining the car, tail and plug composition. Its matching standalone icon was derived with the built-in image generation tool; both assets are stored in the project. Originals remain separate from runtime configuration and credentials. No vehicle identifier or API key is present in the artwork.


## Charging animation car (1.8.10)

`citifoxcharger/web/brand/charging-car.png` is a transparent raster sprite derived from the logo car with the built-in image generation tool. It replaces the car drawing in the energy-flow animation; fixed CSS dimensions preserve the dashboard height. The existing pulse and reduced-motion preference apply to the image. The station's small line icon remains part of the existing energy icon set.

Final edit prompt: Preserve the logo car silhouette, perspective, cabin, wheels and headlights; remove lettering, fox, plug and cable; simplify to emerald, pale mint/cream and charcoal; use clean transparent edges and no background plate. Keep it readable as a small charging indicator. The generated rendering includes a subtle green halo.
