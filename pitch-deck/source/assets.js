// Renders the deck's 3D / glass visual assets from the REAL ClipForge screenshots,
// using Chromium CSS 3D transforms (perspective, rotateX/Y, translateZ) + layered glows.
// Usage: node assets.js            -> assets/generated/*.png
//        node assets.js --video    -> also assets/generated/hero_loop.mp4 (needs ffmpeg)
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const { execSync } = require("child_process");

const S = path.join(__dirname, "assets", "screens");
const OUT = path.join(__dirname, "assets", "generated");
fs.mkdirSync(OUT, { recursive: true });
const _cache = {};
const img = (f) => (_cache[f] ||= "data:image/" + (f.endsWith(".png") ? "png" : "jpeg") + ";base64," +
  fs.readFileSync(path.join(S, f)).toString("base64"));

// Design tokens (mirrors tokens in build_deck.js)
const T = {
  bg0: "#07071A", bg1: "#0D0B24", violet: "#7C5CFF", violetL: "#A78BFA",
  cyan: "#22D3EE", pink: "#F472B6", text: "#F5F5FF",
};

const base = (w, h, body, extraCss = "") => `<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:${w}px;height:${h}px;background:transparent;overflow:hidden}
.stage{position:absolute;inset:0;perspective:2400px;perspective-origin:50% 40%}
.screen{position:absolute;border-radius:18px;overflow:hidden;
  box-shadow:0 0 0 1.5px rgba(167,139,250,.55),0 0 0 7px rgba(255,255,255,.05)}
.screen img{display:block;width:100%;height:100%;object-fit:cover}
.screen::after{content:"";position:absolute;inset:0;
  background:linear-gradient(125deg,rgba(255,255,255,.14) 0%,rgba(255,255,255,0) 32%);pointer-events:none}
.phone{position:absolute;border-radius:34px;overflow:hidden;background:#000;
  box-shadow:0 0 0 2px rgba(167,139,250,.7),0 0 0 10px #12102a,0 0 0 11.5px rgba(167,139,250,.35)}
.phone img{display:block;width:100%;height:100%;object-fit:cover}
.phone::after{content:"";position:absolute;inset:0;
  background:linear-gradient(120deg,rgba(255,255,255,.18) 0%,rgba(255,255,255,0) 30%)}
.glow{position:absolute;background:radial-gradient(closest-side,var(--c),transparent)}
${extraCss}</style></head><body>${body}</body></html>`;

const assets = [];

