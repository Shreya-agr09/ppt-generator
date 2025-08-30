# PPT Generator

A lightweight Python-based tool to generate PowerPoint presentations from Markdown or other text inputs, powered by FastAPI and templating systems.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [API Endpoints](#api-endpoints)
- [Templates](#templates)
- [Development](#development)
- [License](#license)

---

## Features

- Converts markdown or text content into PowerPoint slides.
- Offers a FastAPI-driven interface supporting file uploads and template selection.
- Utilizes Jinja2 templates and `python-pptx` for slide generation.
- Built with a clean templating system (`templates/`) and organized helper modules (`utils/`).
- API not present → works **only for Markdown files** (no AI structuring).  
- For **plain text input** → API key is **compulsory**.  
- For **Markdown files** → API key is **optional** (works even without AI)

---

## Installation

1. **Clone the repository**  
   ```bash
   git clone https://github.com/Shreya-agr09/ppt-generator.git
   cd ppt-generator```
2. **Set up virtual environment**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate```

3. **Install dependencies**
  ```bash
  pip install -r requirements.txt```

## Running locally
```bash
run uvicorn app:app --reload
on ngrock add your auth token and domain and run python app.py```

### How It Works  

- **Parsing Input**  
  - Supports plain text, Markdown, or uploaded files.  
  - Markdown headings → slide titles, subheadings/lists → bullet points.  
  - Plain text is split into logical sections for slides.  
  - If both text and file are given, the app merges content before parsing.  

- **Mapping to Slides**  
  - Each parsed block is mapped to a new slide or added as structured content.  
  - Ensures clean separation between title, body, and points.  

- **Applying Templates**  
  - Chosen PowerPoint template defines fonts, colors, and layout.  
  - Content fills predefined placeholders for titles, bullets, and images.  
  - Logos, backgrounds, and styles from the template ensure professional consistency.  

- **Result**  
  - Clear separation between **content (what to present)** and **template (how it looks)**.  
  - Generates polished, branded slides automatically.  
