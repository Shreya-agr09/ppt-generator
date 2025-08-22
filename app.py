import os, io, tempfile, shutil, re, json, logging
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pptx import Presentation
from pptx.util import Pt, Inches
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from PIL import Image
import httpx
import asyncio
import google.generativeai as genai

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# LLM providers configuration
LLM_PROVIDERS = {
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-3.5-turbo"
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/messages",
        "model": "claude-3-sonnet-20240229",
        "version": "2023-06-01"
    },
    "gemini": {
        "model": "gemini-1.5-flash"
    }
}

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "builtin_templates": BUILTIN_TEMPLATES.keys(),
        "llm_providers": list(LLM_PROVIDERS.keys())
    })

# --- LLM Integration ---
async def call_llm(provider: str, api_key: str, prompt: str, guidance: str = "") -> str:
    """Call the selected LLM provider to structure the presentation"""
    if provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported LLM provider: {provider}")
    
    config = LLM_PROVIDERS[provider]
    
    # Create a comprehensive system prompt for better slide generation
    system_prompt = f"""You are a presentation structuring assistant. Analyze the given text and break it into appropriate slides for a presentation.

Follow this format for your response:
Slide 1: [Title]
- Key point 1
- Key point 2
- Key point 3

Slide 2: [Title]
- Key point 1
- Key point 2

Continue this pattern for all slides. Make sure each slide has a clear title and 3-5 bullet points maximum.

{guidance}"""
    
    if provider == "openai":
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Please structure this content into presentation slides:\n\n{prompt}"}
        ]
        
        data = {
            "model": config["model"],
            "messages": messages,
            "temperature": 0.3
        }
        
        try:
            timeout = httpx.Timeout(60.0, read=60.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(config["url"], json=data, headers=headers)
                response.raise_for_status()
                result = response.json()
                return result["choices"][0]["message"]["content"]
        
        except httpx.HTTPError as e:
            logger.error(f"OpenAI API error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error calling OpenAI API: {str(e)}")
    
    elif provider == "anthropic":
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": config["version"]
        }
        
        data = {
            "model": config["model"],
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": f"{system_prompt}\n\nPlease structure this content into presentation slides:\n\n{prompt}"}]
        }
        
        try:
            timeout = httpx.Timeout(60.0, read=60.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(config["url"], json=data, headers=headers)
                response.raise_for_status()
                result = response.json()
                return result["content"][0]["text"]
        
        except httpx.HTTPError as e:
            logger.error(f"Anthropic API error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error calling Anthropic API: {str(e)}")
    
    elif provider == "gemini":
        try:
            # Configure the Gemini API
            genai.configure(api_key=api_key)
            
            # Create the model with proper configuration
            generation_config = {
                "temperature": 0.3,
                "top_p": 0.8,
                "top_k": 40,
                "max_output_tokens": 4000,
            }
            
            # Safety settings to reduce blocking
            safety_settings = [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH", 
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                }
            ]
            
            model = genai.GenerativeModel(
                model_name=config["model"],
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            # Prepare the full prompt
            full_prompt = f"{system_prompt}\n\nPlease structure this content into presentation slides:\n\n{prompt}"
            
            # Add retry logic for Gemini API
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Generate content with timeout
                    response = model.generate_content(
                        full_prompt,
                        request_options={"timeout": 60}
                    )
                    
                    # Check if we have a valid response
                    if hasattr(response, 'text') and response.text and response.text.strip():
                        return response.text
                    elif hasattr(response, 'candidates') and response.candidates:
                        # Try to get text from candidates
                        candidate = response.candidates[0]
                        if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                            parts_text = ""
                            for part in candidate.content.parts:
                                if hasattr(part, 'text'):
                                    parts_text += part.text
                            if parts_text.strip():
                                return parts_text
                    
                    # If we get here, no valid content was generated
                    if attempt == max_retries - 1:
                        logger.warning("Gemini API returned empty response after all retries")
                        raise HTTPException(status_code=400, detail="Gemini API returned empty response - content may have been blocked by safety filters")
                    
                    # Wait before retry
                    await asyncio.sleep(2 ** attempt)
                    
                except Exception as retry_error:
                    if attempt == max_retries - 1:
                        raise retry_error
                    logger.warning(f"Gemini API attempt {attempt + 1} failed: {str(retry_error)}")
                    await asyncio.sleep(2 ** attempt)
        
        except HTTPException:
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Gemini API error: {error_msg}")
            
            # Provide more specific error messages
            if "API_KEY_INVALID" in error_msg or "invalid API key" in error_msg.lower():
                raise HTTPException(status_code=401, detail="Invalid Gemini API key")
            elif "quota" in error_msg.lower() or "limit" in error_msg.lower():
                raise HTTPException(status_code=429, detail="Gemini API quota exceeded")
            elif "safety" in error_msg.lower() or "blocked" in error_msg.lower():
                raise HTTPException(status_code=400, detail="Content blocked by Gemini safety filters")
            else:
                raise HTTPException(status_code=500, detail=f"Gemini API error: {error_msg}")

def parse_llm_response(response_text: str) -> List[Dict[str, Any]]:
    """Parse LLM response into structured slide data"""
    slides = []
    
    # If the response is empty, create a default slide
    if not response_text.strip():
        return [{
            "title": "Presentation",
            "content": [{"type": "paragraph", "text": "No content could be generated."}],
            "type": "content"
        }]
    
    # Try to extract slides using different patterns
    slide_patterns = [
        r'(?:Slide\s*\d+):\s*(.*?)(?=(?:Slide\s*\d+):|$)',
        r'#+\s*(.*?)(?=#+|$)',
        r'Title:\s*(.*?)(?=Content:|Title:|$)',
    ]
    
    content = None
    for pattern in slide_patterns:
        # Fixed: Changed re.DOTNAME to re.DOTALL
        slides_found = re.findall(pattern, response_text, re.DOTALL | re.IGNORECASE)
        if slides_found:
            content = slides_found
            break
    
    # If no specific pattern found, split by double newlines
    if not content:
        content = response_text.split('\n\n')
    
    for i, item in enumerate(content):
        if not item.strip():
            continue
            
        lines = item.strip().split('\n')
        title = f"Slide {i+1}"
        content_lines = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Extract title from the first meaningful line
            # Extract title from the first meaningful line
            # Extract title from the first meaningful line
            if title == f"Slide {i+1}" and len(line) > 3 and not line.startswith('-') and not line.startswith('*'):
                clean = line.strip()
                # Remove "Slide X:" or "Title:" prefixes if present
                clean = re.sub(r'^(Slide\s*\d+:?|Title:)\s*', '', clean, flags=re.IGNORECASE)
                title = clean
                # ✅ Do not add this to content
                continue


                
            # Handle bullet points
            if line.startswith('- ') or line.startswith('* '):
                content_lines.append({"type": "bullet", "text": line[2:].strip()})
            elif line.startswith('• '):
                content_lines.append({"type": "bullet", "text": line[2:].strip()})
            else:
                content_lines.append({"type": "paragraph", "text": line})
        
        # If we didn't find a better title, use the first content line
        # If we didn't find a better title, use the first content line
            if title == f"Slide {i+1}" and content_lines:
                clean = content_lines[0]["text"]
                clean = re.sub(r'^(Slide\s*\d+:?|Title:)\s*', '', clean, flags=re.IGNORECASE)
                title = clean[:50] + "..." if len(clean) > 50 else clean
                # ✅ Remove it from content (otherwise it repeats below title)
                content_lines = content_lines[1:]

        
        slides.append({
            "title": title,
            "content": content_lines,
            "type": "title" if i == 0 else "content"
        })
    
    # Ensure we have at least one slide
    if not slides:
        slides = [{
            "title": "Presentation",
            "content": [{"type": "paragraph", "text": response_text}],
            "type": "content"
        }]
    
    return slides

# --- Template Analysis ---
def analyze_template(prs: Presentation) -> Dict[str, Any]:
    """Analyze the template to extract styles, colors, and layouts"""
    template_info = {
        "title_style": None,
        "content_style": None,
        "colors": set(),
        "layouts": [],
        "images": []
    }
    
    # Analyze master slides for styles
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            layout_info = {
                "name": layout.name,
                "placeholders": []
            }
            
            # Find title placeholder first
            title_shape = None
            try:
                # Try to get title placeholder
                for shape in layout.placeholders:
                    if hasattr(shape, 'placeholder_format') and shape.placeholder_format.type == 1:  # Title placeholder
                        title_shape = shape
                        break
                # Fallback: check if layout has a title shape
                if not title_shape and hasattr(layout, 'shapes') and hasattr(layout.shapes, 'title'):
                    title_shape = layout.shapes.title
            except:
                pass
            
            for shape in layout.shapes:
                try:
                    if shape.has_text_frame:
                        text_frame = shape.text_frame
                        style_info = {
                            "font": None,
                            "size": None,
                            "color": None,
                            "alignment": None
                        }
                        
                        # Extract style information from paragraphs
                        if text_frame.paragraphs:
                            p = text_frame.paragraphs[0]
                            if p.runs:
                                run = p.runs[0]
                                try:
                                    style_info["font"] = run.font.name
                                except:
                                    pass
                                try:
                                    style_info["size"] = run.font.size
                                except:
                                    pass
                                try:
                                    if run.font.color and hasattr(run.font.color, 'rgb'):
                                        style_info["color"] = run.font.color.rgb
                                except:
                                    pass
                                try:
                                    style_info["alignment"] = p.alignment
                                except:
                                    pass
                        
                        # Determine if this is a title or content shape
                        is_title_shape = False
                        if title_shape and shape == title_shape:
                            is_title_shape = True
                        elif hasattr(shape, 'placeholder_format'):
                            # Check placeholder type
                            if shape.placeholder_format.type == 1:  # Title
                                is_title_shape = True
                        
                        if is_title_shape and not template_info["title_style"]:
                            template_info["title_style"] = style_info
                        elif not is_title_shape and not template_info["content_style"]:
                            template_info["content_style"] = style_info
                
                except Exception as e:
                    # Skip shapes that cause errors
                    logger.debug(f"Error analyzing shape: {str(e)}")
                    continue
                
                # Check for images in the template
                try:
                    if hasattr(shape, "image") and shape.image:
                        image_bytes = shape.image.blob
                        template_info["images"].append(image_bytes)
                except:
                    pass
            
            template_info["layouts"].append(layout_info)
    
    # Set default styles if none were found
    if not template_info["title_style"]:
        template_info["title_style"] = {
            "font": "Calibri",
            "size": Pt(44),
            "color": RGBColor(0, 0, 0),
            "alignment": PP_ALIGN.CENTER
        }
    
    if not template_info["content_style"]:
        template_info["content_style"] = {
            "font": "Calibri", 
            "size": Pt(18),
            "color": RGBColor(0, 0, 0),
            "alignment": PP_ALIGN.LEFT
        }
    
    return template_info

# --- Slide Creation with Template Styles ---
def apply_template_styles(shape, style_info):
    """Apply template styles to a shape"""
    if not style_info or not shape.has_text_frame:
        return
    
    text_frame = shape.text_frame
    for paragraph in text_frame.paragraphs:
        for run in paragraph.runs:
            try:
                if style_info.get("font"):
                    run.font.name = style_info["font"]
            except:
                pass
            try:
                if style_info.get("size"):
                    run.font.size = style_info["size"]
            except:
                pass
            try:
                if style_info.get("color"):
                    run.font.color.rgb = style_info["color"]
            except:
                pass
        try:
            if style_info.get("alignment"):
                paragraph.alignment = style_info["alignment"]
        except:
            pass

def create_slide_from_template(prs, slide_data, template_info, slide_index=0):
    """Create a slide using the template styles"""
    # Use appropriate layout based on slide index
    if slide_index == 0 and len(prs.slide_layouts) > 0:
        layout = prs.slide_layouts[0]  # Title slide
    elif len(prs.slide_layouts) > 1:
        layout = prs.slide_layouts[1]  # Content slide
    else:
        layout = prs.slide_layouts[0]
    
    slide = prs.slides.add_slide(layout)
    
    # Set title
    title_shape = None
    try:
        # Try different ways to get the title shape
        if hasattr(slide.shapes, 'title') and slide.shapes.title:
            title_shape = slide.shapes.title
        else:
            # Look for title placeholder
            for shape in slide.placeholders:
                if hasattr(shape, 'placeholder_format') and shape.placeholder_format.type == 1:  # Title
                    title_shape = shape
                    break
    except:
        pass
    
    if title_shape:
        title_shape.text = slide_data["title"]
        if template_info["title_style"]:
            apply_template_styles(title_shape, template_info["title_style"])
    
    # Add content
    content_placeholder = None
    try:
        # Look for content placeholder
        for shape in slide.placeholders:
            if shape != title_shape and shape.has_text_frame:
                content_placeholder = shape
                break
    except:
        pass
    
    if not content_placeholder:
        # Fallback: create a textbox
        try:
            content_placeholder = slide.shapes.add_textbox(
                Inches(1), Inches(1.5), Inches(8), Inches(5)
            )
        except:
            # If that fails, try to find any shape with text frame
            for shape in slide.shapes:
                if shape.has_text_frame and shape != title_shape:
                    content_placeholder = shape
                    break
    
    if content_placeholder:
        text_frame = content_placeholder.text_frame
        text_frame.clear()
        
        for i, item in enumerate(slide_data["content"]):
            if i == 0:
                # Use the first paragraph
                p = text_frame.paragraphs[0] if text_frame.paragraphs else text_frame.add_paragraph()
            else:
                p = text_frame.add_paragraph()
            
            if item["type"] == "bullet":
                p.text = "• " + item["text"]
                p.level = 0
            else:
                p.text = item["text"]
        
        if template_info["content_style"]:
            apply_template_styles(content_placeholder, template_info["content_style"])
    
    return slide

# --- Core Conversion ---
async def structured_markdown_to_slides(prs, text_input, guidance, llm_provider, api_key):
    """Use LLM to structure content and create slides with template styles"""
    # Call LLM to structure the content
    llm_response = await call_llm(llm_provider, api_key, text_input, guidance)
    
    # Parse the LLM response
    slides_data = parse_llm_response(llm_response)
    
    # Analyze the template for styling
    template_info = analyze_template(prs)
    
    # Create slides
    for i, slide_data in enumerate(slides_data):
        create_slide_from_template(prs, slide_data, template_info, i)
    
    return prs
# --- Utilities: PPT helpers ---
def add_slide(prs, title_text, body_lines, is_title_slide=False):
    """Add a slide with title and body content"""
    from pptx.util import Pt, Inches

    if is_title_slide:
        slide_layout = prs.slide_layouts[0]
    else:
        slide_layout = None
        for layout in prs.slide_layouts:
            for ph in layout.placeholders:
                if ph.placeholder_format.type == 2:  # Content placeholder
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

def split_into_sentences(text: str) -> list[str]:
    """Naive sentence splitter for plain text"""
    import re
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

        for sent in sentences:
            if char_count + len(sent) > max_chars_per_slide and chunk:
                first = chunk[0]
                title = " ".join(first.split()[:8]) + ("..." if len(first.split()) > 8 else "")
                body = chunk[1:] if len(chunk) > 1 else []
                add_slide(prs, title, body, is_title_slide=False)

                chunk = []
                char_count = 0

            chunk.append(sent)
            char_count += len(sent)

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
    guidance: str = Form(""),
    llm_provider: str = Form("openai"),
    api_key: str = Form(""),
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

    # Load template
    if template_file:
        tmp_template = tempfile.NamedTemporaryFile(delete=False, suffix=".pptx")
        with open(tmp_template.name, "wb") as buffer:
            shutil.copyfileobj(template_file.file, buffer)
        prs = Presentation(tmp_template.name)
    elif template_id and template_id in BUILTIN_TEMPLATES and os.path.exists(BUILTIN_TEMPLATES[template_id]):
        prs = Presentation(BUILTIN_TEMPLATES[template_id])
    else:
        default_template = "templates/default.pptx"
        prs = Presentation(default_template) if os.path.exists(default_template) else Presentation()

    # Remove first blank slide if present
    if prs.slides:
        r_id = prs.slides._sldIdLst[0].rId
        prs.part.drop_rel(r_id)
        del prs.slides._sldIdLst[0]

    # --- Decide how to process ---
    has_markdown = any(line.strip().startswith("#") for line in text_input.split("\n"))

    if has_markdown:
        if guidance.strip():  # Markdown + tone → use LLM
            if not api_key:
                raise HTTPException(status_code=400, detail="API key is required when tone/guidance is provided")
            prs = await structured_markdown_to_slides(prs, text_input, guidance, llm_provider, api_key)
        else:  # Markdown only → direct parsing
            prs = markdown_to_slides(prs, text_input)
    else:
        # Plain text always → LLM required
        if not api_key:
            raise HTTPException(status_code=400, detail="API key is required for plain text input")
        prs = await structured_markdown_to_slides(prs, text_input, guidance, llm_provider, api_key)

    # Save and return PPTX
    output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pptx")
    prs.save(output_file.name)

    return FileResponse(
        output_file.name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename="slides.pptx"
    )



@app.post("/api/test-gemini")
async def test_gemini(api_key: str = Form(...)):
    """Test if the Gemini API key is valid"""
    try:
        # Configure the Gemini API
        genai.configure(api_key=api_key)
        
        # Create the model with proper configuration
        generation_config = {
            "temperature": 0.1,
            "max_output_tokens": 100,
        }
        
        # Safety settings
        safety_settings = [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH", 
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_NONE"
            }
        ]
        
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        # Generate content with simple test message
        response = model.generate_content(
            "Hello, please respond with exactly 'API is working' if you can see this message.",
            request_options={"timeout": 30}
        )
        
        # Check response
        if hasattr(response, 'text') and response.text and response.text.strip():
            return {"status": "success", "response": response.text.strip()}
        elif hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                parts_text = ""
                for part in candidate.content.parts:
                    if hasattr(part, 'text'):
                        parts_text += part.text
                if parts_text.strip():
                    return {"status": "success", "response": parts_text.strip()}
        
        return {"status": "error", "message": "No response generated - possibly blocked by safety filters"}
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Gemini test error: {error_msg}")
        
        # Provide specific error messages
        if "API_KEY_INVALID" in error_msg or "invalid API key" in error_msg.lower():
            return {"status": "error", "message": "Invalid API key"}
        elif "quota" in error_msg.lower() or "limit" in error_msg.lower():
            return {"status": "error", "message": "API quota exceeded"}
        else:
            return {"status": "error", "message": f"API error: {error_msg}"}

@app.get("/health")
def health():
    return {"ok": True}