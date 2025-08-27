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
run uvicorn app:app --reload
on ngrock add your auth token and domain and run python app.py
