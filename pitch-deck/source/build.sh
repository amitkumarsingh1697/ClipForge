#!/usr/bin/env bash
# Rebuild everything: 3D assets -> PPTX -> native motion -> validate -> PDF + slide previews.
set -euo pipefail
cd "$(dirname "$0")"
export NODE_PATH="$PWD/node_modules:$(npm root -g)"
: "${PPTX_SKILL:?set PPTX_SKILL to the pptx skill folder (for apply_theme.js, validate.py, soffice.py)}"
node assets.js ${VIDEO:+--video}
node build_deck.js
python3 motion.py
python3 "$PPTX_SKILL/scripts/office/validate.py" out/ClipForge_Client_Pitch_Deck.pptx
rm -rf out/render && mkdir -p out/render && cp out/ClipForge_Client_Pitch_Deck.pptx out/render/deck.pptx
(cd out/render && python3 "$PPTX_SKILL/scripts/office/soffice.py" --headless --convert-to pdf deck.pptx >/dev/null 2>&1 && pdftoppm -jpeg -r 80 deck.pdf s)

# Supporting documents
node build_pdfs.js
python3 merge_pdf.py
node build_script_docx.js && (cd out && zip -d ClipForge_Presenter_Script.docx '*/' >/dev/null 2>&1 || true)
echo "all deliverables built in out/"
