# Textures

Drop images here and point `[textures]` in `config.toml` at them (e.g. `ground = "assets/ground.png"`).
Anything left empty keeps the built-in drawing, so you can swap textures in one at a time.
PNG with transparency works best. Images are scaled to the sizes below when the game starts,
so author them at that size (or a clean multiple: 64x64, 128x128 for tiles).

| key | what | drawn at (px) |
|---|---|---|
| `ground` | laterite street | 32x32 tile |
| `yard` | swept courtyard inside a lot | 32x32 tile |
| `road` | asphalt (centre dashes are still drawn on top) | 32x32 tile |
| `water` | barrage | 32x32 tile |
| `grass` | roundabout garden | 32x32 tile |
| `house` | roof tile, multiplied by each house's colour: keep it light/grey | 32x32 tile |
| `wall_high` / `wall_low` / `wall_glass` | wall seen from above; only the band through the middle shows (18 px for high, 14 for the others), corners and junctions are cut from the same image | 32x32 tile |
| `gate` | metal gate in an east-west wall (rotated for north-south walls) | 32x32 tile |
| `bin` | trash bin (searched bins are drawn darker) | 14x16 |
| `lamp` | street lamp post, seen from above | 12x12 |
| `player` | you, top-down | 24x24 |
| `portrait` | your face for the HUD corner (front view, transparent background); centre-cropped to a square | 128x128 |
| `djinn` | djinn (its glowing eyes are still drawn on top) | 26x40 |
| `cop`, `cop_<Type>` | police, top-down; `cop` is the fallback for types without their own | 24x24 |

The dialogue box has its own skin settings under `[dialogue]` (font, box texture, portraits).
