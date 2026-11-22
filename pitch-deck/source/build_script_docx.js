// Presenter script (.docx) from script.json + slide thumbnails in out/render/s-XX.jpg
const fs = require("fs");
const path = require("path");
const d = require("docx");
const { Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  HeadingLevel, AlignmentType, LevelFormat, PageBreak, Footer, PageNumber, TableLayoutType } = d;

const S = JSON.parse(fs.readFileSync(path.join(__dirname, "script.json"), "utf8"));
const R = path.join(__dirname, "out", "render");
const FONT = "Arial", VIOLET = "5B3FD9", MUTED = "5E6390";
const ACTS = { 1: "Act 1 · The opportunity", 6: "Act 2 · Our solution", 11: "Act 3 · User transformation", 15: "Act 4 · Business value", 18: "Act 5 · Technology, execution & trust", 23: "Act 6 · The next step" };

const p = (text, o = {}) => new Paragraph({ spacing: { after: 100 }, ...o.para, children: [new TextRun({ text, font: FONT, size: 22, ...o.run })] });
const label = (t) => new TextRun({ text: t + "  ", bold: true, color: VIOLET, font: FONT, size: 20 });
const W = 9360; // 6.5" content width
const cellBorder = { style: BorderStyle.SINGLE, size: 4, color: "D9DAE8" };
const borders = { top: cellBorder, bottom: cellBorder, left: cellBorder, right: cellBorder };

function metaTable(sc) {
  const row = (k, v) => new TableRow({ children: [
    new TableCell({ width: { size: 2000, type: WidthType.DXA }, borders, shading: { type: ShadingType.CLEAR, color: "auto", fill: "EEEBFF" },
      margins: { top: 80, bottom: 80, left: 120, right: 120 }, children: [new Paragraph({ children: [new TextRun({ text: k, bold: true, font: FONT, size: 20, color: VIOLET })] })] }),
    new TableCell({ width: { size: W - 2000, type: WidthType.DXA }, borders, margins: { top: 80, bottom: 80, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: v, font: FONT, size: 20 })] })] }),
  ] });
  return new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: [2000, W - 2000], layout: TableLayoutType.FIXED,
    rows: [row("Objective", sc.objective), row("Key message", sc.key), row("Duration", sc.duration)] });
}

const children = [
  new Paragraph({ heading: HeadingLevel.TITLE, children: [new TextRun({ text: "ClipForge Client Pitch Deck", font: FONT })] }),
  p("Presenter script", { run: { size: 28, color: MUTED } }),
  p("Use this script to deliver the deck with confidence. For every slide it gives the objective, the one message the audience should remember, presenter notes, talking points, the line that carries you to the next slide, and a recommended duration. Do not read the slides aloud: the slides carry the visuals and the short claims; you carry the story.", { para: { spacing: { before: 200, after: 120 } } }),
  new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: "Before you present", font: FONT })] }),
  ...[
    "Total running time is about 35 minutes for 24 slides. For a 20-minute slot, skip slides 3, 10, 14 and 20 (keep them for questions).",
    "Present from PowerPoint desktop (Microsoft 365 or 2019+) to get the Morph transitions and the auto-playing builds. Every slide builds itself: press the arrow key once per slide.",
    "Fill in the contact placeholders on slide 23 before sending or presenting the deck.",
    "Every number in the deck is an illustrative estimate and is labelled as such. Say so out loud; it builds trust.",
    "Have the live ClipForge app open in a browser tab, in case the client asks to see it working.",
    "If the client is a podcaster or brand rather than a streamer, change the CTA headline on slide 23 to match (for example, \"Let's clip your next episode together\").",
  ].map((t) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: t, font: FONT, size: 22 })] })),
];

S.forEach((sc, i) => {
  const n = i + 1;
  children.push(new Paragraph({ children: [new PageBreak()] }));
  if (ACTS[n]) children.push(p(ACTS[n].toUpperCase(), { run: { size: 18, bold: true, color: "0E9FB8" }, para: { spacing: { after: 60 } } }));
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text: `Slide ${n}. ${sc.title}`, font: FONT })] }));
  const thumb = path.join(R, `s-${String(n).padStart(2, "0")}.jpg`);
  if (fs.existsSync(thumb)) children.push(new Paragraph({ spacing: { after: 160 }, children: [new ImageRun({ type: "jpg", data: fs.readFileSync(thumb),
    transformation: { width: 384, height: 216 }, altText: { title: `Slide ${n}`, description: sc.title, name: `slide${n}` } })] }));
  children.push(metaTable(sc));
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: "Presenter notes", font: FONT })] }));
  children.push(p(sc.notes));
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: "Talking points", font: FONT })] }));
  sc.points.forEach((t) => children.push(new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: t, font: FONT, size: 22 })] })));
  children.push(new Paragraph({ spacing: { before: 200 }, children: [label("Transition"), new TextRun({ text: sc.transition, italics: true, font: FONT, size: 22 })] }));
});

const doc = new Document({
  creator: "ClipForge", title: "ClipForge Presenter Script",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Title", name: "Title", basedOn: "Normal", next: "Normal", run: { font: FONT, size: 52, bold: true, color: "15132F" }, paragraph: { spacing: { before: 0, after: 60 }, keepNext: true } },
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 32, bold: true, color: "15132F" }, paragraph: { spacing: { before: 0, after: 160 }, keepNext: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 24, bold: true, color: VIOLET }, paragraph: { spacing: { before: 240, after: 100 }, keepNext: true, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [
      new TextRun({ text: "ClipForge presenter script  ·  page ", font: FONT, size: 16, color: MUTED }), new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, color: MUTED })] })] }) },
    children,
  }],
});
Packer.toBuffer(doc).then((b) => { fs.writeFileSync(path.join(__dirname, "out", "ClipForge_Presenter_Script.docx"), b); console.log("docx written"); });
