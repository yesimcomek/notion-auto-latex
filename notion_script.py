import os
import time
import re
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
PAGE_ID = os.getenv("NOTION_PAGE_ID")

BASE_URL = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# --- Simple unicode → LaTeX normalizer (extend as you like)
UNICODE_TO_LATEX = {
    "≥": r"\geq",
    "≤": r"\leq",
    "≠": r"\ne",
    "±": r"\pm",
    "×": r"\times",
    "÷": r"\div",
    "√": r"\sqrt{}",
    "→": r"\to",
    "⇒": r"\Rightarrow",
    "⇔": r"\Leftrightarrow",
    "∞": r"\infty",
    "≈": r"\approx",
    "∑": r"\sum",
    "∫": r"\int",
    "∈": r"\in",
    "∉": r"\notin",
    "∧": r"\land",
    "∨": r"\lor",
    "⋅": r"\cdot",
    "•": r"\cdot",  # sometimes used in math
}

def normalize_latex(expr: str) -> str:
    # Replace common unicode symbols with LaTeX commands
    for u, latex in UNICODE_TO_LATEX.items():
        expr = expr.replace(u, latex)
    return expr

# Blocks whose text we can safely rewrite (add more as needed)
TEXTUAL_BLOCK_TYPES = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "toggle",
    "quote",
    "callout",
}

# Blocks we should SKIP (already math/code or non-text)
SKIP_BLOCK_TYPES = {
    "equation",
    "code",
    "bookmark",
    "image",
    "video",
    "file",
    "pdf",
    "divider",
    "table",
    "table_row",
    "column_list",
    "column",
    "synced_block",
    "link_to_page",
    "child_database",
    "child_page",
    "breadcrumb",
    "table_of_contents", # Does not support rich_text updates
}

MATH_PATTERN = re.compile(r"\$(.+?)\$", flags=re.DOTALL)  # non-greedy match between $...$

def fetch_block_children(block_id: str):
    url = f"{BASE_URL}/blocks/{block_id}/children"
    results = []
    start_cursor = None

    while True:
        params = {}
        if start_cursor:
            params["start_cursor"] = start_cursor
        res = requests.get(url, headers=HEADERS, params=params)
        res.raise_for_status()
        data = res.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return results

def update_block_rich_text(block_id: str, block_type: str, rich_text):
    """
    Update a textual block's rich_text array in place.
    """
    url = f"{BASE_URL}/blocks/{block_id}"
    payload = {
        block_type: {"rich_text": rich_text}
    }
    res = requests.patch(url, headers=HEADERS, json=payload)
    if res.status_code == 429:  # rate limit
        retry = int(res.headers.get("Retry-After", "1"))
        time.sleep(retry)
        res = requests.patch(url, headers=HEADERS, json=payload)
    res.raise_for_status()
    return res.json()

def block_has_math_dollars(block) -> bool:
    """
    Checks if any plain text segment inside block contains $...$.
    """
    bt = block["type"]
    if bt not in TEXTUAL_BLOCK_TYPES:
        return False
    rt = block[bt].get("rich_text", [])
    for item in rt:
        if item["type"] == "text":
            content = item["text"]["content"]
            if "$" in content and MATH_PATTERN.search(content):
                return True
        # If it’s already an equation rich_text, we skip rewriting
    return False

def build_rich_text_from_text_with_math(text: str):
    """
    Take a plain text string that may have multiple $...$ regions and
    return a Notion rich_text array mixing 'text' + 'equation' items.
    """
    rich = []
    idx = 0
    for m in MATH_PATTERN.finditer(text):
        start, end = m.span()
        # leading plain text
        if start > idx:
            leading = text[idx:start]
            if leading:
                rich.append({
                    "type": "text",
                    "text": {"content": leading}
                })
        expr = m.group(1).strip()
        expr = normalize_latex(expr)
        # inline equation element (no annotations field for equations)
        rich.append({
            "type": "equation",
            "equation": {"expression": expr}
        })
        idx = end
    # trailing plain text
    if idx < len(text):
        trailing = text[idx:]
        if trailing:
            rich.append({
                "type": "text",
                "text": {"content": trailing}
            })

    # Edge case: if there were NO matches (shouldn't happen), return as plain text
    if not rich:
        return [{
            "type": "text",
            "text": {"content": text}
        }]

    return rich

def rewrite_block_inline_math(block):
    """
    For a paragraph/heading/etc block:
     - read its rich_text
     - for any 'text' piece containing $...$, split into text + equation items
     - keep existing annotations/links for plain text where possible (we’ll drop them on equation chunks)
    """
    btype = block["type"]
    old_rt = block[btype].get("rich_text", [])
    
    # [FIX] Flatten the entire rich_text array into a single string.
    # This handles cases where a paragraph is composed of multiple rich_text objects
    # with different styling, which was causing the 400 error.
    full_text = "".join(item.get("plain_text", "") for item in old_rt)

    # If the flattened text contains math, rebuild the entire rich_text array.
    changed = "$" in full_text and MATH_PATTERN.search(full_text)
    if changed:
        new_rt = build_rich_text_from_text_with_math(full_text)
        update_block_rich_text(block["id"], btype, new_rt)
        
    return changed

def walk_and_fix(block_id: str, depth=0):
    """
    Recursively traverse all child blocks from the given block id,
    rewriting inline $...$ into equation rich_text where found.
    """
    children = fetch_block_children(block_id)
    total_changed = 0

    for b in children:
        btype = b["type"]

        # Skip non-textual types we don’t want to edit
        if btype in SKIP_BLOCK_TYPES:
            # still descend into containers (like toggles, columns)
            if b.get("has_children"):
                total_changed += walk_and_fix(b["id"], depth + 1)
            continue

        # Try to rewrite math in textual blocks
        try:
            if block_has_math_dollars(b):
                if rewrite_block_inline_math(b):
                    print(f"[INFO] Successfully updated block {b['id']}")
                    total_changed += 1
        except requests.HTTPError as e:
            print(f"[WARN] Failed to update block {b['id']} of type '{btype}'. Error: {e}")
            print(f"[DEBUG] Block content: {b}")

        # Recurse if there are children
        if b.get("has_children"):
            total_changed += walk_and_fix(b["id"], depth + 1)

        # be gentle with API rate limits
        time.sleep(0.05)

    return total_changed

def main():
    # The page itself is also a block; we traverse its children
    print("Scanning and fixing inline LaTeX…")
    changed = walk_and_fix(PAGE_ID)
    print(f"Done. Updated {changed} block(s).")

if __name__ == "__main__":
    if not NOTION_TOKEN or not PAGE_ID:
        print("Please set NOTION_TOKEN and NOTION_PAGE_ID in your environment or .env file.")
        raise SystemExit(1)
    main()