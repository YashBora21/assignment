import os

path = r'D:\assignment\TenderExtractPro\frontend\src\index.css'
with open(path, 'r', encoding='utf-8') as f:
    css = f.read()

font_old = "@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=DM+Sans:wght@400;500;700&display=swap');"
font_new = "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');"

if font_old in css:
    css = css.replace(font_old, font_new)

# TenderGenie design tokens
tokens_replacement = """
  /* TenderGenie.ai Theme Design Tokens */
  --primary-ink: #0b132b;
  --primary-active: #1c2541;
  --canvas: #090d16;
  --canvas-soft: #0f172a;
  --surface-card: #111827;
  --surface-strong: #1e293b;
  --surface-dark: #030712;
  
  --text-ink: #f8fafc;
  --text-body: #cbd5e1;
  --text-muted: #94a3b8;
  --text-muted-soft: #64748b;
  
  --hairline: rgba(255, 255, 255, 0.08);
  --hairline-strong: rgba(255, 255, 255, 0.16);
  --border-color: rgba(255, 255, 255, 0.08);
  
  /* Cybernetic / Electric Accents */
  --accent-primary: #2563eb;
  --accent-glow: #38bdf8;
  --accent-success: #10b981;
  --accent-warning: #f59e0b;
  
  --gradient-mint: rgba(16, 185, 129, 0.15);
  --gradient-peach: rgba(245, 158, 11, 0.15);
  --gradient-lavender: rgba(147, 51, 234, 0.15);
  --gradient-sky: rgba(56, 189, 248, 0.15);
  --gradient-rose: rgba(239, 68, 68, 0.15);
  
  --success: #10b981;
  --error: #ef4444;
  --primary: #2563eb;

  /* Typography */
  --font-display: 'Inter', system-ui, -apple-system, sans-serif;
  --font-body: 'Inter', system-ui, -apple-system, sans-serif;
  --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
"""

idx_start = css.find(':root {')
if idx_start != -1:
    idx_end = css.find('font-family:', idx_start)
    if idx_end != -1:
        css = css[:idx_start + 7] + tokens_replacement + '\n  ' + css[idx_end:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(css)

print('Successfully applied TenderGenie design system to index.css')
