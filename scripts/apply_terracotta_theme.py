path = r'D:\assignment\TenderExtractPro\frontend\src\index.css'
with open(path, 'r', encoding='utf-8') as f:
    css = f.read()

tokens_replacement = """
  /* Tender Matching (Warm Terracotta / Peach / White Theme) */
  --primary-ink: #1e1e24;
  --primary-active: #111116;
  --canvas: #ffffff;
  --canvas-soft: #fffaf7;
  --surface-card: #ffffff;
  --surface-strong: #fff3ec;
  --surface-dark: #1e1e24;
  
  --text-ink: #1a1a1e;
  --text-body: #4a4a54;
  --text-muted: #8a8a96;
  --text-muted-soft: #b8b8c4;
  
  --hairline: #f4e8e1;
  --hairline-strong: #e8d5cb;
  --border-color: #f0ded4;
  
  /* Terracotta Brand Accents from Image */
  --accent-primary: #d96b32;       /* Terracotta Orange / Rust */
  --accent-secondary: #c25721;     /* Deep Terracotta */
  --accent-glow: #e88650;          /* Warm Peach Glow */
  --accent-soft: #fdf3ed;          /* Light Peach Surface */
  --accent-success: #15803d;       /* Forest / Muted Green */
  --accent-warning: #b45309;       /* Deep Amber */

  --gradient-mint: rgba(21, 128, 61, 0.12);
  --gradient-peach: #fff0e7;
  --gradient-lavender: #fdf3ed;
  --gradient-sky: #ffeedd;
  --gradient-rose: #ffebe5;
  
  --success: #15803d;
  --error: #dc2626;
  --primary: #d96b32;

  /* Typography */
  --font-display: 'Inter', system-ui, -apple-system, sans-serif;
  --font-body: 'Inter', system-ui, -apple-system, sans-serif;
  --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
"""

dark_tokens_replacement = """
  --primary-ink: #fdf8f5;
  --primary-active: #ffffff;
  --canvas: #161413;
  --canvas-soft: #1f1b19;
  --surface-card: #25201d;
  --surface-strong: #332b27;
  --surface-dark: #0f0d0c;
  
  --text-ink: #fbf6f3;
  --text-body: #ded4ce;
  --text-muted: #ab9c94;
  --text-muted-soft: #7a6e67;
  
  --hairline: #38302c;
  --hairline-strong: #4f443e;
  --border-color: #38302c;
  
  --accent-primary: #e88650;
  --accent-secondary: #d96b32;
  --accent-glow: #f09e70;
  --accent-soft: #2d2420;
  --accent-success: #22c55e;
  --accent-warning: #f59e0b;
  
  --gradient-peach: #2d2420;
  --gradient-sky: #2d2420;
  
  --primary: #e88650;
"""

idx_start = css.find(':root {')
if idx_start != -1:
    idx_end = css.find('font-family:', idx_start)
    if idx_end != -1:
        css = css[:idx_start + 7] + tokens_replacement + '\n  ' + css[idx_end:]

dark_start = css.find("[data-theme='dark'] {")
if dark_start != -1:
    dark_end = css.find('}', dark_start)
    if dark_end != -1:
        css = css[:dark_start + 21] + dark_tokens_replacement + '\n' + css[dark_end:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(css)

print('Updated index.css with warm terracotta/peach color palette')
