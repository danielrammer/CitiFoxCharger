# CitiFoxCharger visual identity

The original CitiFoxCharger mark combines three visual ideas in one symbol:

- A compact electric city-car front, recalling the Citigo used by this installation.
- A fox face and ears, recalling the SMARTFOX energy manager.
- A central lightning bolt for charging and electricity.

The car keeps the friendly smile and wide wheel spacing used by the dashboard's charging illustration. This is a personal application identity, not an official Škoda or SMARTFOX logo or a claim of manufacturer endorsement. Existing manufacturer marks were not copied into the assets.

## Files

| Asset | File | Format / dimensions | Use |
|---|---|---|---|
| Standalone symbol | [icon.png](citifoxcharger/web/brand/icon.png) | Transparent RGBA PNG, 1254 × 1254 | Header, browser favicon and touch icon; downloadable app artwork. |
| Horizontal logo | [logo.png](citifoxcharger/web/brand/logo.png) | Transparent RGBA PNG, 2172 × 724 | Documentation and larger logo placements. |

The PNGs are generated artwork, not editable SVG sources. Keep the original aspect ratio. The header renders the symbol at 36 × 36 CSS pixels, or 30 × 30 in the compact mobile header, to preserve the tablet layout. The wordmark in the header remains live text for clarity and theme contrast; the horizontal artwork is used in the README.

The palette follows the dashboard: forest green and lime, with warm copper fox details and ivory highlights. The transparent symbol works with both existing light and dark dashboard surfaces. The horizontal logo's dark wordmark is intended primarily for light backgrounds.

Assets are served only through the backend's explicit `/icon.png` and `/logo.png` static routes with the `image/png` content type. The HTML declares the icon with `rel="icon"` and `rel="apple-touch-icon"`; no external image host or font service is used. These links provide artwork for browsers and saved shortcuts; they do not install a PWA, Windows executable or shortcut automatically.

Both assets were created with the built-in image generation tool and copied into the project. Originals remain separate from runtime configuration and credentials. No vehicle identifier or API key is present in the artwork.
