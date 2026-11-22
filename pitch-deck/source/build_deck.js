// ClipForge client pitch deck generator (pptxgenjs).
// node build_deck.js  -> out/ClipForge_Client_Pitch_Deck.raw.pptx + out/motion.json
// then: python3 motion.py  -> injects Morph/Fade transitions and entrance animations.
const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const Fi = require("react-icons/fi");
const { applyTheme } = require(process.env.PPTX_SKILL + "/scripts/apply_theme.js");

const A = (f) => path.join(__dirname, "assets", f);
const OUT = path.join(__dirname, "out");
fs.mkdirSync(OUT, { recursive: true });
const SCRIPT = JSON.parse(fs.readFileSync(path.join(__dirname, "script.json"), "utf8"));

// ---------------- Design tokens ----------------
const K = {
  bg0: "07071A", bg1: "0D0B24", surface: "15132F", surface2: "1E1B40", line: "2E2A5E",
  violet: "7C5CFF", violetL: "A78BFA", cyan: "22D3EE", pink: "F472B6", amber: "F5B544", green: "34D399",
  text: "F5F5FF", muted: "A3A8CC", dim: "6B7099",
};
const THEME = {
  name: "ClipForge Midnight", headFontFace: "Manrope", bodyFontFace: "Inter",
  colors: { dk1: K.bg0, lt1: K.text, dk2: K.surface, lt2: K.muted, accent1: K.violet, accent2: K.cyan,
    accent3: K.pink, accent4: K.amber, accent5: K.green, accent6: K.violetL, hlink: K.cyan, folHlink: K.violetL },
};
const HF = "Manrope", BF = "Inter";
const W = 13.333, H = 7.5, M = 0.6, CW = W - 2 * M;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.theme = { headFontFace: HF, bodyFontFace: BF };
pres.title = "ClipForge Client Pitch Deck";
pres.author = "ClipForge";
pres.company = "ClipForge";
const C = pres.SchemeColor;

// ---------------- Layouts ----------------
const footer = (dark = true) => [
  { text: { text: "ClipForge  ·  Client presentation", options: { x: M, y: 7.0, w: 5, h: 0.3, fontFace: BF, fontSize: 9, color: K.dim, margin: 0 } } },
];
const titlePh = { placeholder: { options: { name: "title", type: "title", x: M, y: 0.95, w: CW, h: 1.05, fontFace: HF, fontSize: 28, bold: true, color: K.text, align: "left", valign: "top", margin: 0, lineSpacingMultiple: 0.95 }, text: "" } };
const kickerPh = { placeholder: { options: { name: "kicker", type: "body", x: M, y: 0.55, w: 8, h: 0.32, fontFace: BF, fontSize: 11, bold: true, color: K.cyan, charSpacing: 3, margin: 0, valign: "middle" }, text: "" } };
const slideNum = { x: W - M - 0.6, y: 7.0, w: 0.6, h: 0.3, fontFace: BF, fontSize: 9, color: K.dim, align: "right" };
const layout = (title, bgImg, withTitle = true) => pres.defineSlideMaster({
  title, background: { path: A("generated/" + bgImg + ".jpg") },
  objects: withTitle ? [...footer(), kickerPh, titlePh] : footer(),
  slideNumber: slideNum,
});
layout("CONTENT", "bg_content");
layout("CONTENT_RIGHT", "bg_right");
layout("PROBLEM", "bg_problem");
layout("COVER", "bg_cover", false);
layout("CENTER", "bg_center", false);

// ---------------- Icons ----------------
async function icon(name, color, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Fi[name], { color: "#" + color, size: String(size), strokeWidth: 2 }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

// ---------------- Motion registry (consumed by motion.py) ----------------
// effect: fade | rise | zoom | wipe ; delay/dur in ms. transition: fade | morph
const MOTION = [];
let cur = null;
function newSlide(master, sectionTitle, transition = "fade") {
  const s = pres.addSlide({ masterName: master, sectionTitle });
  cur = { transition, anims: [] };
  MOTION.push(cur);
  const n = MOTION.length;
  if (SCRIPT[n - 1]) {
    const sc = SCRIPT[n - 1];
    s.addNotes(`${sc.key}\n\n${sc.notes}\n\nTalking points:\n- ${sc.points.join("\n- ")}\n\nTransition: ${sc.transition}\nTime: ${sc.duration}`);
  }
  return s;
}
let nameCounter = 0;
function anim(name, effect, delay, dur = 700) { cur.anims.push({ name, effect, delay, dur }); return name; }
const nm = (p) => `${p}_${++nameCounter}`;

// ---------------- Components ----------------
function kicker(s, t) { s.addText(t, { placeholder: "kicker" }); anim("kicker", "fade", 0, 500); }
function title(s, t) { s.addText(t, { placeholder: "title" }); anim("title", "rise", 150, 700); }
function txt(s, text, o, a) {
  const name = o.objectName || nm("t");
  s.addText(text, { isTextBox: true, fontFace: BF, fontSize: 14, color: K.text, margin: 0, valign: "top", ...o, objectName: name });
  if (a) anim(name, a[0], a[1], a[2]);
  return name;
}
function card(s, x, y, w, h, o = {}, a) {
  const name = o.objectName || nm("card");
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: o.r ?? 0.14, objectName: name,
    fill: { color: o.fill || K.surface, transparency: o.transparency ?? 18 },
    line: { color: o.line || K.line, width: o.lineW ?? 1 },
    shadow: o.noShadow ? undefined : { type: "outer", color: "000000", opacity: 0.35, blur: 18, offset: 6, angle: 90 },
  });
  if (a) anim(name, a[0], a[1], a[2]);
  return name;
}
async function iconDot(s, ic, x, y, d, color, a) {
  const name = nm("ic");
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color, transparency: 78 }, line: { color, width: 1, transparency: 40 }, objectName: name + "_bg" });
  s.addImage({ data: await icon(ic, color), x: x + d * 0.25, y: y + d * 0.25, w: d * 0.5, h: d * 0.5, objectName: name });
  if (a) { anim(name + "_bg", a[0], a[1], a[2]); anim(name, a[0], a[1], a[2]); }
}
function chip(s, t, x, y, color = K.amber, w, a) {
  const name = nm("chip");
  const ww = w || 0.25 + t.length * 0.085;
  s.addText(t, { isTextBox: true, x, y, w: ww, h: 0.3, shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.15,
    fill: { color, transparency: 82 }, line: { color, width: 0.75, transparency: 30 },
    fontFace: BF, fontSize: 9, bold: true, color, align: "center", valign: "middle", margin: 0, charSpacing: 1.5, objectName: name });
  if (a) anim(name, a[0], a[1], a[2]);
  return name;
}
function img(s, file, o, a) {
  const name = o.objectName || nm("img");
  s.addImage({ path: A(file), ...o, objectName: name });
  if (a) anim(name, a[0], a[1], a[2]);
  return name;
}
const fit = (pxW, pxH, maxW, maxH) => { const r = pxW / pxH; let w = maxW, h = w / r; if (h > maxH) { h = maxH; w = h * r; } return { w, h }; };

