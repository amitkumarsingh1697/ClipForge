"""Inject native PowerPoint motion into the pptxgenjs deck.

Reads out/ClipForge_Client_Pitch_Deck.raw.pptx + out/motion.json (written by build_deck.js) and writes
out/ClipForge_Client_Pitch_Deck.pptx with:
  * slide transitions: Morph (PowerPoint 2019+/365, falls back to Fade elsewhere) or smooth Fade
  * entrance animations that auto-play in sequence when the slide appears (no clicks needed):
      fade  - Fade                         (presetID 10)
      rise  - Float In: fade + 0.04 rise   (presetID 42), ease-out
      zoom  - Zoom: fade + scale 85%->100% (presetID 53), ease-out
      wipe  - Wipe from left               (presetID 22)
Placeholders are renamed "Kicker"/"Title" so they are easy to find in the Selection Pane.
"""
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "out" / "ClipForge_Client_Pitch_Deck.raw.pptx"
OUT = HERE / "out" / "ClipForge_Client_Pitch_Deck.pptx"
MOTION = json.loads((HERE / "out" / "motion.json").read_text())

MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
P14 = 'xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main"'
P159 = 'xmlns:p159="http://schemas.microsoft.com/office/powerpoint/2015/09/main"'


def transition_xml(kind: str) -> str:
    if kind == "morph":
        return (f'<mc:AlternateContent {MC}><mc:Choice {P159} Requires="p159">'
                f'<p:transition {P14} spd="slow" p14:dur="1600"><p159:morph option="byObject"/></p:transition>'
                '</mc:Choice><mc:Fallback><p:transition spd="slow"><p:fade/></p:transition></mc:Fallback></mc:AlternateContent>')
    return (f'<mc:AlternateContent {MC}><mc:Choice {P14} Requires="p14">'
            '<p:transition spd="med" p14:dur="800"><p:fade/></p:transition>'
            '</mc:Choice><mc:Fallback><p:transition spd="med"><p:fade/></p:transition></mc:Fallback></mc:AlternateContent>')


class Ids:
    def __init__(self, start=100000):
        self.n = start

    def __call__(self):
        self.n += 1
        return self.n


def effect_xml(ids: Ids, spid: int, effect: str, delay: int, dur: int) -> str:
    preset = {"fade": (10, 0), "rise": (42, 0), "zoom": (53, 16), "wipe": (22, 8)}[effect]
    tgt = f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>'
    beh = [f'<p:set><p:cBhvr><p:cTn id="{ids()}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>{tgt}'
           '<p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set>']
    if effect == "wipe":
        beh.append(f'<p:animEffect transition="in" filter="wipe(left)"><p:cBhvr><p:cTn id="{ids()}" dur="{dur}"/>{tgt}</p:cBhvr></p:animEffect>')
    else:
        beh.append(f'<p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="{ids()}" dur="{dur}"/>{tgt}</p:cBhvr></p:animEffect>')

    def anim(attr, frm, to):
        return (f'<p:anim calcmode="lin" valueType="num"><p:cBhvr><p:cTn id="{ids()}" dur="{dur}" decel="100000" fill="hold"/>{tgt}'
                f'<p:attrNameLst><p:attrName>{attr}</p:attrName></p:attrNameLst></p:cBhvr><p:tavLst>'
                f'<p:tav tm="0"><p:val><p:strVal val="{frm}"/></p:val></p:tav>'
                f'<p:tav tm="100000"><p:val><p:strVal val="{to}"/></p:val></p:tav></p:tavLst></p:anim>')
    if effect == "rise":
        beh.append(anim("ppt_y", "#ppt_y+0.04", "#ppt_y"))
    if effect == "zoom":
        beh.append(anim("ppt_w", "#ppt_w*0.85", "#ppt_w"))
        beh.append(anim("ppt_h", "#ppt_h*0.85", "#ppt_h"))
    return (f'<p:par><p:cTn id="{ids()}" presetID="{preset[0]}" presetClass="entr" presetSubtype="{preset[1]}" fill="hold" grpId="0" nodeType="withEffect">'
            f'<p:stCondLst><p:cond delay="{delay}"/></p:stCondLst><p:childTnLst>{"".join(beh)}</p:childTnLst></p:cTn></p:par>')


def timing_xml(targets) -> str:
    """targets: list of (spid, kind, effect, delay, dur). All effects start automatically on slide entry."""
    if not targets:
        return ""
    ids = Ids(4)
    effects = "".join(effect_xml(ids, *t[0:1], *t[2:]) for t in targets)
    bld = []
    for spid, kind, *_ in targets:
        if kind == "sp":
            bld.append(f'<p:bldP spid="{spid}" grpId="0" animBg="1"/>')
        elif kind == "graphicFrame":
            bld.append(f'<p:bldGraphic spid="{spid}" grpId="0"><p:bldAsOne/></p:bldGraphic>')
    bld_xml = f'<p:bldLst>{"".join(dict.fromkeys(bld))}</p:bldLst>' if bld else ""
    return ('<p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
            '<p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
            '<p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/><p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst><p:childTnLst>'
            f'<p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>{effects}</p:childTnLst></p:cTn></p:par>'
            '</p:childTnLst></p:cTn></p:par>'
            '</p:childTnLst></p:cTn><p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
            '<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>'
            f'</p:childTnLst></p:cTn></p:par></p:tnLst>{bld_xml}</p:timing>')


def process(xml: str, spec: dict, slide_no: int) -> str:
    # Rename the kicker/title placeholders.
    def ph_rename(m):
        new = {"body": "Kicker", "title": "Title"}[m.group(3)]
        return f'<p:cNvPr id="{m.group(1)}" name="{new}">{m.group(2)}'
    xml = re.sub(r'<p:cNvPr id="(\d+)" name="Text 1">(</p:cNvPr><p:cNvSpPr/><p:nvPr><p:ph\s+idx="\d+"\s+type="(body|title)")', ph_rename, xml)
    # name -> (id, element kind)
    names = {}
    for m in re.finditer(r'<p:(sp|pic|graphicFrame|cxnSp)>\s*<p:nv\w+>\s*<p:cNvPr id="(\d+)" name="([^"]*)"', xml):
        names.setdefault(m.group(3), (int(m.group(2)), m.group(1)))
    targets, missing = [], []
    for a in spec["anims"]:
        key = {"kicker": "Kicker", "title": "Title"}.get(a["name"], a["name"])
        if key not in names:
            missing.append(key)
            continue
        spid, kind = names[key]
        targets.append((spid, kind, a["effect"], a["delay"], a["dur"]))
    if missing:
        print(f"slide {slide_no}: no shape for {missing}", file=sys.stderr)
    assert "<p:timing" not in xml and "<p:transition" not in xml
    inject = transition_xml(spec["transition"]) + timing_xml(targets)
    xml = xml.replace("</p:clrMapOvr>", "</p:clrMapOvr>" + inject, 1) if "</p:clrMapOvr>" in xml else xml.replace("</p:cSld>", "</p:cSld>" + inject, 1)
    return xml, len(targets)


def main():
    tmp = OUT.with_suffix(".tmp")
    total = 0
    with zipfile.ZipFile(RAW) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", item.filename)
            if m:
                n = int(m.group(1))
                xml, k = process(data.decode("utf8"), MOTION[n - 1], n)
                total += k
                data = xml.encode("utf8")
            zout.writestr(item, data)
    shutil.move(tmp, OUT)
    print(f"{OUT.name}: {len(MOTION)} slides, {total} animated objects, "
          f"{sum(1 for s in MOTION if s['transition'] == 'morph')} Morph transitions")


if __name__ == "__main__":
    main()
