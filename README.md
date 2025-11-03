# Notion Inline Math Renderer

This script automatically converts `$...$` math expressions in a Notion page into native Notion equations using the Notion API.

## Features
- Detects inline LaTeX (`$h_2(n) \ge h_1(n)$`)
- Converts to real Notion-rendered math equations
- Skips code and non-text blocks safely

## Setup
1. Create a Notion Integration (https://www.notion.so/my-integrations)
2. Share your page with that integration
3. Create a `.env` file:

NOTION_TOKEN=secret_xxx
NOTION_PAGE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

4. Install dependencies:
```bash
    pip install -r requirements.txt
```
5. Run:
    python notion_script.py
