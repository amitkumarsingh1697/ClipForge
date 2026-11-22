# ClipForge pitch deck: technical notes

## What was built and how

| Layer | Tool | Output |
|---|---|---|
| 3D visuals | `assets.js`: Playwright + Chromium CSS 3D (perspective, rotateX/Y, translateZ), rendering the real product screenshots | Transparent PNGs, JPEG backgrounds, `hero_loop.mp4` (frames + FFmpeg) |
| Slides | `build_deck.js`: PptxGenJS 3.12, 16:9 widescreen (13.33 × 7.5 in) | Theme, 5 slide layouts, 24 slides, native charts, speaker notes |
| Motion | `motion.py`: injects PowerPoint XML (transitions + timing) into each slide | Morph / Fade transitions and auto-playing entrance builds |
| Theme colours | the pptx skill's `apply_theme.js` | Theme "ClipForge Midnight" (colours + Manrope/Inter) |
| Deck PDF | LibreOffice export + an "About this PDF edition" page (`merge_pdf.py`) | `ClipForge_Client_Pitch_Deck.pdf` |
| Design guide | `build_pdfs.js`: HTML → PDF in Chromium | `ClipForge_Presentation_Design_Guide.pdf` |
| Presenter script | `build_script_docx.js`: docx-js, from `script.json` | `ClipForge_Presenter_Script.docx` |

`script.json` is the single source for the speaker content: it feeds both the PPTX speaker notes and the Word script.

## Motion, exactly as implemented

**Native PowerPoint transitions**
- Morph (by object, 1.6 s) on slides 2, 7 and 24. Pairs are matched by object name: `!!hero` (cover hero → executive-summary hero), `!!stage` (reveal screen → showcase), `!!logo` (CTA logo → closing logo). Written inside `mc:AlternateContent`, so PowerPoint versions without Morph fall back to a Fade.
- Fade (0.8 s) on every other slide.

**Native PowerPoint entrance animations**
- 562 animated objects across 24 slides, all in one "With previous" group per slide that starts automatically when the slide appears. Delays stagger them in reading order; a slide's build completes in about 1.5 to 2.8 s.
- Effects: Fade (presetID 10), Float In: fade + 0.04 rise with ease-out (presetID 42), Zoom: fade + scale 85% → 100% with ease-out (presetID 53), Wipe from left (presetID 22). Charts animate as one object.
- To change one: Animations › Animation Pane. Effects keep their timing if you change the effect type.

**Not implemented / not claimed**
- No interactive 3D models. The 3D look comes from static renders of real screenshots.
- No embedded video in the PPTX. `hero_loop.mp4` is supplied separately.
- No animated GIFs, no exit animations, no sound.

## Playback requirements and compatibility

| App | Slides & fonts | Entrance builds | Morph | Notes |
|---|---|---|---|---|
| PowerPoint for Microsoft 365 / 2019+ (Windows, Mac) | Yes (install fonts) | Yes | Yes | Recommended for presenting |
| PowerPoint 2016 | Yes | Yes | Falls back to Fade | |
| PowerPoint for the web | Yes (web fonts may substitute) | Yes | Usually | Check fonts first |
| Keynote | Yes | Most | Converted to Magic Move or Dissolve | Check slide 2 and 7 |
| Google Slides | Yes (fonts may substitute) | Dropped | Fade | Use the PDF instead, or accept a static deck |
| LibreOffice Impress | Yes | Partly | Fade | Used here for QA rendering |
| PDF | Final built state of every slide | n/a | n/a | Last page explains the motion and the illustrative numbers |

**Fonts.** Install `fonts/Manrope-*.ttf` and `fonts/Inter-*.ttf` on the presenting computer before opening the deck (double-click › Install on Mac or Windows). Without them PowerPoint substitutes a default sans and some titles may wrap differently. The PDF has the fonts embedded.

**File size.** PPTX ≈ 11 MB, PDF ≈ 3 MB.

## Quality checks that were run

- `validate.py` (pptx skill: schema, relationships, content types, charts, slide XML): **passed** on the final PPTX.
- Every slide rendered to an image through LibreOffice and inspected in two passes; fixed: title wrapping and alignment, cover headline overlap, chip and label overflow, a wrapped card title, footnote collisions, architecture column width.
- python-pptx re-opens the file: 24 slides, 24 with speaker notes, fonts used are only Manrope and Inter.
- Presenter script rendered to PDF and checked (25 pages, a thumbnail per slide).
- **Not tested:** opening the PPTX in Microsoft PowerPoint itself (not available in the build environment). The transition and animation XML follows PowerPoint's own format and passes validation, but please do one rehearsal run in PowerPoint before a client meeting, especially slides 2, 7 and 24 for Morph.

## Editing and reuse

**Quick edits in PowerPoint**
- Text, cards, diagrams and tables are native and editable. Titles and kickers are layout placeholders ("Title", "Kicker" in the Selection Pane).
- Chart numbers: right-click the chart › Edit Data.
- Colours and fonts: Design › Variants › Colours / Fonts (theme "ClipForge Midnight").
- Replacing a Morph image: keep its name (`!!hero`, `!!stage`, `!!logo`) in Selection Pane, or the Morph falls back to a cross-fade.
- New prospect: fill in slide 23's contact line; adjust the CTA headline to their content type (stream, episode, video).

**Rebuilding from source** (for bigger changes)
```bash
cd source
npm install                                 # pptxgenjs, docx, sharp, react, react-dom, react-icons (+ playwright)
export PPTX_SKILL=/path/to/pptx-skill       # provides apply_theme.js, validate.py, soffice.py
./build.sh                                  # VIDEO=1 ./build.sh also re-renders hero_loop.mp4
```
Needs Node 18+, Python 3 with `pypdf` and `python-pptx`, LibreOffice, Poppler (`pdftoppm`), FFmpeg (video only), and the Manrope/Inter fonts installed. Edit slide content in `build_deck.js`, speaker content in `script.json`, visuals in `assets.js`, motion timings next to each element (`["rise", delay_ms, duration_ms]`).

## Known limitations

- Morph and builds were verified structurally and by schema, not by playing in Microsoft PowerPoint (see above).
- All numbers are illustrative and labelled so; there is no real usage, revenue or customer data yet.
- The Telegram message on slide 12 is a native mock-up of the bot's real message format, not a screenshot.
- `progress.jpg` contains a real streamer's name in clip titles (see the asset inventory).
- Platform rules quoted (YouTube Shorts length and API quota, Instagram hashtag cap, Facebook Reels length, TikTok audit) come from ClipForge's documentation as of November 2026 and can change.
