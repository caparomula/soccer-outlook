# League artwork

Most league emblems come from ESPN's scoreboard feed. The assets here replace wide, overly detailed or outdated images with compact artwork from the competition organizers. All 34 tracked competitions were reviewed at small display sizes on 10 October 2026; the other emblems were retained where they were already clear or no better suitable official asset could be verified.

These files were downloaded unchanged on 10 October 2026:

| Competition | Local file | Official source and reason |
| --- | --- | --- |
| Liga MX | `liga-mx.png` | [192 × 192 PNG](https://ligamx.net/images/favicons_liga/android-chrome-192x192.png), identified by [Liga MX's icon manifest](https://ligamx.net/images/favicons_liga/manifest.json). The compact mark replaces ESPN's wide sponsor wordmark. |
| Premier League | `premier-league.png` | [196 × 196 PNG](https://www.premierleague.com/resources/v1.54.15/i/favicon/favicon-196x196.png), linked by the league's homepage. The lion fills the space without a tiny wordmark. |
| Champions League | `champions-league.svg` | [UEFA's standalone starball](https://img.uefa.com/imgml/uefacom/ucl/bottom-panel/ucl-starballIcon.svg), referenced by UEFA's competition-page styles. The symbol is larger without the accompanying text. |
| Concacaf Champions Cup | `concacaf-champions-cup.svg` | [Current cup symbol](https://images.concacaf.com/image/private/t_q_good/v1748245386/prd/assets/logos/champions-cup/CCC_Icon_Secondary_Color_RGB_fk2wi0.svg), used in [Concacaf's navigation](https://www.concacaf.com/). Replaces ESPN's obsolete Champions League branding. |
| Concacaf Nations League | `concacaf-nations-league.png` | [125 × 119 PNG](https://images.concacaf.com/image/private/t_q_good/v1784827653/prd/assets/logos/nations-league/NEW%20BRAND/Concacaf_Nations_League_Master_Brand_Icon_Only_biimcu.png), used in Concacaf's navigation. Replaces the old wide CNL wordmark. |

The builder embeds these files in both normal and embedded-image builds, avoiding a runtime dependency on the organizers' image servers. They also replace older copies in the optional image cache. `--no-logos` still omits them.

Artwork keeps its transparency with no added background or frame. The existing selective contrast adjustment still applies to colored marks. The white Champions League starball uses a CSS brightness filter to appear black in the light theme; its source file remains unchanged.

The official Saudi Pro League navigation SVG was also tested, but its pale lettering lost contrast in the light theme, so the existing ESPN rendition was retained.

Remaining limitations: the USL Championship image is still a wide wordmark, and ESPN's Ligue 1 image uses older branding. The available alternatives were not verified or were unsuitable for a compact icon in both themes; revisit these when better official assets become available.

These are third-party league marks, not original project artwork. They remain subject to their owners' rights and are not covered by the project's zlib license.
