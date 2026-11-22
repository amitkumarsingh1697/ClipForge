# ClipForge pitch deck: asset inventory

Every visual in `ClipForge_Client_Pitch_Deck.pptx`, where it came from, and what should be replaced over time. Paths are relative to `source/` in the delivery folder.

## 1. Source screenshots (real product)

Copied unchanged from the ClipForge repository (`docs/screenshots/`, commit `97dc47b`, November 2026). They show the real app; the video inside them is generated demo footage, except `progress.jpg`, whose clip titles name a real stream ("GTA 6 MARATHON | Kai Cenat").

| File | Shows | Used in |
|---|---|---|
| `assets/screens/clips.jpg` | Clips page with four scored clip cards | Hero (slides 1, 2), showcase (7), reveal (6) |
| `assets/screens/create.jpg` | Create page: source, shape, style | Showcase (7), How it works (9) |
| `assets/screens/progress.jpg` | Live step-by-step progress (light theme) | Showcase (7), How it works (9) |
| `assets/screens/edit.jpg` | Edit dialog: title, hook, caption, cover | How it works (9) |
| `assets/screens/schedule.jpg` | Approve and publish / schedule dialog | How it works (9) |
| `assets/screens/publish.jpg` | Publishing page: YouTube, Instagram, Facebook, TikTok | Why us (22) |
| `assets/screens/mobile.jpg` | App on a phone | User experience (12) |
| `assets/screens/clip-frames.jpg` | Three frames of a finished vertical clip | Split into `frame1-3.png` |
| `assets/screens/frame1.png`, `frame2.png`, `frame3.png` | Crops of `clip-frames.jpg` (cover + hook, pop captions, payoff) | Hero (1, 2), anatomy (11) |
| `assets/screens/ai.jpg`, `earn.jpg` | AI & brand page, Earn page | Rendered (`tilt_ai`, `tilt_earn`) but not placed; available for variants |

## 2. Generated 3D renders (built from the screenshots above)

Rendered by `assets.js` with Chromium CSS 3D transforms (perspective, rotateX/Y, translateZ), glass rim highlights and radial light. These are **static images**, not interactive 3D models.

| File | Description | Used in |
|---|---|---|
| `assets/generated/hero.png` | Clips page tilted in 3D with three floating finished clips (transparent PNG) | 1, 2 (Morph pair `!!hero`) |
| `assets/generated/showcase.png` | Create, Clips and Progress stacked in depth | 7 (Morph pair `!!stage`) |
| `assets/generated/tilt_clips.png` | Clips page, single tilted glass screen | 6 (Morph pair `!!stage`) |
| `assets/generated/tilt_create.png`, `tilt_progress.png`, `tilt_edit.png`, `tilt_schedule.png` | Step screens | 9 |
| `assets/generated/tilt_publish.png` | Publishing page | 22 |
| `assets/generated/tilt_ai.png`, `tilt_earn.png` | Spare renders | not used |
| `assets/generated/phone.png` | `mobile.jpg` in a tilted phone frame | 12 |
| `assets/generated/frame1_glass.png` … `frame3_glass.png` | Clip frames in glass phone frames | 11 |
| `assets/generated/logo.png` | ClipForge mark redrawn from the app's own icon (pink-violet gradient, play glyph) | 1, 23, 24 (Morph pair `!!logo`) |
| `assets/generated/bg_cover.jpg`, `bg_content.jpg`, `bg_right.jpg`, `bg_problem.jpg`, `bg_center.jpg` | Midnight backgrounds with violet / cyan / pink light and a perspective grid floor | Slide layouts (COVER, CONTENT, CONTENT_RIGHT, PROBLEM, CENTER) |

## 3. Motion assets

| File | Type | Notes |
|---|---|---|
| `hero_loop.mp4` (also `source/assets/generated/hero_loop.mp4`) | MP4, H.264, 1920×1080, 5 s seamless loop | The hero composition gently rotating. **Not embedded in the PPTX** (to keep the deck light and avoid autoplay issues). Use it for intros, a website header or social posts, or insert it on slide 1 yourself (Insert › Video). |

Native PowerPoint motion (no asset files): 3 Morph transitions, Fade transitions on the other slides, and 562 entrance animations. See the technical notes.

## 4. Native, editable elements

Drawn as PowerPoint shapes, so they can be edited directly: all cards, icons in circles, the six-hour timeline (4), the pipeline diagram (8), the before/after columns (13), the architecture diagram (18), the roadmap (20), the adoption timeline (21), the Telegram message mock-up (12), and all text.

Native charts (edit data with right-click › Edit Data): minutes per clip (5), clips per 2-hour block (15), weekly hours by volume (16). ROI inputs are a native table (16).

Icons: Feather icon set (`react-icons/fi`, MIT licence), rasterised to PNG at 256 px.

## 5. Fonts

| Font | Use | File | Licence |
|---|---|---|---|
| Manrope (Regular, Medium, SemiBold, Bold, ExtraBold) | Headings, numbers | `fonts/Manrope-*.ttf` | SIL Open Font License 1.1 (`fonts/Manrope-OFL.txt`) |
| Inter (Regular, Italic, Bold, Bold Italic) | Body text | `fonts/Inter-*.ttf` | SIL Open Font License 1.1 (`fonts/Inter-LICENSE.txt`) |

## 6. Replace or add before wider use

| Priority | Item | Why |
|---|---|---|
| High | Contact placeholders on slide 23 (`[Your name]`, `[email@yourdomain.com]`, `[phone or Telegram]`) | Must be filled in per prospect |
| High | Illustrative numbers (slides 2, 5, 13, 15, 16) | Replace with measured pilot data once available: minutes per clip, approval rate, views per clip, payout per 1K views |
| Medium | `progress.jpg` (slides 7, 9) | Shows a real streamer's name in clip titles; re-capture it with demo footage before sending the deck widely |
| Medium | A real Telegram screenshot (slide 12) | The current message is a faithful mock-up of the bot's format, drawn natively |
| Medium | Customer proof: results from your own channels or a pilot client (with permission) | No case studies exist yet, so the deck makes no performance claims |
| Low | A short screen recording of the app making a clip | Could replace or complement the showcase on slide 7 |
| Low | Company name, logo lockup and legal entity, if the service is sold under a different brand than ClipForge | The deck currently uses ClipForge as the brand |
