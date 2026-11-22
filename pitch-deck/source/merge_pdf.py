"""Deck PDF = LibreOffice export of the PPTX + the 'About this PDF edition' page."""
from pathlib import Path
from pypdf import PdfWriter
out = Path(__file__).parent / "out"
w = PdfWriter()
w.append(str(out / "render" / "deck.pdf"))
w.append(str(out / "about_page.pdf"))
w.add_metadata({"/Title": "ClipForge Client Pitch Deck", "/Author": "ClipForge"})
w.write(str(out / "ClipForge_Client_Pitch_Deck.pdf"))
print("deck pdf pages:", len(w.pages))