// =====================================================================
(async () => {
  const ILL = "ILLUSTRATIVE";

  // ---------- 1. COVER ----------
  pres.addSection({ title: "Act 1 · The opportunity" });
  let s = newSlide("COVER", "Act 1 · The opportunity");
  img(s, "generated/hero.png", { x: 6.3, y: 0.95, w: 7.3, h: 7.3 * 800 / 1140, objectName: "!!hero" }, ["zoom", 0, 1400]);
  img(s, "generated/logo.png", { x: M, y: 0.6, w: 0.5, h: 0.5, objectName: "!!logo" }, ["fade", 0, 600]);
  txt(s, "ClipForge", { x: M + 0.62, y: 0.6, w: 3, h: 0.5, fontFace: HF, fontSize: 20, bold: true, valign: "middle", objectName: "brand" }, ["fade", 100, 600]);
  txt(s, "CLIENT PRESENTATION", { x: M, y: 2.35, w: 5, h: 0.3, fontSize: 11, bold: true, color: K.cyan, charSpacing: 3 }, ["fade", 400, 600]);
  txt(s, "Your best moments,\neverywhere.", { x: M, y: 2.75, w: 6.0, h: 1.5, fontFace: HF, fontSize: 40, bold: true, lineSpacingMultiple: 0.92 }, ["rise", 600, 900]);
  txt(s, "ClipForge turns long streams, podcasts and videos into a daily flow of ready-to-post Shorts, Reels and TikToks, and you approve every one.",
    { x: M, y: 4.45, w: 5.2, h: 1.1, fontSize: 16, color: K.muted, lineSpacingMultiple: 1.15 }, ["rise", 1000, 800]);

  // ---------- 2. EXECUTIVE SUMMARY ----------
  s = newSlide("CONTENT", "Act 1 · The opportunity", "morph");
  kicker(s, "EXECUTIVE SUMMARY");
  title(s, "One link in. A week of on-brand short clips out");
  img(s, "generated/hero.png", { x: 7.35, y: 1.95, w: 5.5, h: 5.5 * 800 / 1140, objectName: "!!hero" });
  const rows = [
    ["WHAT IT IS", "A clipping engine that finds the most exciting moments in any video or live stream and turns them into finished, captioned, platform-ready short clips."],
    ["THE PROBLEM", "Finding, cutting, captioning, packaging and posting one short by hand takes about an hour (illustrative), so most long-form video is never clipped."],
    ["WHO IT SERVES", "Streamers, podcasters, creators and brands with long-form video, and the agencies and clippers who post for them."],
    ["WHY IT MATTERS", "Short-form feeds are where new viewers find creators. Consistent, well-packaged clips give every long video a second life."],
  ];
  rows.forEach(([k, v], i) => {
    const y = 2.2 + i * 1.12;
    txt(s, k, { x: M, y, w: 1.9, h: 0.3, fontSize: 10.5, bold: true, color: K.violetL, charSpacing: 2 }, ["rise", 500 + i * 180, 600]);
    txt(s, v, { x: M + 1.95, y: y - 0.04, w: 4.55, h: 1.0, fontSize: 13.5, color: K.text, lineSpacingMultiple: 1.1 }, ["rise", 500 + i * 180, 600]);
  });
  card(s, 7.55, 5.95, 5.15, 0.85, { fill: K.violet, transparency: 80, line: K.violet }, ["rise", 1400, 700]);
  txt(s, [{ text: "Value:  ", options: { bold: true, color: K.cyan } }, { text: "an hour of manual work per clip becomes minutes of review, and you keep control of every post." }],
    { x: 7.75, y: 6.0, w: 4.8, h: 0.75, fontSize: 13, valign: "middle" }, ["rise", 1400, 700]);

  // ---------- 3. WORLD TODAY ----------
  s = newSlide("CONTENT", "Act 1 · The opportunity");
  kicker(s, "THE WORLD TODAY");
  title(s, "Audiences discover creators in 30 seconds. Creators publish in hours");
  card(s, M, 2.2, 3.85, 3.85, {}, ["rise", 400, 700]);
  txt(s, "WHERE CONTENT IS MADE", { x: M + 0.3, y: 2.45, w: 3.3, h: 0.3, fontSize: 10.5, bold: true, color: K.violetL, charSpacing: 2 }, ["rise", 400, 700]);
  const made = [["FiRadio", "Live streams", "Hours long, unscripted"], ["FiMic", "Podcasts", "Long conversations, a few gems"], ["FiFilm", "Gameplay & long videos", "Highlights scattered throughout"]];
  for (let i = 0; i < made.length; i++) {
    const y = 2.95 + i * 0.98;
    await iconDot(s, made[i][0], M + 0.3, y, 0.62, K.violetL, ["rise", 550 + i * 150, 700]);
    txt(s, made[i][1], { x: M + 1.1, y: y + 0.02, w: 2.6, h: 0.32, fontFace: HF, fontSize: 15, bold: true }, ["rise", 550 + i * 150, 700]);
    txt(s, made[i][2], { x: M + 1.1, y: y + 0.34, w: 2.6, h: 0.3, fontSize: 12, color: K.muted }, ["rise", 550 + i * 150, 700]);
  }
  const ar = nm("arrow");
  s.addShape(pres.shapes.RIGHT_ARROW, { x: 4.62, y: 3.85, w: 0.55, h: 0.55, fill: { color: K.cyan }, line: { color: K.cyan, width: 0 }, objectName: ar });
  anim(ar, "wipe", 1000, 500);
  txt(s, "WHERE AUDIENCES FIND IT", { x: 5.35, y: 1.88, w: 5, h: 0.3, fontSize: 10.5, bold: true, color: K.cyan, charSpacing: 2 }, ["fade", 1100, 500]);
  const plats = [
    ["YouTube Shorts", "Vertical, up to 3 minutes. Ad revenue share once a channel qualifies."],
    ["Instagram Reels", "Max 5 hashtags per post. Strong for growth and brand deals."],
    ["Facebook Reels", "3 to 90 seconds. Eligible Pages are paid per view."],
    ["TikTok", "Creator Rewards pays for videos over 1 minute, in supported countries."],
  ];
  plats.forEach(([n, d], i) => {
    const x = 5.35 + (i % 2) * 3.74, y = 2.2 + Math.floor(i / 2) * 1.98;
    card(s, x, y, 3.6, 1.82, { fill: K.surface2, transparency: 25 }, ["rise", 1200 + i * 150, 700]);
    txt(s, n, { x: x + 0.28, y: y + 0.25, w: 3.1, h: 0.4, fontFace: HF, fontSize: 17, bold: true }, ["rise", 1200 + i * 150, 700]);
    txt(s, d, { x: x + 0.28, y: y + 0.72, w: 3.05, h: 0.95, fontSize: 12.5, color: K.muted, lineSpacingMultiple: 1.1 }, ["rise", 1200 + i * 150, 700]);
  });
  txt(s, [{ text: "And a new market for clips: ", options: { bold: true, color: K.text } },
    { text: "creators and brands now pay clippers per 1,000 views through marketplaces such as Whop Content Rewards, Vyro and Clipster." }],
    { x: M, y: 6.3, w: CW, h: 0.45, fontSize: 12.5, color: K.muted, valign: "middle" }, ["fade", 2000, 700]);

  // ---------- 4. PROBLEM ----------
  s = newSlide("PROBLEM", "Act 1 · The opportunity");
  kicker(s, "THE PROBLEM");
  title(s, "The best 30 seconds are buried somewhere in six hours");
  txt(s, "A six-hour stream, drawn to scale. Each pink sliver is a moment worth clipping.", { x: M, y: 2.0, w: 8.5, h: 0.3, fontSize: 12, color: K.muted }, ["fade", 400, 500]);
  chip(s, ILL, W - M - 1.25, 2.0, K.amber, 1.25, ["fade", 400, 500]);
  const tl = nm("timeline");
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: M, y: 2.45, w: CW, h: 0.55, rectRadius: 0.1, fill: { color: K.surface2 }, line: { color: K.line, width: 1 }, objectName: tl });
  anim(tl, "wipe", 600, 1400);
  [0.07, 0.21, 0.38, 0.55, 0.71, 0.9].forEach((p, i) => {
    const n = nm("moment");
    s.addShape(pres.shapes.RECTANGLE, { x: M + p * CW, y: 2.5, w: 0.07, h: 0.45, fill: { color: K.pink }, line: { color: K.pink, width: 0 },
      shadow: { type: "outer", color: K.pink, opacity: 0.8, blur: 10, offset: 0, angle: 90 }, objectName: n });
    anim(n, "zoom", 2000 + i * 120, 400);
  });
  txt(s, "0:00", { x: M, y: 3.05, w: 1, h: 0.25, fontSize: 10, color: K.dim }, ["fade", 600, 400]);
  txt(s, "6:00:00", { x: W - M - 1, y: 3.05, w: 1, h: 0.25, fontSize: 10, color: K.dim, align: "right" }, ["fade", 600, 400]);
  const pains = [
    ["FiSearch", "Finding", "Someone has to watch or scrub hours of footage to spot each moment."],
    ["FiCrop", "Reframing", "Wide footage must be cropped to vertical without losing the face or the action."],
    ["FiType", "Captioning", "Every clip needs captions transcribed, timed and styled by hand."],
    ["FiTag", "Packaging", "Hook, title, cover, description and hashtags decide whether anyone clicks."],
    ["FiSend", "Posting", "Four platforms, four sets of rules, four uploads, every single day."],
  ];
  const pw = (CW - 4 * 0.2) / 5;
  for (let i = 0; i < pains.length; i++) {
    const x = M + i * (pw + 0.2), y = 3.75, d = 2900 + i * 150;
    card(s, x, y, pw, 2.95, {}, ["rise", d, 700]);
    await iconDot(s, pains[i][0], x + 0.25, y + 0.28, 0.6, K.pink, ["rise", d, 700]);
    txt(s, pains[i][1], { x: x + 0.25, y: y + 1.05, w: pw - 0.45, h: 0.4, fontFace: HF, fontSize: 17, bold: true }, ["rise", d, 700]);
    txt(s, pains[i][2], { x: x + 0.25, y: y + 1.5, w: pw - 0.45, h: 1.3, fontSize: 12.5, color: K.muted, lineSpacingMultiple: 1.12 }, ["rise", d, 700]);
  }

  // ---------- 5. COST ----------
  s = newSlide("PROBLEM", "Act 1 · The opportunity");
  kicker(s, "THE COST OF THE PROBLEM");
  title(s, "By hand, one clip costs about an hour, and the clock resets daily");
  chip(s, ILL + " ESTIMATE", M, 2.0, K.amber, 2.3, ["fade", 300, 500]);
  const steps = ["Find the moment", "Cut & reframe", "Captions", "Hook, title & cover", "Description & hashtags", "Upload to 4 platforms"];
  const mins = [20, 10, 15, 10, 5, 12];
  const chartName = nm("chart");
  s.addChart(pres.charts.BAR, [{ name: "Minutes per clip", labels: steps, values: mins }], {
    x: M - 0.1, y: 2.4, w: 7.4, h: 4.0, barDir: "bar", objectName: chartName,
    chartColors: [K.pink], barGapWidthPct: 55, catAxisOrientation: "maxMin",
    catAxisLabelColor: K.text, catAxisLabelFontSize: 12, catAxisLabelFontFace: "+mn-lt", catAxisLineShow: false,
    valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: K.text, dataLabelFontSize: 12, dataLabelFontFace: "+mn-lt", dataLabelFormatCode: '0" min"',
    showLegend: false, showTitle: false, plotArea: { fill: { color: K.bg0, transparency: 100 } },
  });
  anim(chartName, "wipe", 500, 1200);
  card(s, 8.3, 2.0, 4.43, 2.15, { fill: K.pink, transparency: 85, line: K.pink }, ["zoom", 1400, 800]);
  txt(s, "≈72 min", { x: 8.55, y: 2.15, w: 4, h: 0.95, fontFace: HF, fontSize: 48, bold: true, color: K.text }, ["zoom", 1400, 800]);
  txt(s, "hands-on time per finished clip. At 3 clips a day, that is about 25 hours a week.", { x: 8.55, y: 3.1, w: 3.95, h: 0.9, fontSize: 13, color: K.muted, lineSpacingMultiple: 1.1 }, ["zoom", 1400, 800]);
  txt(s, "HIDDEN COSTS", { x: 8.3, y: 4.4, w: 4, h: 0.3, fontSize: 10.5, bold: true, color: K.violetL, charSpacing: 2 }, ["fade", 1900, 500]);
  const hidden = [["Missed timing", "by the time a clip is ready, the moment has passed."], ["Uneven quality", "captions, hooks and covers vary clip to clip."], ["Copyright surprises", "re-uploads that get claimed, muted or blocked."]];
  hidden.forEach(([a, b], i) => txt(s, [{ text: a + ": ", options: { bold: true } }, { text: b, options: { color: K.muted } }],
    { x: 8.3, y: 4.78 + i * 0.6, w: 4.43, h: 0.55, fontSize: 12.5, lineSpacingMultiple: 1.05 }, ["rise", 2000 + i * 150, 600]));
  txt(s, "Assumptions: one experienced editor, 3 clips a day, 4 platforms. Replace with your own numbers; we measure them in the pilot.",
    { x: M, y: 6.5, w: 7.5, h: 0.4, fontSize: 10, color: K.dim }, ["fade", 2400, 500]);

  // ---------- 6. INTRODUCING ----------
  pres.addSection({ title: "Act 2 · Our solution" });
  s = newSlide("CENTER", "Act 2 · Our solution");
  txt(s, "INTRODUCING", { x: 0, y: 0.75, w: W, h: 0.3, fontSize: 11, bold: true, color: K.cyan, charSpacing: 4, align: "center" }, ["fade", 0, 600]);
  txt(s, "ClipForge", { x: 0, y: 1.05, w: W, h: 1.05, fontFace: HF, fontSize: 60, bold: true, align: "center" }, ["zoom", 300, 1000]);
  txt(s, "Paste a link. Approve the best. ClipForge does the rest.", { x: 0, y: 2.1, w: W, h: 0.5, fontSize: 21, color: K.muted, align: "center" }, ["rise", 900, 800]);
  const st = fit(1800, 1280, 6.6, 4.05);
  img(s, "generated/tilt_clips.png", { x: (W - st.w) / 2, y: 2.62, w: st.w, h: st.h, objectName: "!!stage" }, ["zoom", 1300, 1200]);
  const promises = ["Runs on your computer", "Any video or live stream", "You approve every post"];
  promises.forEach((p, i) => {
    const w = 2.75, x = W / 2 - (3 * w + 2 * 0.25) / 2 + i * (w + 0.25);
    const n = nm("promise");
    s.addText(p, { isTextBox: true, x, y: 6.55, w, h: 0.4, shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.2,
      fill: { color: K.violet, transparency: 78 }, line: { color: K.violetL, width: 0.75, transparency: 30 },
      fontFace: BF, fontSize: 12, bold: true, color: K.text, align: "center", valign: "middle", margin: 0, objectName: n });
    anim(n, "rise", 2000 + i * 150, 600);
  });

  // ---------- 7. PRODUCT SHOWCASE ----------
  s = newSlide("CONTENT_RIGHT", "Act 2 · Our solution", "morph");
  kicker(s, "PRODUCT SHOWCASE");
  title(s, "A real product, not a concept: every screen is ClipForge today");
  img(s, "generated/showcase.png", { x: 0.15, y: 1.95, w: 8.45, h: 8.45 * 700 / 1100, objectName: "!!stage" });
  const calls = [
    ["1", "Create", "Paste a link, upload a file or follow a live stream. Choose shape, length and style."],
    ["2", "Clips", "Every clip arrives scored, captioned and packaged, ready to approve, edit or remake."],
    ["3", "Live progress", "Each step shows as it happens, from download to the live link."],
  ];
  calls.forEach(([n, h, d], i) => {
    const y = 2.25 + i * 1.42, dl = 700 + i * 250;
    const c = nm("num");
    s.addText(n, { isTextBox: true, x: 8.9, y, w: 0.5, h: 0.5, shape: pres.shapes.OVAL, fill: { color: K.violet }, line: { color: K.violetL, width: 1 },
      fontFace: HF, fontSize: 15, bold: true, color: K.text, align: "center", valign: "middle", margin: 0, objectName: c });
    anim(c, "zoom", dl, 500);
    txt(s, h, { x: 9.6, y: y + 0.03, w: 3.1, h: 0.4, fontFace: HF, fontSize: 18, bold: true }, ["rise", dl, 600]);
    txt(s, d, { x: 9.6, y: y + 0.45, w: 3.15, h: 0.85, fontSize: 12.5, color: K.muted, lineSpacingMultiple: 1.1 }, ["rise", dl, 600]);
  });
  txt(s, "Screens captured from the ClipForge app. Footage is generated demo content.", { x: 8.9, y: 6.55, w: 3.85, h: 0.4, fontSize: 10, color: K.dim }, ["fade", 1500, 500]);

  // ---------- 8. EXPERIENCE ARCHITECTURE ----------
  s = newSlide("CONTENT", "Act 2 · Our solution");
  kicker(s, "HOW THE SOLUTION FITS THE PROBLEM");
  title(s, "Six manual jobs become one supervised pipeline");
  const stages = [
    ["FiSearch", "Find", "Scrub hours of footage", "Audio excitement plus on-screen motion, re-ranked by AI from what is said"],
    ["FiCrop", "Reframe", "Crop each clip by hand", "Smart crop follows faces and action. Vertical, portrait, square or landscape"],
    ["FiScissors", "Edit", "Caption, cut and zoom manually", "Word-by-word captions, hook title, jump cuts, zooms, payoff tease, slow-mo replay"],
    ["FiTag", "Package", "Write every title and tag", "Titles, cover, SEO description, tags and hashtags per platform, plus a virality score"],
    ["FiCheckCircle", "Review", "Download, check, re-export", "Approve, edit or reject in the web app or in Telegram"],
    ["FiSend", "Publish", "Upload four times", "YouTube, Instagram, Facebook and TikTok, now or on a schedule"],
  ];
  const sw = CW / 6;
  txt(s, "BEFORE", { x: M, y: 2.05, w: 2, h: 0.3, fontSize: 10, bold: true, color: K.dim, charSpacing: 2 }, ["fade", 300, 400]);
  txt(s, "WITH CLIPFORGE", { x: M, y: 4.4, w: 3, h: 0.3, fontSize: 10, bold: true, color: K.cyan, charSpacing: 2 }, ["fade", 300, 400]);
  const pl = nm("pipe");
  s.addShape(pres.shapes.LINE, { x: M + sw / 2, y: 3.55, w: CW - sw, h: 0, line: { color: K.violet, width: 2.5, dashType: "solid" }, objectName: pl });
  anim(pl, "wipe", 400, 1600);
  for (let i = 0; i < stages.length; i++) {
    const [ic, n, before, after] = stages[i];
    const cx = M + i * sw + sw / 2, d = 600 + i * 250;
    txt(s, before, { x: cx - sw / 2 + 0.08, y: 2.4, w: sw - 0.16, h: 0.55, fontSize: 11.5, color: K.dim, align: "center", italic: true }, ["fade", d, 500]);
    const isReview = n === "Review";
    await iconDot(s, ic, cx - 0.36, 3.19, 0.72, isReview ? K.cyan : K.violetL, ["zoom", d, 500]);
    txt(s, n, { x: cx - sw / 2, y: 3.98, w: sw, h: 0.35, fontFace: HF, fontSize: 16, bold: true, align: "center" }, ["rise", d, 500]);
    card(s, cx - sw / 2 + 0.06, 4.75, sw - 0.12, 1.68, { fill: isReview ? K.cyan : K.surface, transparency: isReview ? 86 : 20, line: isReview ? K.cyan : K.line, noShadow: true }, ["rise", d + 100, 600]);
    txt(s, after, { x: cx - sw / 2 + 0.18, y: 4.88, w: sw - 0.36, h: 1.5, fontSize: 11.5, color: K.text, lineSpacingMultiple: 1.08 }, ["rise", d + 100, 600]);
  }
  txt(s, [{ text: "You stay in control. ", options: { bold: true, color: K.cyan } }, { text: "Everything runs automatically except the decision to publish." }],
    { x: M, y: 6.55, w: CW, h: 0.35, fontSize: 13, color: K.muted }, ["fade", 2400, 600]);

  // ---------- 9. HOW IT WORKS ----------
  s = newSlide("CONTENT", "Act 2 · Our solution");
  kicker(s, "HOW IT WORKS");
  title(s, "From link to live post in four steps");
  const how = [
    ["generated/tilt_create.png", 1800, 1280, "Drop in a source", "Paste a link, upload a file or start a live recording. Run the copyright check first if you like."],
    ["generated/tilt_progress.png", 1240, 1720, "ClipForge edits", "Moments are found, cut, captioned and packaged while you do other work. Every step shows live."],
    ["generated/tilt_edit.png", 1800, 1280, "Review and refine", "Approve, reject, or swap the title, hook or cover in the app or in Telegram."],
    ["generated/tilt_schedule.png", 1800, 1280, "Publish or schedule", "Pick the platforms for each clip and post now or at a set time."],
  ];
  const hw = (CW - 3 * 0.25) / 4;
  how.forEach(([f, pw2, ph2, h, d], i) => {
    const x = M + i * (hw + 0.25), y = 2.1, dl = 400 + i * 300;
    card(s, x, y, hw, 4.75, {}, ["rise", dl, 700]);
    const g = fit(pw2, ph2, hw - 0.1, 2.25);
    img(s, f, { x: x + (hw - g.w) / 2, y: y + 0.08 + (2.25 - g.h) / 2, w: g.w, h: g.h }, ["zoom", dl + 150, 700]);
    txt(s, `0${i + 1}`, { x: x + 0.28, y: y + 2.45, w: 1, h: 0.45, fontFace: HF, fontSize: 24, bold: true, color: K.violetL }, ["rise", dl, 700]);
    txt(s, h, { x: x + 0.28, y: y + 2.95, w: hw - 0.5, h: 0.4, fontFace: HF, fontSize: 16, bold: true }, ["rise", dl, 700]);
    txt(s, d, { x: x + 0.28, y: y + 3.4, w: hw - 0.5, h: 1.25, fontSize: 12.5, color: K.muted, lineSpacingMultiple: 1.1 }, ["rise", dl, 700]);
  });

  // ---------- 10. FEATURES ----------
  s = newSlide("CONTENT", "Act 2 · Our solution");
  kicker(s, "KEY CAPABILITIES");
  title(s, "Every feature exists to remove one specific bottleneck");
  const feats = [
    ["FiZap", "Moment finder", "hours of scrubbing", "Scores every second by audio excitement and motion; AI can re-rank by what is said. Live streams are clipped while still running."],
    ["FiCrop", "Smart crop", "manual reframing", "Follows faces and action instead of the centre. Vertical, portrait, square or landscape output."],
    ["FiFilm", "Viral edit", "repetitive editing", "Pop captions, hook title, jump cuts on real dead air only, zoom punch-ins, payoff tease and a looping slow-mo replay."],
    ["FiTag", "SEO packaging", "blank-page copywriting", "Hook-first titles, long YouTube descriptions and tags, and captions that follow each platform's hashtag rules."],
    ["FiShield", "Copyright check", "blind reposting", "A risk level for every source, whether the original is likely monetised, and what to do about it."],
    ["FiSend", "Publish everywhere", "four separate uploads", "YouTube, Instagram, Facebook and TikTok, chosen per clip, scheduled, with a visibility check after upload."],
  ];
  const fw = (CW - 2 * 0.25) / 3, fh = 2.18;
  for (let i = 0; i < feats.length; i++) {
    const [ic, n, rem, d] = feats[i];
    const x = M + (i % 3) * (fw + 0.25), y = 2.15 + Math.floor(i / 3) * (fh + 0.22), dl = 400 + i * 150;
    card(s, x, y, fw, fh, {}, ["rise", dl, 700]);
    await iconDot(s, ic, x + 0.25, y + 0.25, 0.58, K.violetL, ["rise", dl, 700]);
    txt(s, n, { x: x + 1.0, y: y + 0.22, w: fw - 1.2, h: 0.35, fontFace: HF, fontSize: 16, bold: true }, ["rise", dl, 700]);
    txt(s, "Removes " + rem, { x: x + 1.0, y: y + 0.56, w: fw - 1.2, h: 0.3, fontSize: 11, bold: true, color: K.pink }, ["rise", dl, 700]);
    txt(s, d, { x: x + 0.25, y: y + 1.0, w: fw - 0.5, h: 1.1, fontSize: 12, color: K.muted, lineSpacingMultiple: 1.08 }, ["rise", dl, 700]);
  }

  // ---------- 11. ANATOMY ----------
  pres.addSection({ title: "Act 3 · User transformation" });
  s = newSlide("CONTENT_RIGHT", "Act 3 · User transformation");
  kicker(s, "THE OUTPUT");
  title(s, "Every clip ships with the packaging top clip channels rely on");
  const fr = [["generated/frame1_glass.png", "Opens on the cover and hook"], ["generated/frame2_glass.png", "Pop captions carry the story"], ["generated/frame3_glass.png", "Payoff, then a replay that loops"]];
  fr.forEach(([f, l], i) => {
    const w = 2.25, h = w * 1000 / 600, x = M + i * 2.4, dl = 400 + i * 300;
    img(s, f, { x, y: 1.95, w, h }, ["rise", dl, 800]);
    txt(s, l, { x: x + 0.1, y: 1.95 + h - 0.1, w: w - 0.2, h: 0.5, fontSize: 11.5, color: K.muted, align: "center" }, ["fade", dl + 300, 500]);
  });
  const anat = [
    ["FiType", "Hook title", "Big curiosity text for the first 3 seconds"],
    ["FiPlayCircle", "Payoff tease", "Opens with 2 seconds of the best moment"],
    ["FiMessageSquare", "Pop captions", "Word by word, key words highlighted"],
    ["FiZoomIn", "Zoom punch-ins", "Quick zooms on key words and the payoff"],
    ["FiRepeat", "Slow-mo replay", "Loops back into the opening; replays count"],
    ["FiMusic", "Ducked beat", "Royalty-free or generated, quiet under speech"],
    ["FiImage", "Cover image", "From the reaction frame, used as frame one"],
    ["FiTrendingUp", "Virality score", "0 to 100, to decide what to post first"],
  ];
  for (let i = 0; i < anat.length; i++) {
    const y = 2.0 + i * 0.6, dl = 1100 + i * 100;
    await iconDot(s, anat[i][0], 8.1, y, 0.46, K.cyan, ["rise", dl, 500]);
    txt(s, [{ text: anat[i][1] + "  ", options: { bold: true, color: K.text } }, { text: anat[i][2], options: { color: K.muted } }],
      { x: 8.72, y: y + 0.02, w: 4.05, h: 0.45, fontSize: 12, valign: "middle" }, ["rise", dl, 500]);
  }
  txt(s, "Frames from a ClipForge render of generated demo footage.", { x: 8.1, y: 6.85, w: 4.6, h: 0.25, fontSize: 10, color: K.dim }, ["fade", 2000, 500]);

  // ---------- 12. REVIEW FROM ANYWHERE ----------
  s = newSlide("CONTENT", "Act 3 · User transformation");
  kicker(s, "THE USER EXPERIENCE");
  title(s, "Review from your desk or your phone. Nothing posts without your yes");
  img(s, "generated/phone.png", { x: 0.35, y: 1.85, w: 5.15 * 1040 / 1640, h: 5.15 }, ["rise", 300, 900]);
  const tx = 4.05, ty = 2.1, tw = 4.45;
  card(s, tx, ty, tw, 4.55, { fill: K.surface2, transparency: 10 }, ["zoom", 700, 800]);
  txt(s, "TELEGRAM  ·  YOUR CLIPFORGE BOT", { x: tx + 0.25, y: ty + 0.18, w: tw - 0.5, h: 0.3, fontSize: 9.5, bold: true, color: K.cyan, charSpacing: 2 }, ["fade", 900, 500]);
  card(s, tx + 0.25, ty + 0.58, tw - 0.5, 1.95, { fill: K.bg1, transparency: 0, noShadow: true, r: 0.1 }, ["fade", 1000, 500]);
  txt(s, [
    { text: "Virality 88/100", options: { bold: true, color: K.amber, breakLine: true } },
    { text: "Hook: He did NOT see this coming", options: { breakLine: true } },
    { text: "The Craziest Move Ever", options: { bold: true, breakLine: true } },
    { text: "#gaming #clutch #viral", options: { color: K.violetL, breakLine: true } },
    { text: "32s vertical clip  ·  Copyright risk: medium", options: { color: K.muted } },
  ], { x: tx + 0.42, y: ty + 0.7, w: tw - 0.8, h: 1.75, fontSize: 12, lineSpacingMultiple: 1.18 }, ["fade", 1000, 500]);
  const btns = [["Approve & Publish", K.green, 0, 0, 2], ["Reject & Delete", K.pink, 2, 0, 1],
    ["Title", K.violetL, 0, 1, 1], ["Hook", K.violetL, 1, 1, 1], ["Cover", K.violetL, 2, 1, 1],
    ["YouTube", K.cyan, 0, 2, 1], ["Instagram", K.cyan, 1, 2, 1], ["Facebook", K.cyan, 2, 2, 1]];
  const bw = (tw - 0.5 - 0.2) / 3;
  btns.forEach(([t, col, cx, cy, span], i) => {
    const n = nm("btn");
    s.addText(t, { isTextBox: true, x: tx + 0.25 + cx * (bw + 0.1), y: ty + 2.7 + cy * 0.52, w: bw * span + 0.1 * (span - 1), h: 0.42,
      shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.08, fill: { color: col, transparency: 82 }, line: { color: col, width: 0.75, transparency: 30 },
      fontFace: BF, fontSize: 11, bold: true, color: K.text, align: "center", valign: "middle", margin: 0, objectName: n });
    anim(n, "rise", 1300 + i * 70, 400);
  });
  txt(s, "Example message format, reproduced from the ClipForge documentation.", { x: tx, y: 6.72, w: tw, h: 0.25, fontSize: 9.5, color: K.dim }, ["fade", 1800, 500]);
  const ux = [["FiCheck", "One tap to approve", "or reject, from anywhere."], ["FiEdit3", "Edit without an editor", "Swap the title, hook or cover; the clip re-renders in about a minute."],
    ["FiToggleRight", "Choose platforms per clip", "Untick any platform for this one post."], ["FiWifi", "Works on home Wi-Fi", "The bot needs no public address."]];
  for (let i = 0; i < ux.length; i++) {
    const y = 2.15 + i * 1.12, dl = 1700 + i * 180;
    await iconDot(s, ux[i][0], 8.85, y, 0.55, K.violetL, ["rise", dl, 600]);
    txt(s, ux[i][1], { x: 9.55, y: y - 0.02, w: 3.2, h: 0.35, fontFace: HF, fontSize: 15, bold: true }, ["rise", dl, 600]);
    txt(s, ux[i][2], { x: 9.55, y: y + 0.35, w: 3.2, h: 0.7, fontSize: 12, color: K.muted }, ["rise", dl, 600]);
  }

  // ---------- 13. BEFORE / AFTER ----------
  s = newSlide("CONTENT", "Act 3 · User transformation");
  kicker(s, "BEFORE VS AFTER");
  title(s, "The same clip, two very different workdays");
  chip(s, ILL, W - M - 1.25, 0.55, K.amber, 1.25, ["fade", 300, 500]);
  const colW = 5.55;
  const before = [["Scrub the footage for a moment", "20"], ["Cut and reframe to vertical", "10"], ["Transcribe and style captions", "15"], ["Write hook and title, make a cover", "10"], ["Write description and hashtags", "5"], ["Upload to four platforms", "12"]];
  const after = [["Paste the link or start the live recording", "1"], ["ClipForge finds, cuts, captions, packages and renders", "auto"], ["Review the clip; tweak the hook or cover if needed", "5"], ["Approve and pick platforms, or schedule", "1"]];
  card(s, M, 2.1, colW, 4.75, { fill: K.surface, transparency: 30 }, ["rise", 400, 700]);
  txt(s, "BY HAND", { x: M + 0.3, y: 2.3, w: 3, h: 0.3, fontSize: 10.5, bold: true, color: K.pink, charSpacing: 2 }, ["rise", 400, 700]);
  before.forEach(([a, b], i) => {
    const y = 2.75 + i * 0.5;
    txt(s, a, { x: M + 0.3, y, w: 4.1, h: 0.4, fontSize: 13, color: K.muted, valign: "middle" }, ["rise", 500 + i * 100, 500]);
    txt(s, b + " min", { x: M + colW - 1.35, y, w: 1.05, h: 0.4, fontSize: 13, bold: true, color: K.text, align: "right", valign: "middle" }, ["rise", 500 + i * 100, 500]);
  });
  txt(s, "≈72 min", { x: M + 0.3, y: 5.85, w: 3.5, h: 0.8, fontFace: HF, fontSize: 40, bold: true, color: K.pink }, ["zoom", 1200, 700]);
  txt(s, "hands-on per clip", { x: M + 3.0, y: 6.1, w: 2.3, h: 0.4, fontSize: 12, color: K.muted, align: "right" }, ["fade", 1200, 600]);
  const ar2 = nm("arrow");
  s.addShape(pres.shapes.RIGHT_ARROW, { x: M + colW + 0.27, y: 4.2, w: 0.55, h: 0.55, fill: { color: K.cyan }, line: { color: K.cyan, width: 0 }, objectName: ar2 });
  anim(ar2, "wipe", 1500, 500);
  const ax = M + colW + 1.1;
  card(s, ax, 2.1, colW, 4.75, { fill: K.violet, transparency: 82, line: K.violet }, ["rise", 1700, 700]);
  txt(s, "WITH CLIPFORGE", { x: ax + 0.3, y: 2.3, w: 3, h: 0.3, fontSize: 10.5, bold: true, color: K.cyan, charSpacing: 2 }, ["rise", 1700, 700]);
  after.forEach(([a, b], i) => {
    const y = 2.75 + i * 0.75;
    txt(s, a, { x: ax + 0.3, y, w: 4.1, h: 0.62, fontSize: 13, color: K.text, valign: "middle" }, ["rise", 1800 + i * 120, 500]);
    txt(s, b === "auto" ? "unattended" : b + " min", { x: ax + colW - 1.45, y, w: 1.15, h: 0.62, fontSize: 13, bold: true, color: b === "auto" ? K.cyan : K.text, align: "right", valign: "middle" }, ["rise", 1800 + i * 120, 500]);
  });
  txt(s, "≈7 min", { x: ax + 0.3, y: 5.85, w: 3.5, h: 0.8, fontFace: HF, fontSize: 40, bold: true, color: K.cyan }, ["zoom", 2400, 700]);
  txt(s, "hands-on per clip", { x: ax + 3.0, y: 6.1, w: 2.3, h: 0.4, fontSize: 12, color: K.muted, align: "right" }, ["fade", 2400, 600]);

  // ---------- 14. USE CASES ----------
  s = newSlide("CONTENT", "Act 3 · User transformation");
  kicker(s, "USE CASES");
  title(s, "Built for anyone sitting on hours of unclipped video");
  chip(s, "ILLUSTRATIVE SCENARIOS", W - M - 2.2, 0.55, K.amber, 2.2, ["fade", 300, 500]);
  const uses = [
    ["FiRadio", "The streamer", "ClipForge follows the live channel and cuts highlights while the stream is still running. Clips reach Telegram for approval before the stream ends.", "Live clipping  ·  Telegram review"],
    ["FiMic", "The podcaster", "The face-following crop keeps whoever is talking in frame, and word-by-word captions carry the conversation for silent scrollers.", "Smart crop  ·  Pop captions"],
    ["FiBriefcase", "The brand", "Running a paid clipping campaign: required hashtags and @mentions are added automatically, post links are collected, and earnings are estimated from views.", "Campaigns  ·  Earnings estimate"],
    ["FiUsers", "The clipping studio", "One operator runs several channels, one niche each, posting 1 to 3 clips a day per channel at the times viewers are online.", "Scheduling  ·  Multi-platform"],
  ];
  const uw = (CW - 3 * 0.25) / 4;
  for (let i = 0; i < uses.length; i++) {
    const x = M + i * (uw + 0.25), y = 2.15, dl = 400 + i * 200;
    card(s, x, y, uw, 4.7, {}, ["rise", dl, 700]);
    await iconDot(s, uses[i][0], x + 0.28, y + 0.3, 0.75, i % 2 ? K.cyan : K.violetL, ["rise", dl, 700]);
    txt(s, uses[i][1], { x: x + 0.28, y: y + 1.25, w: uw - 0.5, h: 0.4, fontFace: HF, fontSize: 18, bold: true }, ["rise", dl, 700]);
    txt(s, uses[i][2], { x: x + 0.28, y: y + 1.75, w: uw - 0.5, h: 2.2, fontSize: 12.5, color: K.muted, lineSpacingMultiple: 1.12 }, ["rise", dl, 700]);
    txt(s, uses[i][3], { x: x + 0.28, y: y + 4.1, w: uw - 0.5, h: 0.35, fontSize: 10.5, bold: true, color: K.cyan }, ["rise", dl, 700]);
  }

  // ---------- 15. IMPACT ----------
  pres.addSection({ title: "Act 4 · Business value" });
  s = newSlide("CONTENT", "Act 4 · Business value");
  kicker(s, "BUSINESS IMPACT");
  title(s, "Clipping stops being a daily chore and becomes a supervised system");
  const kp = [["Faster", "Hands-on time per clip drops from about an hour to minutes.", K.violetL], ["Consistent", "The same packaging standard on every clip, every day.", K.cyan],
    ["Wider reach", "One approval publishes to up to four platforms.", K.pink], ["Lower cost", "Runs on your own computer. Local AI has no per-clip fee.", K.green]];
  kp.forEach(([a, b, col], i) => {
    const y = 2.1 + i * 1.18, dl = 400 + i * 180;
    card(s, M, y, 5.4, 1.05, {}, ["rise", dl, 600]);
    txt(s, a, { x: M + 0.3, y: y + 0.12, w: 2.2, h: 0.8, fontFace: HF, fontSize: 22, bold: true, color: col, valign: "middle" }, ["rise", dl, 600]);
    txt(s, b, { x: M + 2.35, y: y + 0.12, w: 2.85, h: 0.8, fontSize: 12.5, color: K.muted, valign: "middle" }, ["rise", dl, 600]);
  });
  txt(s, "Clips supervised in a 2-hour daily block", { x: 6.55, y: 2.1, w: 4.5, h: 0.35, fontFace: HF, fontSize: 15, bold: true }, ["fade", 1200, 500]);
  chip(s, ILL, W - M - 1.25, 2.12, K.amber, 1.25, ["fade", 1200, 500]);
  const ch2 = nm("chart");
  s.addChart(pres.charts.BAR, [{ name: "Clips per day", labels: ["By hand", "With ClipForge"], values: [2, 17] }], {
    x: 6.45, y: 2.55, w: 6.3, h: 3.65, barDir: "col", objectName: ch2, chartColors: [K.dim, K.violet], barGapWidthPct: 70,
    catAxisLabelColor: K.text, catAxisLabelFontSize: 13, catAxisLabelFontFace: "+mn-lt", catAxisLineShow: false,
    valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: K.text, dataLabelFontSize: 20, dataLabelFontFace: "+mj-lt", dataLabelFontBold: true,
    showLegend: false, plotArea: { fill: { color: K.bg0, transparency: 100 } },
  });
  anim(ch2, "rise", 1400, 1000);
  txt(s, "120 minutes ÷ 72 min per clip by hand ≈ 1.7; 120 ÷ 7 min of review ≈ 17. Same assumptions as the cost slide.",
    { x: 6.55, y: 6.3, w: 6.2, h: 0.5, fontSize: 10.5, color: K.dim }, ["fade", 2000, 500]);

  // ---------- 16. ROI ----------
  s = newSlide("CONTENT", "Act 4 · Business value");
  kicker(s, "ROI / BUSINESS CASE");
  title(s, "A transparent model: hours saved times your cost of an hour");
  card(s, M, 2.1, 6.0, 1.15, { fill: K.violet, transparency: 80, line: K.violet }, ["zoom", 400, 700]);
  txt(s, [{ text: "Weekly hours saved  =  ", options: { color: K.muted } }, { text: "clips per week × (manual min − review min) ÷ 60", options: { bold: true } }],
    { x: M + 0.3, y: 2.2, w: 5.5, h: 0.95, fontSize: 14.5, valign: "middle" }, ["zoom", 400, 700]);
  const roiRows = [["Clips per week", "21", "3 a day"], ["Manual minutes per clip", "72", "cost slide"], ["Review minutes per clip", "7", "before/after slide"], ["Cost of an editing hour", "$20", "placeholder"],
    ["Hours saved per week", "≈ 22.75", "21 × 65 ÷ 60"], ["Value per week", "≈ $455", "22.75 × $20"], ["Value per month", "≈ $1,970", "× 4.33 weeks"]];
  const tbl = [[{ text: "INPUT (EXAMPLE)", options: { bold: true, color: K.violetL, fontSize: 10 } }, { text: "VALUE", options: { bold: true, color: K.violetL, fontSize: 10, align: "right" } }, { text: "SOURCE", options: { bold: true, color: K.violetL, fontSize: 10 } }],
    ...roiRows.map(([a, b, c], i) => [{ text: a, options: { color: i >= 4 ? K.text : K.muted, bold: i >= 4 } }, { text: b, options: { align: "right", bold: true, color: i >= 4 ? K.cyan : K.text } }, { text: c, options: { color: K.dim } }])];
  const tn = nm("table");
  s.addTable(tbl, { x: M, y: 3.45, w: 6.0, colW: [2.75, 1.3, 1.95], rowH: 0.36, fontFace: BF, fontSize: 12.5, color: K.text, objectName: tn,
    border: { type: "solid", pt: 0.5, color: K.line }, fill: { color: K.surface }, margin: [0.03, 0.12, 0.03, 0.12], valign: "middle" });
  anim(tn, "rise", 800, 800);
  txt(s, "Weekly hands-on hours, by volume", { x: 7.0, y: 2.1, w: 4.4, h: 0.35, fontFace: HF, fontSize: 15, bold: true }, ["fade", 1000, 500]);
  chip(s, ILL, W - M - 1.25, 2.12, K.amber, 1.25, ["fade", 1000, 500]);
  const ch3 = nm("chart");
  s.addChart(pres.charts.BAR, [
    { name: "By hand", labels: ["7 clips/wk", "21 clips/wk", "42 clips/wk"], values: [8.4, 25.2, 50.4] },
    { name: "With ClipForge", labels: ["7 clips/wk", "21 clips/wk", "42 clips/wk"], values: [0.8, 2.5, 4.9] }], {
    x: 6.9, y: 2.5, w: 5.9, h: 3.55, barDir: "col", objectName: ch3, chartColors: [K.dim, K.violet], barGapWidthPct: 60,
    catAxisLabelColor: K.text, catAxisLabelFontSize: 12, catAxisLabelFontFace: "+mn-lt", catAxisLineShow: false,
    valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: K.text, dataLabelFontSize: 12, dataLabelFontFace: "+mn-lt", dataLabelFormatCode: '0.0" h"',
    showLegend: true, legendPos: "t", legendColor: K.muted, legendFontSize: 11, legendFontFace: "+mn-lt", plotArea: { fill: { color: K.bg0, transparency: 100 } },
  });
  anim(ch3, "rise", 1200, 1000);
  txt(s, [{ text: "To validate in a pilot: ", options: { bold: true, color: K.text } }, { text: "real minutes per clip, approval rate, views per clip, and payout per 1K views. Costs: the software is free and local; cloud AI is optional; our service fee is agreed separately.", options: { color: K.muted } }],
    { x: 6.95, y: 6.1, w: 5.8, h: 0.8, fontSize: 11, lineSpacingMultiple: 1.08 }, ["fade", 1800, 600]);

  // ---------- 17. WHY DIFFERENT ----------
  s = newSlide("CONTENT_RIGHT", "Act 4 · Business value");
  kicker(s, "DIFFERENTIATION");
  title(s, "What sets ClipForge apart: where it runs and who stays in control");
  const diff = [
    ["FiHardDrive", "Local-first", "Runs on your computer. Videos, tokens and settings stay with you; local AI via Ollama costs nothing per clip."],
    ["FiRadio", "Any source, even live", "Links from major platforms, uploaded files, and live streams clipped while they run."],
    ["FiUserCheck", "Human in the loop", "Nothing is posted without your approval, in the web app or in Telegram."],
    ["FiShield", "Rights-aware", "A copyright and monetisation check on every source, with clear advice."],
    ["FiLayers", "Platform-native", "Each platform's rules applied: Shorts length, Reels hashtag cap, Facebook's 3 to 90 s window."],
    ["FiCode", "Open and model-agnostic", "Works with Ollama or Claude, ChatGPT, Gemini and others. Code you can inspect, with 73 automated tests."],
  ];
  for (let i = 0; i < diff.length; i++) {
    const x = M + (i % 2) * 4.05, y = 2.15 + Math.floor(i / 2) * 1.55, dl = 400 + i * 150;
    await iconDot(s, diff[i][0], x, y, 0.62, K.violetL, ["rise", dl, 600]);
    txt(s, diff[i][1], { x: x + 0.8, y: y - 0.02, w: 3.1, h: 0.35, fontFace: HF, fontSize: 15.5, bold: true }, ["rise", dl, 600]);
    txt(s, diff[i][2], { x: x + 0.8, y: y + 0.36, w: 3.1, h: 1.05, fontSize: 11.5, color: K.muted, lineSpacingMultiple: 1.08 }, ["rise", dl, 600]);
  }
  card(s, 9.05, 2.15, 3.68, 4.55, { fill: K.violet, transparency: 78, line: K.violetL }, ["zoom", 1400, 900]);
  txt(s, [{ text: "Your computer.", options: { breakLine: true } }, { text: "Your accounts.", options: { breakLine: true } }, { text: "Your approval.", options: { color: K.cyan } }],
    { x: 9.35, y: 2.5, w: 3.2, h: 2.4, fontFace: HF, fontSize: 28, bold: true, lineSpacingMultiple: 1.05 }, ["zoom", 1400, 900]);
  txt(s, "ClipForge is built so the client never hands over control of their content, their channels or their decision to post.",
    { x: 9.35, y: 5.0, w: 3.15, h: 1.4, fontSize: 12.5, color: K.text, lineSpacingMultiple: 1.1 }, ["fade", 1900, 600]);

  // ---------- 18. ARCHITECTURE ----------
  pres.addSection({ title: "Act 5 · Technology, execution & trust" });
  s = newSlide("CONTENT", "Act 5 · Technology, execution & trust");
  kicker(s, "TECHNOLOGY & ARCHITECTURE");
  title(s, "Proven open-source building blocks, assembled into one local pipeline");
  const col = (x, w, head, color, items, dl, yy = 2.1, hh = 3.55) => {
    card(s, x, yy, w, hh, { fill: K.surface, transparency: 15, line: color }, ["rise", dl, 700]);
    txt(s, head, { x: x + 0.2, y: yy + 0.15, w: w - 0.4, h: 0.3, fontSize: 10.5, bold: true, color, charSpacing: 2 }, ["rise", dl, 700]);
    items.forEach(([a, b], i) => txt(s, [{ text: a, options: { bold: true, breakLine: true } }, { text: b, options: { color: K.muted, fontSize: 10.5 } }],
      { x: x + 0.2, y: yy + 0.55 + i * 0.72, w: w - 0.4, h: 0.68, fontSize: 12.5 }, ["rise", dl + 100, 600]));
  };
  col(M, 2.3, "SOURCES", K.pink, [["Video links", "yt-dlp: YouTube, Twitch, Kick, TikTok, X…"], ["Uploads", "any local file"], ["Live streams", "recorded in segments"]], 400);
  // engine
  const ex = M + 2.3 + 0.45, ew = 5.4;
  card(s, ex, 2.1, ew, 3.55, { fill: K.violet, transparency: 84, line: K.violetL, lineW: 1.5 }, ["zoom", 800, 800]);
  txt(s, "CLIPFORGE ENGINE  ·  ON YOUR COMPUTER", { x: ex + 0.2, y: 2.25, w: ew - 0.4, h: 0.3, fontSize: 10.5, bold: true, color: K.violetL, charSpacing: 2 }, ["zoom", 800, 800]);
  const mods = [["Highlight finder", "librosa audio + OpenCV motion"], ["Speech to text", "faster-whisper (local)"], ["AI writer", "Ollama local, or cloud LLM"],
    ["Video editor", "FFmpeg + libass captions"], ["SEO & rights", "per-platform text, risk check"], ["Jobs & scheduler", "SQLite job store, FastAPI"]];
  const mw = (ew - 0.4 - 0.2) / 2;
  mods.forEach(([a, b], i) => {
    const x = ex + 0.2 + (i % 2) * (mw + 0.2), y = 2.7 + Math.floor(i / 2) * 0.95, dl = 1000 + i * 100;
    card(s, x, y, mw, 0.82, { fill: K.bg1, transparency: 10, noShadow: true, r: 0.08 }, ["rise", dl, 500]);
    txt(s, [{ text: a, options: { bold: true, breakLine: true } }, { text: b, options: { color: K.muted, fontSize: 10.5 } }], { x: x + 0.15, y: y + 0.08, w: mw - 0.3, h: 0.68, fontSize: 12.5, valign: "middle" }, ["rise", dl, 500]);
  });
  col(ex + ew + 0.45, W - M - (ex + ew + 0.45), "REVIEW  →  PUBLISH", K.cyan, [["Web app", "127.0.0.1:8000, desktop or phone"], ["Telegram bot", "approve, edit, choose platforms"], ["Official APIs", "YouTube Data, Instagram Graph, Facebook Pages, TikTok Content Posting"]], 1700);
  [[M + 2.3 + 0.05, 1300], [ex + ew + 0.05, 1600]].forEach(([x, dl]) => {
    const n = nm("arrow");
    s.addShape(pres.shapes.RIGHT_ARROW, { x, y: 3.65, w: 0.35, h: 0.4, fill: { color: K.cyan }, line: { color: K.cyan, width: 0 }, objectName: n });
    anim(n, "wipe", dl, 400);
  });
  card(s, M, 5.9, 6.0, 0.95, { fill: K.surface2, transparency: 20, noShadow: true }, ["rise", 2100, 600]);
  txt(s, [{ text: "Security by default  ", options: { bold: true, color: K.green } }, { text: "Listens only on this computer; a token is required for network access; tokens masked in logs; secrets never committed.", options: { color: K.muted } }],
    { x: M + 0.2, y: 5.95, w: 5.6, h: 0.85, fontSize: 11.5, valign: "middle" }, ["rise", 2100, 600]);
  card(s, M + 6.15, 5.9, CW - 6.15, 0.95, { fill: K.surface2, transparency: 20, noShadow: true }, ["rise", 2250, 600]);
  txt(s, [{ text: "Two ways to deploy  ", options: { bold: true, color: K.cyan } }, { text: "One process on a Mac, Windows or Linux computer, or Docker on a server (Redis + Celery workers).", options: { color: K.muted } }],
    { x: M + 6.35, y: 5.95, w: CW - 6.55, h: 0.85, fontSize: 11.5, valign: "middle" }, ["rise", 2250, 600]);

  // ---------- 19. LIMITS / TRUST ----------
  s = newSlide("CONTENT", "Act 5 · Technology, execution & trust");
  kicker(s, "RISKS, LIMITS & ASSUMPTIONS");
  title(s, "What we tell every client before we start");
  const risks = [
    ["FiAlertTriangle", "Copyright and reused content", "Re-uploads can be claimed or demonetised.", "We run the rights check, prefer creators who allow clipping, add commentary and credit the original."],
    ["FiEye", "Views are never guaranteed", "Packaging raises the odds; content, niche and channel age still matter.", "We post consistently, one niche per channel, and learn from early results."],
    ["FiYoutube", "YouTube API limits", "About 6 uploads a day per Google project; unaudited projects can be locked to private.", "Visibility is checked after every upload; we file Google's free audit form."],
    ["FiSmartphone", "TikTok and Facebook rules", "TikTok posts stay private until its app audit; Facebook Reels must be 3 to 90 s.", "Per-clip platform choice; longer clips skip Facebook automatically."],
    ["FiCpu", "AI drafts, people decide", "AI titles and hooks can miss the mark.", "Every clip is reviewed; alternatives are one tap away."],
    ["FiHardDrive", "Setup needs", "First run downloads a ~500 MB speech model; local AI needs ~5 GB.", "We set it up, or run it for you as a service."],
  ];
  const rw = (CW - 2 * 0.25) / 3, rh = 2.2;
  for (let i = 0; i < risks.length; i++) {
    const [ic, h, r, m] = risks[i];
    const x = M + (i % 3) * (rw + 0.25), y = 2.1 + Math.floor(i / 3) * (rh + 0.2), dl = 400 + i * 150;
    card(s, x, y, rw, rh, {}, ["rise", dl, 600]);
    await iconDot(s, ic, x + 0.22, y + 0.22, 0.5, K.amber, ["rise", dl, 600]);
    txt(s, h, { x: x + 0.85, y: y + 0.24, w: rw - 1.05, h: 0.45, fontFace: HF, fontSize: 14, bold: true, valign: "middle" }, ["rise", dl, 600]);
    txt(s, r, { x: x + 0.22, y: y + 0.85, w: rw - 0.44, h: 0.6, fontSize: 11.5, color: K.muted, lineSpacingMultiple: 1.05 }, ["rise", dl, 600]);
    txt(s, [{ text: "How we handle it: ", options: { bold: true, color: K.green } }, { text: m, options: { color: K.text } }], { x: x + 0.22, y: y + 1.45, w: rw - 0.44, h: 0.7, fontSize: 11, lineSpacingMultiple: 1.05 }, ["rise", dl, 600]);
  }

  // ---------- 20. ROADMAP ----------
  s = newSlide("CONTENT", "Act 5 · Technology, execution & trust");
  kicker(s, "ROADMAP");
  title(s, "Shipping today, with a clear path to what's next");
  const road = [
    ["AVAILABLE NOW", K.green, "FiCheckCircle", ["Links, uploads and live streams", "Moment finder with AI re-ranking", "Smart crop in four shapes", "Viral edit: captions, hooks, replay, beat", "SEO packaging and copyright check", "YouTube, Instagram, Facebook, TikTok", "Web app, Telegram bot, scheduling", "Campaign tracking and earnings estimate"]],
    ["NEXT  ·  PLANNED", K.cyan, "FiArrowRightCircle", ["Highlights that span live-recording segments", "Panning crop that follows action across the frame", "Live chat spikes as an excitement signal", "Automatic disk clean-up for long sessions", "Hardening on real accounts at volume"]],
    ["EXPLORING  ·  IDEAS", K.violetL, "FiCompass", ["Multi-client workspaces", "Learning from which clips get views", "Team approval roles", "No dates committed"]],
  ];
  const rdw = (CW - 2 * 0.3) / 3;
  for (let i = 0; i < road.length; i++) {
    const [h, colr, ic, items] = road[i];
    const x = M + i * (rdw + 0.3), dl = 400 + i * 400;
    card(s, x, 2.1, rdw, 4.75, { fill: i === 0 ? K.surface : K.surface, transparency: i === 0 ? 10 : 35, line: colr }, ["rise", dl, 700]);
    await iconDot(s, ic, x + 0.28, 2.32, 0.55, colr, ["rise", dl, 700]);
    txt(s, h, { x: x + 0.98, y: 2.42, w: rdw - 1.2, h: 0.35, fontSize: 11, bold: true, color: colr, charSpacing: 2 }, ["rise", dl, 700]);
    txt(s, items.map((t, j) => ({ text: t, options: { bullet: { indent: 14 }, breakLine: j < items.length - 1, color: t.startsWith("No dates") ? K.dim : K.text, italic: t.startsWith("No dates") } })),
      { x: x + 0.28, y: 3.1, w: rdw - 0.5, h: 3.6, fontSize: 12.5, paraSpaceAfter: 7 }, ["rise", dl + 100, 700]);
  }

  // ---------- 21. IMPLEMENTATION ----------
  s = newSlide("CONTENT", "Act 5 · Technology, execution & trust");
  kicker(s, "IMPLEMENTATION & ADOPTION");
  title(s, "Start small: a two-week pilot, then scale what works");
  const phases = [["WEEK 0", "Discovery", "Goals, channels, niches and content rights."], ["WEEK 1", "Setup", "Install or managed run, connect accounts, brand kit: watermark, hashtags, caption style."],
    ["WEEKS 1–2", "Pilot", "Daily clips sent for your approval in the app or Telegram."], ["WEEK 3", "Review & scale", "Results against the baseline, then choose the model that fits."]];
  const tlx = M, tlw = CW, tly = 2.55;
  const tline = nm("tline");
  s.addShape(pres.shapes.LINE, { x: tlx + 0.2, y: tly, w: tlw - 0.4, h: 0, line: { color: K.violet, width: 2.5 }, objectName: tline });
  anim(tline, "wipe", 300, 1400);
  const phw = tlw / 4;
  phases.forEach(([wk, h, d], i) => {
    const x = tlx + i * phw, dl = 500 + i * 300;
    const dot = nm("dot");
    s.addShape(pres.shapes.OVAL, { x: x + 0.12, y: tly - 0.14, w: 0.28, h: 0.28, fill: { color: i === 2 ? K.cyan : K.violet }, line: { color: K.text, width: 1.5 }, objectName: dot });
    anim(dot, "zoom", dl, 400);
    txt(s, wk, { x: x + 0.12, y: tly + 0.3, w: phw - 0.3, h: 0.3, fontSize: 10.5, bold: true, color: i === 2 ? K.cyan : K.violetL, charSpacing: 2 }, ["rise", dl, 500]);
    txt(s, h, { x: x + 0.12, y: tly + 0.62, w: phw - 0.3, h: 0.4, fontFace: HF, fontSize: 17, bold: true }, ["rise", dl, 500]);
    txt(s, d, { x: x + 0.12, y: tly + 1.05, w: phw - 0.35, h: 0.85, fontSize: 12, color: K.muted, lineSpacingMultiple: 1.08 }, ["rise", dl, 500]);
  });
  const models = [["FiPackage", "Done-for-you clipping", "We run ClipForge on our side. You send sources or channels and approve clips from your phone. Best for creators who want zero setup."],
    ["FiTool", "Your own ClipForge", "We install it on your computer or server, connect your accounts, set your brand and train your team. Best for agencies and in-house teams."]];
  for (let i = 0; i < models.length; i++) {
    const x = M + i * (CW / 2 + 0.12), w = CW / 2 - 0.12, y = 4.6, dl = 1800 + i * 200;
    card(s, x, y, w, 1.75, { fill: K.violet, transparency: 84, line: K.violet }, ["rise", dl, 600]);
    await iconDot(s, models[i][0], x + 0.25, y + 0.3, 0.6, K.cyan, ["rise", dl, 600]);
    txt(s, models[i][1], { x: x + 1.05, y: y + 0.25, w: w - 1.3, h: 0.38, fontFace: HF, fontSize: 16, bold: true }, ["rise", dl, 600]);
    txt(s, models[i][2], { x: x + 1.05, y: y + 0.68, w: w - 1.3, h: 0.95, fontSize: 12, color: K.muted, lineSpacingMultiple: 1.08 }, ["rise", dl, 600]);
  }
  txt(s, "Proposed plan. Timing depends on platform approvals (Google, Meta, TikTok) that sit outside our control.", { x: M, y: 6.5, w: CW, h: 0.3, fontSize: 10, color: K.dim }, ["fade", 2300, 500]);

  // ---------- 22. WHY US ----------
  s = newSlide("CONTENT_RIGHT", "Act 5 · Technology, execution & trust");
  kicker(s, "WHY WORK WITH US");
  title(s, "We built the engine, we use it daily, and we stay accountable");
  const why = [["FiLayers", "Built end to end", "Ingest, AI, editing, review and publishing: one team owns the whole pipeline."],
    ["FiGitMerge", "Fast iteration", "Nine merged releases between 17 and 21 Nov 2026, with 73 automated tests."],
    ["FiShield", "Honest by design", "Visibility checks, copyright warnings and clearly labelled estimates are part of the product."],
    ["FiHeart", "Creator-first packaging", "Hooks, captions, covers and SEO built from how short-form feeds work."],
    ["FiLock", "Your data stays yours", "Your accounts, tokens and footage live on your machine or under your control."]];
  for (let i = 0; i < why.length; i++) {
    const y = 2.1 + i * 0.94, dl = 400 + i * 150;
    await iconDot(s, why[i][0], M, y, 0.6, K.violetL, ["rise", dl, 600]);
    txt(s, why[i][1], { x: M + 0.8, y: y - 0.02, w: 5.6, h: 0.35, fontFace: HF, fontSize: 15.5, bold: true }, ["rise", dl, 600]);
    txt(s, why[i][2], { x: M + 0.8, y: y + 0.34, w: 5.6, h: 0.5, fontSize: 12, color: K.muted }, ["rise", dl, 600]);
  }
  img(s, "generated/tilt_publish.png", { x: 7.15, y: 2.0, w: 5.9, h: 5.9 * 1280 / 1800 }, ["zoom", 900, 1100]);
  txt(s, "ClipForge Publishing page: YouTube, Instagram, Facebook and TikTok connections.", { x: 7.5, y: 6.3, w: 5.2, h: 0.4, fontSize: 10, color: K.dim, align: "center" }, ["fade", 1600, 500]);

  // ---------- 23. CTA ----------
  pres.addSection({ title: "Act 6 · The next step" });
  s = newSlide("CENTER", "Act 6 · The next step");
  txt(s, "THE NEXT STEP", { x: 0, y: 0.85, w: W, h: 0.3, fontSize: 11, bold: true, color: K.cyan, charSpacing: 4, align: "center" }, ["fade", 0, 500]);
  txt(s, "Let's clip your next stream together", { x: 0, y: 1.2, w: W, h: 0.95, fontFace: HF, fontSize: 44, bold: true, align: "center" }, ["zoom", 200, 900]);
  const cta = [["1", "Send us 2–3 recent videos", "or your channel link."], ["2", "We clip them with ClipForge", "and send every clip for your approval."], ["3", "We review results together", "and agree the plan that fits."]];
  const cw3 = 3.6, cx0 = (W - (3 * cw3 + 2 * 0.3)) / 2;
  cta.forEach(([n, a, b], i) => {
    const x = cx0 + i * (cw3 + 0.3), y = 2.65, dl = 800 + i * 250;
    card(s, x, y, cw3, 2.1, { fill: K.surface, transparency: 15 }, ["rise", dl, 700]);
    txt(s, n, { x: x + 0.3, y: y + 0.2, w: 1, h: 0.6, fontFace: HF, fontSize: 30, bold: true, color: K.violetL }, ["rise", dl, 700]);
    txt(s, a, { x: x + 0.3, y: y + 0.85, w: cw3 - 0.6, h: 0.4, fontFace: HF, fontSize: 16, bold: true }, ["rise", dl, 700]);
    txt(s, b, { x: x + 0.3, y: y + 1.28, w: cw3 - 0.6, h: 0.6, fontSize: 13, color: K.muted }, ["rise", dl, 700]);
  });
  card(s, cx0, 5.05, 3 * cw3 + 0.6, 1.35, { fill: K.violet, transparency: 75, line: K.violetL }, ["zoom", 1700, 800]);
  txt(s, "Book a 30-minute walkthrough of the live product", { x: cx0 + 0.35, y: 5.15, w: 6.5, h: 0.45, fontFace: HF, fontSize: 17, bold: true, valign: "middle" }, ["zoom", 1700, 800]);
  txt(s, "[Your name]   ·   [email@yourdomain.com]   ·   [phone or Telegram]", { x: cx0 + 0.35, y: 5.65, w: 7.5, h: 0.5, fontSize: 14, color: K.cyan, valign: "middle" }, ["zoom", 1700, 800]);
  img(s, "generated/logo.png", { x: cx0 + 3 * cw3 + 0.6 - 1.25, y: 5.22, w: 1.0, h: 1.0, objectName: "!!logo" }, ["zoom", 1900, 800]);

  // ---------- 24. CLOSING ----------
  s = newSlide("CENTER", "Act 6 · The next step", "morph");
  img(s, "generated/logo.png", { x: (W - 1.5) / 2, y: 1.6, w: 1.5, h: 1.5, objectName: "!!logo" });
  txt(s, "Your best moments, everywhere.", { x: 0, y: 3.35, w: W, h: 1.0, fontFace: HF, fontSize: 46, bold: true, align: "center" }, ["rise", 900, 1000]);
  txt(s, "Every long video is a library of shorts waiting to be found.", { x: 0, y: 4.4, w: W, h: 0.5, fontSize: 18, color: K.muted, align: "center" }, ["rise", 1400, 900]);
  txt(s, "ClipForge", { x: 0, y: 5.6, w: W, h: 0.4, fontFace: HF, fontSize: 16, bold: true, color: K.violetL, align: "center", charSpacing: 4 }, ["fade", 2000, 800]);

  // ---------- write ----------
  const raw = path.join(OUT, "ClipForge_Client_Pitch_Deck.raw.pptx");
  await pres.writeFile({ fileName: raw });
  await applyTheme(raw, THEME);
  fs.writeFileSync(path.join(OUT, "motion.json"), JSON.stringify(MOTION, null, 1));
  console.log("slides:", MOTION.length, "->", raw);
})().catch((e) => { console.error(e); process.exit(1); });