// ---------- Backgrounds (opaque, 1333x750 CSS px -> 2666x1500 at 2x) ----------
const bg = (name, layers, grid = false) => assets.push({
  name, w: 1333, h: 750, opaque: true,
  html: base(1333, 750, `<div style="position:absolute;inset:0;background:${T.bg0}"></div>${layers}
  ${grid ? `<div style="position:absolute;left:-30%;right:-30%;bottom:-8%;height:58%;
     transform:perspective(700px) rotateX(64deg);transform-origin:50% 100%;
     background-image:linear-gradient(rgba(124,92,255,.22) 1px,transparent 1px),linear-gradient(90deg,rgba(124,92,255,.22) 1px,transparent 1px);
     background-size:64px 64px;-webkit-mask-image:linear-gradient(to top,rgba(0,0,0,.9),transparent 85%)"></div>` : ""}
  <div style="position:absolute;inset:0;background:radial-gradient(ellipse at 50% 50%,transparent 55%,rgba(0,0,0,.45) 100%)"></div>`),
});
bg("bg_cover", `<div class="glow" style="left:620px;top:40px;width:760px;height:620px;--c:${T.violet};opacity:.42"></div>
  <div class="glow" style="left:980px;top:420px;width:420px;height:360px;--c:${T.cyan};opacity:.22"></div>
  <div class="glow" style="left:-160px;top:-200px;width:520px;height:460px;--c:${T.pink};opacity:.13"></div>`, true);
bg("bg_content", `<div class="glow" style="left:-220px;top:-260px;width:700px;height:520px;--c:${T.violet};opacity:.22"></div>
  <div class="glow" style="left:1050px;top:520px;width:520px;height:420px;--c:${T.cyan};opacity:.10"></div>`);
bg("bg_right", `<div class="glow" style="left:760px;top:60px;width:700px;height:640px;--c:${T.violet};opacity:.30"></div>
  <div class="glow" style="left:1100px;top:500px;width:400px;height:320px;--c:${T.cyan};opacity:.14"></div>`);
bg("bg_center", `<div class="glow" style="left:330px;top:120px;width:680px;height:520px;--c:${T.violet};opacity:.40"></div>
  <div class="glow" style="left:520px;top:380px;width:380px;height:300px;--c:${T.cyan};opacity:.18"></div>`, true);
bg("bg_problem", `<div class="glow" style="left:-200px;top:300px;width:700px;height:600px;--c:${T.pink};opacity:.14"></div>
  <div class="glow" style="left:900px;top:-300px;width:700px;height:520px;--c:${T.violet};opacity:.18"></div>`);

// ---------- Hero: real clips page in 3D + three real finished-clip frames floating ----------
const heroBody = (t = 0) => {
  const ry = -20 + 4 * Math.sin(t * 2 * Math.PI);
  const fy = (k) => 10 * Math.sin(t * 2 * Math.PI + k);
  return `<div class="glow" style="left:120px;top:60px;width:900px;height:700px;--c:${T.violet};opacity:.55"></div>
   <div class="glow" style="left:600px;top:420px;width:500px;height:380px;--c:${T.cyan};opacity:.25"></div>
   <div class="stage">
   <div class="screen" style="left:190px;top:150px;width:760px;height:481px;
      transform:rotateY(${ry}deg) rotateX(9deg) rotateZ(-2deg) translateZ(-120px)"><img src="${img("clips.jpg")}"></div>
   <div class="phone" style="left:120px;top:${360 + fy(0)}px;width:180px;height:320px;transform:rotateY(${ry + 8}deg) rotateX(6deg) translateZ(110px)"><img src="${img("frame1.png")}"></div>
   <div class="phone" style="left:660px;top:${300 + fy(2)}px;width:165px;height:293px;transform:rotateY(${ry - 4}deg) rotateX(6deg) translateZ(150px)"><img src="${img("frame2.png")}"></div>
   <div class="phone" style="left:850px;top:${110 + fy(4)}px;width:145px;height:258px;transform:rotateY(${ry - 10}deg) rotateX(6deg) translateZ(40px)"><img src="${img("frame3.png")}"></div>
  </div>`;
};
assets.push({ name: "hero", w: 1140, h: 800, html: base(1140, 800, heroBody(0)) });

// ---------- Showcase: three real screens stacked in depth ----------
assets.push({
  name: "showcase", w: 1100, h: 700, html: base(1100, 700, `<div class="stage" style="perspective:2000px">
   <div class="glow" style="left:250px;top:150px;width:620px;height:420px;--c:${T.violet};opacity:.4"></div>
   <div class="screen" style="left:40px;top:60px;width:640px;height:405px;transform:rotateY(24deg) rotateX(6deg) translateZ(-260px)"><img src="${img("create.jpg")}"></div>
   <div class="screen" style="left:250px;top:150px;width:660px;height:418px;transform:rotateY(24deg) rotateX(6deg) translateZ(-60px)"><img src="${img("clips.jpg")}"></div>
   <div class="screen" style="left:770px;top:70px;width:300px;height:455px;transform:rotateY(-14deg) rotateX(5deg) translateZ(120px)"><img src="${img("progress.jpg")}" style="object-position:top"></div>
  </div>`),
});

// ---------- Phone with real mobile screenshot ----------
assets.push({
  name: "phone", w: 520, h: 820, html: base(520, 820, `<div class="stage" style="perspective:1600px">
   <div class="glow" style="left:90px;top:200px;width:340px;height:420px;--c:${T.pink};opacity:.35"></div>
   <div class="phone" style="left:120px;top:70px;width:290px;height:628px;transform:rotateY(-16deg) rotateX(5deg) rotateZ(1deg)"><img src="${img("mobile.jpg")}" style="object-position:top"></div>
  </div>`),
});

// ---------- Single tilted screens ----------
const tilt = (name, file, ry, opts = {}) => assets.push({
  name, w: 900, h: 640, html: base(900, 640, `<div class="stage" style="perspective:2200px">
   <div class="glow" style="left:180px;top:140px;width:540px;height:380px;--c:${opts.glow || T.violet};opacity:.38"></div>
   <div class="screen" style="left:70px;top:72px;width:760px;height:481px;transform:rotateY(${ry}deg) rotateX(5deg)">
     <img src="${img(file)}" style="object-position:${opts.pos || "center"}"></div></div>`),
});
tilt("tilt_create", "create.jpg", 14);
tilt("tilt_publish", "publish.jpg", -14, { glow: T.cyan });
tilt("tilt_earn", "earn.jpg", 14, { glow: T.pink });
tilt("tilt_ai", "ai.jpg", -14, { glow: T.cyan });
tilt("tilt_clips", "clips.jpg", -14);
tilt("tilt_edit", "edit.jpg", 12, { glow: T.pink });
tilt("tilt_schedule", "schedule.jpg", -12, { glow: T.cyan });

// Tall progress screen (portrait)
assets.push({
  name: "tilt_progress", w: 620, h: 860, html: base(620, 860, `<div class="stage" style="perspective:2000px">
   <div class="glow" style="left:120px;top:200px;width:380px;height:460px;--c:${T.cyan};opacity:.3"></div>
   <div class="screen" style="left:95px;top:60px;width:430px;height:652px;transform:rotateY(-12deg) rotateX(4deg)"><img src="${img("progress.jpg")}" style="object-position:top"></div>
  </div>`),
});

// ---------- Individual finished-clip frames (flat glass, for the anatomy slide) ----------
for (const k of [1, 2, 3]) assets.push({
  name: `frame${k}_glass`, w: 300, h: 500, html: base(300, 500, `
   <div class="phone" style="left:30px;top:30px;width:240px;height:427px"><img src="${img(`frame${k}.png`)}"></div>`),
});

// ---------- App icon (recreated from the product's own icon style) ----------
assets.push({
  name: "logo", w: 160, h: 160, html: base(160, 160, `
  <div style="position:absolute;left:10px;top:10px;width:140px;height:140px;border-radius:36px;
    background:linear-gradient(135deg,#FF5CA8 0%,#B44CFF 55%,${T.violet} 100%)">
    <svg viewBox="0 0 140 140" width="140" height="140"><path d="M54 40 L102 70 L54 100 Z" fill="#fff"/>
    <rect x="36" y="110" width="68" height="6" rx="3" fill="rgba(255,255,255,.75)"/></svg></div>`),
});

(async () => {
  const browser = await chromium.launch();
  for (const a of assets) {
    const page = await browser.newPage({ viewport: { width: a.w, height: a.h }, deviceScaleFactor: a.opaque ? 2 : 1.6 });
    await page.setContent(a.html, { waitUntil: "load" });
    await page.waitForTimeout(150);
    await page.screenshot(a.opaque ? { path: path.join(OUT, a.name + ".jpg"), type: "jpeg", quality: 92 } : { path: path.join(OUT, a.name + ".png"), omitBackground: true });
    await page.close();
    console.log("rendered", a.name);
  }
  if (process.argv.includes("--video")) {
    const dir = path.join(__dirname, "frames_tmp");
    fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir);
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1.5 });
    const N = 150; // 5 s at 30 fps, seamless loop
    for (let i = 0; i < N; i++) {
      await page.setContent(`<div style="position:fixed;inset:0;background:radial-gradient(ellipse at 60% 45%,#2a1a6b 0%,${T.bg0} 60%)"></div>` +
        base(1140, 800, heroBody(i / N)).replace("<body>", '<body style="position:absolute;left:70px;top:-40px">'), { waitUntil: "load" });
      await page.screenshot({ path: path.join(dir, `f${String(i).padStart(4, "0")}.png`) });
    }
    execSync(`ffmpeg -y -loglevel error -framerate 30 -i ${dir}/f%04d.png -c:v libx264 -pix_fmt yuv420p -crf 18 -movflags +faststart ${OUT}/hero_loop.mp4`);
    fs.rmSync(dir, { recursive: true, force: true });
    console.log("rendered hero_loop.mp4");
  }
  await browser.close();
})();
