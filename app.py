import os, io, tempfile, shutil, re
from typing import List, Optional
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pptx import Presentation
from pptx.util import Pt, Inches
from PIL import Image
from utils.md_to_slides import SlideSpec

# --- App + UI ---
app = FastAPI(title="Markdown → PowerPoint")
templates = Jinja2Templates(directory="templates")

# Optional: serve /static if you add CSS/JS
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

BUILTIN_TEMPLATES = {
    "minimal": os.path.join("templates_pack", "minimal.pptx"),
    "corporate": os.path.join("templates_pack", "corporate.pptx"),
}

MAX_CONTENT_CHARS = 120_000
MAX_FILE_SIZE = 10 * 1024 * 1024

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "builtin_templates": BUILTIN_TEMPLATES.keys()
    })

# --- Utilities: PPT helpers ---
def add_slide(prs, title_text, body_lines, is_title_slide=False):
    """Add a slide with title and body content"""
    if is_title_slide:
        slide_layout = prs.slide_layouts[0]
    else:
        slide_layout = None
        for layout in prs.slide_layouts:
            for ph in layout.placeholders:
                if ph.placeholder_format.type == 2:
                    slide_layout = layout
                    break
            if slide_layout:
                break
        if not slide_layout:
            slide_layout = prs.slide_layouts[1]

    slide = prs.slides.add_slide(slide_layout)

    if slide.shapes.title:
        slide.shapes.title.text = title_text

    if not is_title_slide:
        content_placeholder = None
        for shape in slide.placeholders:
            if shape.is_placeholder and shape.placeholder_format.type == 2:
                content_placeholder = shape
                break

        if content_placeholder:
            tf = content_placeholder.text_frame
            tf.clear()
        else:
            textbox = slide.shapes.add_textbox(Inches(1), Inches(2.5), Inches(8), Inches(4))
            tf = textbox.text_frame

        for line in body_lines:
            p = tf.add_paragraph()
            if line.startswith("CODE:"):
                p.text = line.replace("CODE:", "")
                p.font.name = "Courier New"
                p.font.size = Pt(16)
            elif line.startswith("• "):
                p.text = line[2:]
                p.level = 0
                p.font.size = Pt(18)
            else:
                p.text = line
                p.font.size = Pt(18)

# --- Core conversion ---
def split_into_sentences(text: str) -> List[str]:
    """Naive sentence splitter for plain text"""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]
def markdown_to_slides(prs, text_input, max_chars_per_slide: int = 1000):
    """Convert markdown or plain text into slides with auto headlines."""
    text_input = text_input.replace("\r", "").replace("_x000D_", "")
    lines = [l for l in text_input.split("\n") if l.strip()]

    # Detect if markdown style headings exist
    has_md = any(line.strip().startswith("#") for line in lines)

    if not has_md:
        # --- Plain text mode ---
        text = " ".join(lines)
        sentences = split_into_sentences(text)

        chunk = []
        char_count = 0

        for i, sent in enumerate(sentences):
            # If adding this sentence exceeds char limit, flush current chunk
            if char_count + len(sent) > max_chars_per_slide and chunk:
                first = chunk[0]
                title = " ".join(first.split()[:8]) + ("..." if len(first.split()) > 8 else "")
                body = chunk[1:] if len(chunk) > 1 else []
                add_slide(prs, title, body, is_title_slide=False)

                # reset
                chunk = []
                char_count = 0

            chunk.append(sent)
            char_count += len(sent)

        # Add last chunk
        if chunk:
            first = chunk[0]
            title = " ".join(first.split()[:8]) + ("..." if len(first.split()) > 8 else "")
            body = chunk[1:] if len(chunk) > 1 else []
            add_slide(prs, title, body, is_title_slide=False)

        return prs

    # --- Markdown mode ---
    current_title = None
    current_body = []
    in_code_block = False
    is_title_slide = False

    for line in lines:
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            current_body.append("CODE:" + line)
            continue

        if line.startswith("# "):
            if current_title:
                add_slide(prs, current_title, current_body, is_title_slide)
                current_body = []
            current_title = line[2:].strip()
            is_title_slide = True

        elif line.startswith("## "):
            if current_title:
                add_slide(prs, current_title, current_body, is_title_slide)
            current_title = line[3:].strip()
            current_body = []
            is_title_slide = False

        elif line.startswith("- "):
            current_body.append("• " + line[2:].strip())

        else:
            current_body.append(line.strip())

    if current_title:
        add_slide(prs, current_title, current_body, is_title_slide)

    return prs


# --- API: Convert ---
@app.post("/api/convert")
async def convert(
    content: str = Form(None),
    markdown_file: UploadFile = File(None),
    template_file: UploadFile = File(None),
    template_id: str = Form(None),
):
    text_input = None

    if content and content.strip():
        text_input = content.strip()

    if not text_input and markdown_file is not None:
        raw = await markdown_file.read()
        try:
            text_input = raw.decode("utf-8")
        except:
            raise HTTPException(status_code=400, detail="Invalid file encoding. Please upload UTF-8 text/markdown.")

    if not text_input:
        raise HTTPException(status_code=400, detail="Provide content in text area or upload a markdown file")

    if template_file:
        tmp_template = tempfile.NamedTemporaryFile(delete=False, suffix=".pptx")
        with open(tmp_template.name, "wb") as buffer:
            shutil.copyfileobj(template_file.file, buffer)
        prs = Presentation(tmp_template.name)
    else:
        default_template = "templates/default.pptx"
        if os.path.exists(default_template):
            prs = Presentation(default_template)
        else:
            prs = Presentation()

    # ✅ Remove first blank slide
    if prs.slides:
        r_id = prs.slides._sldIdLst[0].rId
        prs.part.drop_rel(r_id)
        del prs.slides._sldIdLst[0]

    prs = markdown_to_slides(prs, text_input)

    output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pptx")
    prs.save(output_file.name)

    return FileResponse(
        output_file.name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename="slides.pptx"
    )

@app.get("/health")
def health():
    return {"ok": True}
