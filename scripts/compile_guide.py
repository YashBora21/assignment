# scripts/compile_guide.py
"""
Compiler script that aggregates all documentation sections into:
1. docs/CODEBASE_GUIDE.md
2. docs/CODEBASE_GUIDE.html (Self-contained, responsive, light/dark mode, live search, copy buttons, terracotta theme)
"""

import os
import re
import html
import markdown

from docs_sections.part1_overview_and_flow import SECTION_1, SECTION_2
from docs_sections.part2_stack_data_storage import SECTION_3, SECTION_4
from docs_sections.part3_component_deep_dives import SECTION_5
from docs_sections.part4_matching_accuracy import SECTION_6, SECTION_7
from docs_sections.part5_evaluator_qa import SECTION_8
from docs_sections.part6_demo_cheat_glossary import (
    SECTION_9,
    SECTION_10,
    SECTION_11,
    SECTION_12,
    SECTION_VERIFICATION,
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
MD_PATH = os.path.join(OUTPUT_DIR, "CODEBASE_GUIDE.md")
HTML_PATH = os.path.join(OUTPUT_DIR, "CODEBASE_GUIDE.html")

def build_full_markdown():
    sections = [
        SECTION_1,
        SECTION_2,
        SECTION_3,
        SECTION_4,
        SECTION_5,
        SECTION_6,
        SECTION_7,
        SECTION_8,
        SECTION_9,
        SECTION_10,
        SECTION_11,
        SECTION_12,
        SECTION_VERIFICATION,
    ]
    return "\n\n".join(s.strip() for s in sections) + "\n"

def transform_qa_to_accordions(md_content):
    """
    Transforms markdown Q&A items:
    1. **Question?**
       *Answer*: text
    Into interactive <details class="qa-item"><summary>...</summary><div class="qa-body">...</div></details>
    """
    pattern = re.compile(
        r'(?m)^(\d+)\.\s+\*\*([^\n]+?)\*\*\s*\n\s*\*Answer\*:\s*(.+?)(?=(?:\n\d+\.\s+\*\*|\n### |\n## |\Z))',
        re.DOTALL
    )

    def repl(m):
        q_num = m.group(1)
        q_text = m.group(2).strip()
        ans_text = m.group(3).strip()
        # convert internal markdown formatting in answer
        return (
            f'\n<details class="qa-item">\n'
            f'  <summary><span class="q-badge">Q{q_num}</span> <strong>{q_text}</strong></summary>\n'
            f'  <div class="qa-body">\n'
            f'    <p><strong>Answer:</strong> {ans_text}</p>\n'
            f'  </div>\n'
            f'</details>\n'
        )

    return pattern.sub(repl, md_content)

def transform_mermaid_fallback(md_content):
    """
    Replaces ```mermaid flowchart with an inline styled SVG / diagram block
    so that it renders cleanly without any external CDN.
    """
    svg_diagram = """
<div class="diagram-box">
  <div class="diagram-title"><strong>Live Presentation Fallback Protocol</strong></div>
  <svg viewBox="0 0 800 240" class="flow-svg" xmlns="http://www.w3.org/2000/svg">
    <style>
      .node-rect { fill: var(--card-bg); stroke: var(--accent); stroke-width: 2; rx: 8; }
      .decision-rect { fill: var(--accent-light); stroke: var(--accent); stroke-width: 2; rx: 6; }
      .sol-rect { fill: var(--bg); stroke: var(--border); stroke-width: 1.5; rx: 6; }
      .lbl { font-family: system-ui, -apple-system, sans-serif; font-size: 13px; fill: var(--text); }
      .lbl-bold { font-weight: 600; fill: var(--accent); }
      .arrow { stroke: var(--accent); stroke-width: 2; marker-end: url(#arrowhead); fill: none; }
    </style>
    <defs>
      <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
        <polygon points="0 0, 8 3, 0 6" fill="var(--accent)" />
      </marker>
    </defs>
    <!-- Root -->
    <rect x="20" y="85" width="130" height="50" class="node-rect"/>
    <text x="85" y="115" text-anchor="middle" class="lbl lbl-bold">Potential Glitch</text>

    <!-- Arrows from root -->
    <path d="M 150 110 L 210 50" class="arrow"/>
    <path d="M 150 110 L 210 110" class="arrow"/>
    <path d="M 150 110 L 210 170" class="arrow"/>

    <!-- Decisions -->
    <rect x="210" y="25" width="180" height="50" class="decision-rect"/>
    <text x="300" y="47" text-anchor="middle" class="lbl">LLM Rate Limit (429)?</text>
    <text x="300" y="63" text-anchor="middle" class="lbl" style="font-size:11px; opacity:0.8;">or API Timeout</text>

    <rect x="210" y="85" width="180" height="50" class="decision-rect"/>
    <text x="300" y="107" text-anchor="middle" class="lbl">Qdrant / Retrieval Slow?</text>
    <text x="300" y="123" text-anchor="middle" class="lbl" style="font-size:11px; opacity:0.8;">CPU Embedding Mode</text>

    <rect x="210" y="145" width="180" height="50" class="decision-rect"/>
    <text x="300" y="167" text-anchor="middle" class="lbl">PDF Upload Timeout?</text>
    <text x="300" y="183" text-anchor="middle" class="lbl" style="font-size:11px; opacity:0.8;">Heavy Scanned Document</text>

    <!-- Arrows to Solutions -->
    <path d="M 390 50 L 450 50" class="arrow"/>
    <path d="M 390 110 L 450 110" class="arrow"/>
    <path d="M 390 170 L 450 170" class="arrow"/>

    <!-- Solutions -->
    <rect x="450" y="25" width="320" height="50" class="sol-rect"/>
    <text x="460" y="47" class="lbl">Switch to cached JSON run in <code>outputs/</code></text>
    <text x="460" y="63" class="lbl" style="font-size:11px; opacity:0.8;">Explain 15-call safety cap & deterministic rules</text>

    <rect x="450" y="85" width="320" height="50" class="sol-rect"/>
    <text x="460" y="107" class="lbl">Open live Qdrant dashboard at <code>:6333</code></text>
    <text x="460" y="123" class="lbl" style="font-size:11px; opacity:0.8;">Explain 36,907 vectors across 3 collections</text>

    <rect x="450" y="145" width="320" height="50" class="sol-rect"/>
    <text x="460" y="167" class="lbl">Switch to pre-loaded tender in UI dropdown</text>
    <text x="460" y="183" class="lbl" style="font-size:11px; opacity:0.8;">Explain multi-tier OCR fallback hierarchy</text>
  </svg>
</div>
"""
    return re.sub(r'```mermaid\s+flowchart TD[\s\S]*?```', svg_diagram, md_content)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Tender Matching & Extraction - Complete Codebase & Defense Guide</title>
  <style>
    :root {
      --bg: #fffaf7;
      --text: #1a1a1e;
      --text-muted: #57534e;
      --accent: #d96b32;
      --accent-hover: #b8521d;
      --accent-light: #fef0e7;
      --accent-border: #f2c7ae;
      --card-bg: #ffffff;
      --border: #ebd5c8;
      --sidebar-bg: #fff5ee;
      --sidebar-border: #ebd5c8;
      --code-bg: #f7eee7;
      --code-text: #8c3b10;
      --pre-bg: #212124;
      --pre-text: #f3f4f6;
      --tag-full: #10b981;
      --tag-partial: #f59e0b;
      --tag-none: #ef4444;
      --tag-noev: #6b7280;
      --shadow-sm: 0 1px 3px rgba(0,0,0,0.06);
      --shadow-md: 0 4px 12px rgba(217,107,50,0.08);
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
    }

    [data-theme="dark"] {
      --bg: #141416;
      --text: #f1f1f4;
      --text-muted: #a1a1aa;
      --accent: #e57e45;
      --accent-hover: #f09562;
      --accent-light: #2c1e17;
      --accent-border: #4d2e1f;
      --card-bg: #1e1e23;
      --border: #332d29;
      --sidebar-bg: #19191d;
      --sidebar-border: #2d2724;
      --code-bg: #2d2420;
      --code-text: #fbb98d;
      --pre-bg: #0f0f11;
      --pre-text: #e5e7eb;
      --shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
      --shadow-md: 0 4px 12px rgba(0,0,0,0.4);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: var(--font);
      line-height: 1.65;
      display: flex;
      min-height: 100vh;
      font-size: 15px;
      transition: background-color 0.2s ease, color 0.2s ease;
    }

    /* Sidebar Navigation */
    aside.sidebar {
      width: 320px;
      min-width: 320px;
      background-color: var(--sidebar-bg);
      border-right: 1px solid var(--sidebar-border);
      position: sticky;
      top: 0;
      height: 100vh;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      z-index: 100;
    }

    .sidebar-header {
      padding: 1.25rem 1.25rem 0.75rem;
      border-bottom: 1px solid var(--sidebar-border);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.75rem;
    }

    .brand-logo {
      width: 28px;
      height: 28px;
      border-radius: 6px;
      background: var(--accent);
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-weight: bold;
      font-size: 16px;
    }

    .brand-title {
      font-weight: 700;
      font-size: 1.05rem;
      color: var(--accent);
      letter-spacing: -0.02em;
    }

    .theme-toggle-btn {
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.35rem 0.65rem;
      border-radius: 6px;
      cursor: pointer;
      font-size: 0.8rem;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      margin-top: 0.25rem;
      transition: all 0.15s ease;
    }
    .theme-toggle-btn:hover {
      border-color: var(--accent);
      color: var(--accent);
    }

    .search-box {
      margin-top: 0.75rem;
      position: relative;
    }

    .search-input {
      width: 100%;
      padding: 0.5rem 0.75rem;
      font-size: 0.85rem;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      outline: none;
      transition: border-color 0.15s ease;
    }
    .search-input:focus {
      border-color: var(--accent);
    }

    nav.toc {
      padding: 1rem 0.75rem;
      flex: 1;
    }

    nav.toc ul {
      list-style: none;
    }

    nav.toc li {
      margin-bottom: 0.25rem;
    }

    nav.toc a {
      display: block;
      padding: 0.4rem 0.65rem;
      color: var(--text-muted);
      text-decoration: none;
      font-size: 0.86rem;
      border-radius: 6px;
      transition: all 0.15s ease;
      line-height: 1.35;
    }

    nav.toc a:hover, nav.toc a.active {
      color: var(--accent);
      background: var(--accent-light);
      font-weight: 600;
    }

    /* Main Content Area */
    main.content {
      flex: 1;
      max-width: 1040px;
      padding: 2.5rem 3.5rem 5rem;
      overflow-x: hidden;
    }

    /* Typography & Spacing */
    h1 {
      font-size: 2.15rem;
      font-weight: 800;
      color: var(--text);
      letter-spacing: -0.03em;
      margin-bottom: 1rem;
      line-height: 1.25;
    }

    h2 {
      font-size: 1.55rem;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: -0.02em;
      margin-top: 3rem;
      margin-bottom: 1rem;
      padding-bottom: 0.4rem;
      border-bottom: 2px solid var(--accent-border);
    }

    h3 {
      font-size: 1.2rem;
      font-weight: 600;
      color: var(--text);
      margin-top: 1.75rem;
      margin-bottom: 0.75rem;
    }

    h4 {
      font-size: 1.05rem;
      font-weight: 600;
      color: var(--text);
      margin-top: 1.25rem;
      margin-bottom: 0.5rem;
    }

    p { margin-bottom: 1rem; }

    blockquote {
      border-left: 4px solid var(--accent);
      background: var(--accent-light);
      padding: 0.75rem 1.25rem;
      border-radius: 0 8px 8px 0;
      color: var(--text);
      margin-bottom: 1.25rem;
      font-size: 0.95rem;
    }

    blockquote p { margin: 0; }

    hr {
      border: 0;
      height: 1px;
      background: var(--border);
      margin: 2.5rem 0;
    }

    ul, ol {
      margin-bottom: 1rem;
      padding-left: 1.5rem;
    }

    li {
      margin-bottom: 0.35rem;
    }

    /* Tables */
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 1.5rem 0;
      font-size: 0.88rem;
      background: var(--card-bg);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: var(--shadow-sm);
      border: 1px solid var(--border);
    }

    th {
      background: var(--accent-light);
      color: var(--accent);
      text-align: left;
      padding: 0.75rem 0.9rem;
      font-weight: 600;
      border-bottom: 1px solid var(--border);
    }

    td {
      padding: 0.65rem 0.9rem;
      border-bottom: 1px solid var(--border);
      color: var(--text);
      vertical-align: top;
    }

    tr:last-child td { border-bottom: none; }
    tr:nth-child(even) td { background: rgba(0, 0, 0, 0.015); }

    /* Code & Pre Blocks */
    code {
      font-family: var(--font-mono);
      font-size: 0.87em;
      background: var(--code-bg);
      color: var(--code-text);
      padding: 0.15em 0.35em;
      border-radius: 4px;
      border: 1px solid var(--accent-border);
    }

    .code-container {
      position: relative;
      margin: 1.25rem 0;
    }

    pre {
      background: var(--pre-bg);
      color: var(--pre-text);
      padding: 1.1rem 1.25rem;
      border-radius: 8px;
      overflow-x: auto;
      font-family: var(--font-mono);
      font-size: 0.86rem;
      line-height: 1.5;
      box-shadow: var(--shadow-sm);
      border: 1px solid var(--border);
    }

    pre code {
      background: transparent;
      color: inherit;
      padding: 0;
      border: none;
      font-size: inherit;
    }

    .copy-btn {
      position: absolute;
      top: 0.6rem;
      right: 0.6rem;
      background: rgba(255, 255, 255, 0.15);
      color: #e5e7eb;
      border: 1px solid rgba(255, 255, 255, 0.2);
      padding: 0.25rem 0.55rem;
      border-radius: 4px;
      font-size: 0.72rem;
      cursor: pointer;
      backdrop-filter: blur(4px);
      transition: all 0.15s ease;
    }
    .copy-btn:hover {
      background: var(--accent);
      color: #ffffff;
      border-color: var(--accent);
    }

    /* Interactive Q&A Accordion Cards */
    .qa-item {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 0.75rem;
      box-shadow: var(--shadow-sm);
      transition: border-color 0.15s ease;
      overflow: hidden;
    }

    .qa-item[open] {
      border-color: var(--accent);
    }

    .qa-item summary {
      padding: 0.85rem 1.1rem;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.65rem;
      font-size: 0.95rem;
      user-select: none;
      color: var(--text);
    }

    .qa-item summary:hover {
      background: var(--accent-light);
    }

    .q-badge {
      background: var(--accent);
      color: white;
      font-size: 0.72rem;
      font-weight: 700;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      flex-shrink: 0;
    }

    .qa-body {
      padding: 0.9rem 1.25rem 1.1rem;
      border-top: 1px solid var(--border);
      background: var(--bg);
      font-size: 0.92rem;
      color: var(--text);
    }

    /* Diagram Styling */
    .diagram-box {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
      margin: 1.5rem 0;
      box-shadow: var(--shadow-sm);
    }

    .diagram-title {
      font-size: 0.9rem;
      color: var(--accent);
      margin-bottom: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .flow-svg {
      width: 100%;
      height: auto;
      display: block;
    }

    /* Badges & Status Pills */
    .pill {
      display: inline-block;
      padding: 0.15rem 0.45rem;
      border-radius: 9999px;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .pill-full { background: rgba(16, 185, 129, 0.15); color: #059669; }
    .pill-partial { background: rgba(245, 158, 11, 0.15); color: #d97706; }
    .pill-none { background: rgba(239, 68, 68, 0.15); color: #dc2626; }
    .pill-noev { background: rgba(107, 114, 128, 0.15); color: #4b5563; }

    /* Responsive Mobile Layout */
    @media (max-width: 992px) {
      body { flex-direction: column; }
      aside.sidebar {
        width: 100%;
        min-width: 100%;
        height: auto;
        position: relative;
        border-right: none;
        border-bottom: 1px solid var(--sidebar-border);
      }
      main.content { padding: 1.5rem; }
    }
  </style>
</head>
<body>

  <!-- Sticky Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="brand">
        <div class="brand-logo">T</div>
        <div class="brand-title">Tender Matching Guide</div>
      </div>
      <button class="theme-toggle-btn" id="theme-btn">
        <span id="theme-icon">&#9790;</span> Toggle Dark Mode
      </button>
      <div class="search-box">
        <input type="text" id="nav-search" class="search-input" placeholder="Filter sections...">
      </div>
    </div>
    <nav class="toc" id="toc-nav">
      <ul id="toc-list">
        <li><a href="#1-one-page-overview">1. One-Page Overview</a></li>
        <li><a href="#2-end-to-end-flow">2. End-to-End Flow</a></li>
        <li><a href="#3-tech-stack-table">3. Tech Stack & Decisions</a></li>
        <li><a href="#4-data-and-storage">4. Data, Corpus & Storage</a></li>
        <li><a href="#5-component-deep-dives">5. Component Deep Dives</a></li>
        <li><a href="#6-matching-logic-explained-simply">6. Matching Logic & Math</a></li>
        <li><a href="#7-how-accuracy-is-measured">7. Accuracy & Grounding</a></li>
        <li><a href="#8-evaluator-qa-defense-guide">8. Evaluator Q&A (65 Qs)</a></li>
        <li><a href="#9-evaluator-demo-script-5-minute-walkthrough">9. 5-Minute Demo Script</a></li>
        <li><a href="#10-one-page-defense-cheat-sheet">10. 1-Page Defense Cheat Sheet</a></li>
        <li><a href="#11-known-limits-and-honest-engineering-trade-offs">11. Known Limits & Trade-offs</a></li>
        <li><a href="#12-glossary-of-terms-complete-command-reference">12. Glossary & Commands</a></li>
        <li><a href="#13-codebase-verification-pass-claim-to-code-mapping">13. Code Audit Verification</a></li>
      </ul>
    </nav>
  </aside>

  <!-- Main Content Container -->
  <main class="content">
    {{CONTENT}}
  </main>

  <script>
    // Theme toggle
    const themeBtn = document.getElementById('theme-btn');
    const themeIcon = document.getElementById('theme-icon');
    let currentTheme = localStorage.getItem('theme') || 'light';
    
    function applyTheme(theme) {
      if (theme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
        themeIcon.innerHTML = '&#9788;';
        themeBtn.innerHTML = '<span>&#9788;</span> Light Mode';
      } else {
        document.documentElement.removeAttribute('data-theme');
        themeIcon.innerHTML = '&#9790;';
        themeBtn.innerHTML = '<span>&#9790;</span> Dark Mode';
      }
      localStorage.setItem('theme', theme);
    }
    
    applyTheme(currentTheme);
    
    themeBtn.addEventListener('click', () => {
      currentTheme = currentTheme === 'light' ? 'dark' : 'light';
      applyTheme(currentTheme);
    });

    // Copy to clipboard buttons
    document.querySelectorAll('pre').forEach(pre => {
      const container = document.createElement('div');
      container.className = 'code-container';
      pre.parentNode.insertBefore(container, pre);
      container.appendChild(pre);

      const btn = document.createElement('button');
      btn.className = 'copy-btn';
      btn.innerText = 'Copy';
      btn.addEventListener('click', () => {
        const text = pre.innerText;
        navigator.clipboard.writeText(text).then(() => {
          btn.innerText = 'Copied!';
          setTimeout(() => btn.innerText = 'Copy', 2000);
        });
      });
      container.appendChild(btn);
    });

    // Filter sidebar navigation
    const searchInput = document.getElementById('nav-search');
    const navLinks = document.querySelectorAll('#toc-list li');
    searchInput.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase();
      navLinks.forEach(li => {
        const text = li.textContent.toLowerCase();
        li.style.display = text.includes(q) ? '' : 'none';
      });
    });

    // Highlight active link on scroll
    const headings = document.querySelectorAll('main h2, main h1');
    window.addEventListener('scroll', () => {
      let current = '';
      headings.forEach(h => {
        const top = h.getBoundingClientRect().top;
        if (top <= 120) {
          current = h.getAttribute('id');
        }
      });
      if (current) {
        document.querySelectorAll('#toc-list a').forEach(a => {
          a.classList.toggle('active', a.getAttribute('href') === '#' + current);
        });
      }
    });
  </script>
</body>
</html>
"""

def main():
    print("Building full markdown...")
    raw_md = build_full_markdown()

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Save CODEBASE_GUIDE.md
    print(f"Writing Markdown to {MD_PATH}...")
    with open(MD_PATH, "w", encoding="utf-8") as f:
        f.write(raw_md)
    print(f"Saved {MD_PATH} ({len(raw_md)} chars)")

    # 2. Transform markdown for HTML rendering
    print("Transforming Q&A items and diagrams for HTML...")
    html_md = transform_mermaid_fallback(raw_md)
    html_md = transform_qa_to_accordions(html_md)

    # Convert to HTML via python-markdown
    md_converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc"],
        extension_configs={
            "toc": {"permalink": False}
        }
    )
    content_html = md_converter.convert(html_md)

    # Inject status pills
    content_html = content_html.replace('<code>full_match</code>', '<span class="pill pill-full">full_match</span>')
    content_html = content_html.replace('<code>partial_match</code>', '<span class="pill pill-partial">partial_match</span>')
    content_html = content_html.replace('<code>no_match</code>', '<span class="pill pill-none">no_match</span>')
    content_html = content_html.replace('<code>no_evidence</code>', '<span class="pill pill-noev">no_evidence</span>')

    full_html = HTML_TEMPLATE.replace("{{CONTENT}}", content_html)

    print(f"Writing HTML to {HTML_PATH}...")
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"Saved {HTML_PATH} ({len(full_html)} chars)")
    print("Guide compilation complete!")

if __name__ == "__main__":
    main()
