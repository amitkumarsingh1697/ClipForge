// 1) ClipForge_Presentation_Design_Guide.pdf (HTML -> PDF via Chromium)
// 2) "About this PDF edition" page appended to the deck PDF (merged in merge_pdf.py)
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const OUT = path.join(__dirname, "out");
const b64 = (f) => fs.readFileSync(f).toString("base64");
const font = (file, w) => `@font-face{font-family:Manrope;font-weight:${w};src:url(data:font/ttf;base64,${b64(path.join(__dirname, "fonts", file))})}`;
const FONTS = font("Manrope-Regular.ttf", 400) + font("Manrope-SemiBold.ttf", 600) + font("Manrope-Bold.ttf", 700) + font("Manrope-ExtraBold.ttf", 800);
const thumb = (n) => `data:image/jpeg;base64,${b64(path.join(OUT, "render", `s-${String(n).padStart(2, "0")}.jpg`))}`;
const logo = `data:image/png;base64,${b64(path.join(__dirname, "assets/generated/logo.png"))}`;

const css = `${FONTS}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Manrope,Arial,sans-serif;color:#1A1838;font-size:11pt;line-height:1.55}
.page{width:210mm;min-height:297mm;padding:18mm 18mm 16mm;page-break-after:always;position:relative}
.dark{background:#07071A;color:#F5F5FF}
h1{font-family:Manrope;font-weight:800;font-size:34pt;line-height:1.05;letter-spacing:-.5px}
h2{font-family:Manrope;font-weight:800;font-size:20pt;margin-bottom:4mm;letter-spacing:-.3px}
h3{font-family:Manrope;font-weight:700;font-size:12pt;margin:5mm 0 2mm}
.k{font-size:8.5pt;font-weight:700;letter-spacing:2.5px;color:#0E9FB8;text-transform:uppercase;margin-bottom:2mm}
.dark .k{color:#22D3EE}
p{margin-bottom:2.5mm}.muted{color:#5E6390}.dark .muted{color:#A3A8CC}
table{width:100%;border-collapse:collapse;margin:2mm 0 4mm;font-size:9.5pt}
th{text-align:left;font-size:8pt;letter-spacing:1.5px;text-transform:uppercase;color:#5B3FD9;border-bottom:1.5px solid #CFC8FF;padding:2mm}
td{border-bottom:1px solid #E4E3F0;padding:2mm;vertical-align:top}
.sw{display:grid;grid-template-columns:repeat(4,1fr);gap:3mm;margin:3mm 0 5mm}
.chip{border-radius:3mm;overflow:hidden;border:1px solid #E4E3F0}
.chip .c{height:14mm}.chip .t{padding:2mm;font-size:8.5pt}.chip b{display:block;font-family:Manrope;font-size:10pt}
.grad{height:24mm;border-radius:3mm;margin-bottom:2mm}
.thumbs{display:grid;grid-template-columns:repeat(3,1fr);gap:3mm}
.thumbs img{width:100%;border-radius:1.5mm;display:block}
.thumbs div{font-size:8pt;color:#5E6390}
ul{margin:0 0 3mm 5mm}li{margin-bottom:1.2mm}
.do{display:grid;grid-template-columns:1fr 1fr;gap:5mm}
.box{border:1px solid #E4E3F0;border-radius:3mm;padding:4mm}
.foot{position:absolute;bottom:9mm;left:18mm;right:18mm;font-size:7.5pt;color:#8A8EB0;display:flex;justify-content:space-between}
`;
const foot = (n) => `<div class="foot"><span>ClipForge · Presentation design guide</span><span>${n}</span></div>`;

