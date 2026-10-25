# Bundled open fonts

Each upstream font is included unchanged, with its complete SIL Open Font
License 1.1 text and copyright/reserved-name notices. No Windows system or
commercial font is redistributed. These files do not need system installation.

| ID | Upstream source | Fixed source | Included font |
| --- | --- | --- | --- |
| wenkai | https://github.com/lxgw/LxgwWenKai | v1.530 / 4becfb4d5cf0c92cfdb60a6e9b504bda43315325 | LXGWWenKai-Regular.ttf |
| yozai | https://github.com/lxgw/yozai-font (master) | v0.868 / 90a91ca019faad7b7dae885a4f70a44359021333 | Yozai-Regular.ttf |
| chenyu | https://github.com/Chenyu-otf/chenyuluoyan_thin | 6e36815b0bec9f4f948298698d00b27a5f0b65c1 | ChenYuluoyan-2.0-Thin.ttf |

`manifest.json` records the exact download URL and SHA-256 for each font.
WenKai and Yozai hashes match the SHA-256 digests in their upstream GitHub
release asset metadata. Chenyu is downloaded from its immutable commit URL;
its SHA-256 is calculated from those downloaded bytes. Chenyu's upstream
filename says 2.0, while its internal name table reports Version 2.100. It is a
Thin family whose name-table subfamily is Regular, not an invented heavier face.

The complete license files are copied from these fixed URLs:

- `wenkai-OFL.txt`: https://raw.githubusercontent.com/lxgw/LxgwWenKai/4becfb4d5cf0c92cfdb60a6e9b504bda43315325/OFL.txt
- `yozai-OFL.txt`: https://raw.githubusercontent.com/lxgw/yozai-font/90a91ca019faad7b7dae885a4f70a44359021333/OFL.txt
- `chenyu-OFL.txt`: https://raw.githubusercontent.com/Chenyu-otf/chenyuluoyan_thin/6e36815b0bec9f4f948298698d00b27a5f0b65c1/license.txt

`coverage.json` records actual `fontTools.ttLib.TTFont.getBestCmap()` results,
the inspected sample, font name tables and license SHA-256 values. In this
fixed collection, WenKai and Yozai each map all 6,763 GB2312 Hanzi. Chenyu maps
4,333 of that set and lacks, for example, 简谐动长频势计证实验测质绳图习.
This is a named test set, not a claim of full Simplified/Traditional Chinese
coverage. Chenyu's Traditional Chinese glyphs must not be presented as covering
their missing Simplified Chinese counterparts.

For each absent glyph, use the verified WenKai fallback and record the fallback
in output metadata. If WenKai also lacks that codepoint, report the missing
character explicitly instead of silently substituting an empty box or changing
the text. Font fallback may visibly change handwriting style.

The bundled Chenyu source has an invalid TrueType hint program reported by the
tested Pillow/FreeType runtime as `too many function definitions`. Its cmap and
source hash remain valid. The renderer removes hint instructions only from an
in-memory copy and records `fonts.render_adjustments`; the distributed source
file is unchanged. That path was tested with Chinese text and fallback. The
doctor reports raw FreeType status separately from file integrity and cmap.