const palette = [
  ["Midnight", "#07071A", "Primary background (60–70% of every slide)"], ["Deep indigo", "#0D0B24", "Secondary background, inner panels"],
  ["Surface", "#15132F", "Cards (18–30% transparency over the glow)"], ["Surface 2", "#1E1B40", "Raised cards, timeline tracks"],
  ["Electric violet", "#7C5CFF", "Primary brand accent: key shapes, ClipForge data series"], ["Soft violet", "#A78BFA", "Labels, icons, numbering"],
  ["Cyan", "#22D3EE", "Secondary accent: kickers, arrows, 'with ClipForge' states"], ["Pink", "#F472B6", "Problem and pain states (from the product's own brand gradient)"],
  ["Amber", "#F5B544", "Caution only: ILLUSTRATIVE chips, risks"], ["Green", "#34D399", "Success and mitigations"],
  ["White", "#F5F5FF", "Headlines and primary text"], ["Slate", "#A3A8CC", "Body and supporting text"], ["Dim slate", "#6B7099", "Captions, footers, sources"], ["Line", "#2E2A5E", "Card borders, dividers"],
];

const guide = `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body>
<div class="page dark" style="display:flex;flex-direction:column;justify-content:center;background:radial-gradient(ellipse at 75% 35%,#2a1a6b 0%,#07071A 60%)">
 <img src="${logo}" style="width:22mm;margin-bottom:10mm">
 <div class="k">ClipForge client pitch deck</div>
 <h1 style="font-size:44pt">Presentation<br>design guide</h1>
 <p class="muted" style="font-size:12pt;margin-top:6mm;max-width:120mm">The colour, type, layout, motion and branding rules behind the deck, so it can be extended and reused for new prospects without losing its look.</p>
 <p class="muted" style="margin-top:20mm;font-size:9pt">Version 1.0 · November 2026</p>
</div>

<div class="page"><div class="k">01 · Design direction</div><h2>Cinematic, dark, product-first</h2>
<p>ClipForge's own interface is a dark app with a pink-to-violet brand mark. The deck extends that world instead of inventing a new one: deep midnight backgrounds, electric violet light, a cool cyan counter-accent, and the real product floating in perspective. The result should feel like a premium product launch, while every slide still answers one business question.</p>
<h3>Principles</h3><ul>
<li><b>Show the product.</b> Real screenshots and real rendered clips carry the story; no fake interfaces.</li>
<li><b>One message per slide.</b> The headline states the insight; the body proves it.</li>
<li><b>Honesty is a design element.</b> Estimates wear an amber ILLUSTRATIVE chip; sources sit in dim captions.</li>
<li><b>Depth with restraint.</b> 3D and glow are reserved for the hero visuals; data and text stay flat and crisp.</li>
<li><b>Motion explains.</b> Builds follow reading order; Morph carries objects between related slides.</li></ul>
<div class="thumbs" style="margin-top:6mm">${[1, 6, 24].map((n) => `<div><img src="${thumb(n)}">Slide ${n}</div>`).join("")}</div>
${foot(2)}</div>
<div class="page"><div class="k">01 · Design direction</div><h2>Colour system</h2>
<p>One dominant dark, one strong accent, two supporting tones and two semantic colours. Every colour is defined once in the PPTX theme (Design › Variants › Colours).</p>
<div class="sw">${palette.map(([n, h, u]) => `<div class="chip"><div class="c" style="background:${h}"></div><div class="t"><b>${n}</b>${h}<br><span class="muted">${u}</span></div></div>`).join("")}</div>
<h3>Weight and contrast rules</h3><ul>
<li>Midnight dominates; violet is the single strong accent on any slide; cyan and pink are supporting, amber and green are semantic only.</li>
<li>White (#F5F5FF) on Midnight is 18.4:1, Slate (#A3A8CC) 8.6:1, Cyan 11.0:1 and Soft violet 7.3:1: all above WCAG AA for body text. Dim slate (#6B7099, 4.2:1) is for captions and footers only.</li></ul>
${foot(3)}</div>

<div class="page"><div class="k">01 · Design direction</div><h2>Light and gradients</h2>
<div class="grad" style="background:radial-gradient(ellipse at 70% 40%,rgba(124,92,255,.55),transparent 60%),radial-gradient(ellipse at 95% 90%,rgba(34,211,238,.3),transparent 50%),#07071A"></div>
<p class="muted">Cover / centre glow: violet key light, cyan rim light, faint perspective grid floor.</p>
<div class="grad" style="background:radial-gradient(ellipse at 0% 0%,rgba(124,92,255,.35),transparent 50%),#07071A"></div>
<p class="muted">Content: a single violet glow top-left keeps text areas calm.</p>
<div class="grad" style="background:radial-gradient(ellipse at 10% 90%,rgba(244,114,182,.25),transparent 50%),radial-gradient(ellipse at 90% 0%,rgba(124,92,255,.3),transparent 50%),#07071A"></div>
<p class="muted">Problem: a pink undertone signals friction without shouting.</p>
<div class="grad" style="background:linear-gradient(135deg,#FF5CA8,#B44CFF 55%,#7C5CFF)"></div>
<p class="muted">Brand mark gradient (logo only). Never use it as a slide background.</p>
<ul><li>Backgrounds are pre-rendered gradient images on the slide layouts, because PowerPoint gradient fills don't survive all viewers.</li></ul>
${foot(4)}</div>

<div class="page"><div class="k">02 · Typography</div><h2>Manrope for voice, Inter for clarity</h2>
<p>Manrope's geometric, slightly rounded forms echo the product's friendly-technical tone and give headlines weight. Inter is built for screens and stays legible at small sizes on a projector. Numbers use Manrope Bold so big stats feel like part of the headline system.</p>
<table><tr><th>Role</th><th>Font</th><th>Size</th><th>Notes</th></tr>
<tr><td><span style="font-family:Manrope;font-weight:700;font-size:20pt">Cover headline</span></td><td>Manrope Bold</td><td>40pt (closing 46pt)</td><td>Two lines max</td></tr>
<tr><td><span style="font-family:Manrope;font-weight:700;font-size:15pt">Slide title (insight)</span></td><td>Manrope Bold</td><td>28pt</td><td>Left-aligned, max two lines, sentence case, states the insight</td></tr>
<tr><td><span style="font-size:8.5pt;font-weight:700;letter-spacing:2.5px;color:#0E9FB8">KICKER</span></td><td>Inter Bold, tracked +3</td><td>11pt, cyan</td><td>Names the topic above every title</td></tr>
<tr><td><span style="font-family:Manrope;font-weight:700;font-size:12pt">Card heading</span></td><td>Manrope Bold</td><td>15–18pt</td><td></td></tr>
<tr><td>Body</td><td>Inter Regular</td><td>12–16pt</td><td>Slate on dark; 1.1 line spacing</td></tr>
<tr><td><span style="font-family:Manrope;font-weight:800;font-size:22pt">≈72 min</span></td><td>Manrope Bold</td><td>40–48pt</td><td>Hero numbers; always with a unit and a label</td></tr>
<tr><td class="muted">Caption / source</td><td>Inter</td><td>9.5–10.5pt, dim</td><td>Sources, assumptions, footers</td></tr></table>
<h3>Fonts in the file</h3><p>The PPTX names <b>Manrope</b> (headings) and <b>Inter</b> (body) through the theme, so restyling is one change in Design › Variants › Fonts. Install both from the <code>fonts/</code> folder (or Google Fonts) on the presenting computer. Without them PowerPoint substitutes a default sans, and some titles may wrap differently.</p>
${foot(5)}</div>

<div class="page"><div class="k">03 · Layout</div><h2>A 16:9 grid with a fixed title line</h2>
<ul><li>Canvas 13.33 × 7.5 in (16:9 widescreen). Side margins 0.6 in; content from 2.0 in to 6.9 in; footer at 7.0 in.</li>
<li>Kicker at 0.55 in and title at 0.95 in on every content slide, so titles never jump between slides.</li>
<li>Gaps between cards are 0.2–0.3 in. Cards are rounded rectangles (0.14 in radius) with a 1pt line and a soft shadow; no edge stripes or underlines.</li>
<li>Five layouts: <b>COVER</b>, <b>CONTENT</b>, <b>CONTENT_RIGHT</b> (glow on the right, for product visuals), <b>PROBLEM</b> (pink undertone), <b>CENTER</b> (reveal, CTA, closing).</li>
<li>Layout families used across the deck: hero split, 2×2 and 3×2 grids, step cards, pipeline diagram, before/after columns, chart + commentary, architecture diagram, timeline, centred reveal.</li></ul>
<div class="thumbs">${[1, 4, 7, 8, 13, 16].map((n) => `<div><img src="${thumb(n)}">Slide ${n}</div>`).join("")}</div>
${foot(6)}</div>

<div class="page"><div class="k">04 · 3D visual direction</div><h2>Real screens, floating in light</h2>
<p>All 3D visuals are built from the product's real screenshots and real rendered clip frames, composed in Chromium with CSS 3D (perspective, rotateX/Y, translateZ), glass rim highlights and radial light, then exported as transparent 2× PNGs.</p>
<ul><li><b>Hero:</b> the Clips page tilted −20° with three finished clips floating at different depths.</li>
<li><b>Showcase:</b> Create, Clips and Live progress stacked in depth.</li>
<li><b>Device frames:</b> phone and glass screen frames with a violet rim; never cover product UI with decoration.</li>
<li><b>Lighting:</b> violet key light behind the subject, cyan rim at lower right; no hard drop shadows on 3D layers.</li>
<li><b>Motion asset:</b> <code>hero_loop.mp4</code>, a 5-second seamless loop of the hero gently rotating, for intros, websites and social posts.</li></ul>
<div class="thumbs">${[1, 6, 7, 11, 12, 22].map((n) => `<div><img src="${thumb(n)}">Slide ${n}</div>`).join("")}</div>
${foot(7)}</div>

<div class="page"><div class="k">05 · Motion system</div><h2>Slow reveals, one direction, purposeful</h2>
<table><tr><th>Element</th><th>Effect</th><th>Duration</th><th>Delay / order</th></tr>
<tr><td>Kicker</td><td>Fade</td><td>0.5 s</td><td>0 s</td></tr>
<tr><td>Title</td><td>Float in (fade + small rise)</td><td>0.7 s</td><td>0.15 s</td></tr>
<tr><td>Cards, rows, steps</td><td>Float in</td><td>0.6–0.7 s</td><td>from 0.4 s, staggered 0.15–0.3 s in reading order</td></tr>
<tr><td>Hero visuals, big numbers, statements</td><td>Zoom (fade + scale 85% → 100%)</td><td>0.8–1.4 s</td><td>after the content they support</td></tr>
<tr><td>Timelines, pipelines, charts, arrows</td><td>Wipe from left</td><td>0.4–1.6 s</td><td>before the nodes they connect</td></tr>
<tr><td>Slide transition (default)</td><td>Fade</td><td>0.8 s</td><td></td></tr>
<tr><td>Slide transition (linked slides)</td><td>Morph by object</td><td>1.6 s</td><td>Slides 2, 7, 24</td></tr></table>
<h3>Rules</h3><ul>
<li>Every build starts by itself when the slide appears; the presenter never clicks through bullets.</li>
<li>Easing is ease-out (decelerate) for movement; movement is always upward or left-to-right, matching reading order.</li>
<li>No spins, bounces or exits. A slide's build should finish within about 2.5 seconds.</li>
<li>Morph pairs share an object name starting with <code>!!</code> (<code>!!hero</code>, <code>!!stage</code>, <code>!!logo</code>). Keep the name when replacing the image, or the Morph falls back to a fade.</li></ul>
${foot(8)}</div>

<div class="page"><div class="k">06 · Branding and content rules</div><h2>What keeps the deck credible</h2>
<div class="do"><div class="box"><h3 style="margin-top:0;color:#0E8F5B">Do</h3><ul>
<li>Use only real ClipForge screenshots and renders; caption demo footage as such.</li>
<li>Put an amber ILLUSTRATIVE chip on every estimate and state the assumption nearby.</li>
<li>Write titles as insights in sentence case, with no final full stop (the brand tagline is the one exception).</li>
<li>Keep the logo on the cover, CTA and closing slides only.</li>
<li>Separate "available now", "planned" and "ideas" on any roadmap.</li>
<li>Keep the contact placeholders on the CTA slide filled in per prospect.</li></ul></div>
<div class="box"><h3 style="margin-top:0;color:#C2185B">Don't</h3><ul>
<li>Invent customers, testimonials, view counts, revenue or ROI.</li>
<li>Show third-party creators' footage on slides sent outside the meeting.</li>
<li>Use the brand gradient as a background, or add accent bars and underlines.</li>
<li>Use more than one strong accent colour on a slide.</li>
<li>Shrink body text below 11pt to fit more words; cut words instead.</li>
<li>Claim the software guarantees views.</li></ul></div></div>
<h3>Presenting</h3><ul>
<li>Best in PowerPoint for Microsoft 365 or 2019+ (Windows or Mac): full Morph, auto-builds and fonts.</li>
<li>PowerPoint for the web and Keynote play the builds; Morph may fall back to a fade.</li>
<li>Google Slides and LibreOffice show the slides and fade transitions but drop most animations; present the PDF instead if in doubt.</li>
<li>For email, send the PDF. It shows every slide in its final, fully-built state.</li></ul>
${foot(9)}</div>
</body></html>`;

const about = `<!doctype html><html><head><meta charset="utf-8"><style>${css}
.page{width:13.333in;height:7.5in;min-height:0;padding:.6in;background:#07071A;color:#F5F5FF}
</style></head><body><div class="page" style="page-break-after:auto">
<div class="k">About this PDF edition</div>
<h1 style="font-size:32pt;margin:4mm 0 10mm">Notes for readers</h1>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14mm;font-size:15pt;line-height:1.5;color:#A3A8CC">
<div><h3 style="color:#F5F5FF;font-size:17pt;margin-top:6mm">Motion is not shown here</h3><p>The PowerPoint version builds each slide with native animations and uses Morph transitions between linked slides. This PDF shows every slide in its final, fully-built state.</p>
<h3 style="color:#F5F5FF;font-size:17pt;margin-top:6mm">Numbers are illustrative</h3><p>Minutes per clip, weekly hours, capacity and value figures are estimates based on stated assumptions, not measured results. They are marked ILLUSTRATIVE and will be measured in a pilot.</p></div>
<div><h3 style="color:#F5F5FF;font-size:17pt;margin-top:6mm">Product visuals are real</h3><p>Screens are captured from the ClipForge app. Clip frames are ClipForge renders of generated demo footage; no third-party video is shown in the product screens.</p>
<h3 style="color:#F5F5FF;font-size:17pt;margin-top:6mm">Sources</h3><p>Product capabilities and platform rules come from the ClipForge source code and documentation (November 2026). Platform rules change; check them before relying on them.</p></div></div>
</div></body></html>`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setContent(guide, { waitUntil: "load" });
  await page.pdf({ path: path.join(OUT, "ClipForge_Presentation_Design_Guide.pdf"), format: "A4", printBackground: true, margin: { top: 0, bottom: 0, left: 0, right: 0 } });
  await page.setContent(about, { waitUntil: "load" });
  await page.pdf({ path: path.join(OUT, "about_page.pdf"), width: "13.333in", height: "7.5in", printBackground: true, margin: { top: 0, bottom: 0, left: 0, right: 0 } });
  await browser.close();
  console.log("pdfs written");
})();
