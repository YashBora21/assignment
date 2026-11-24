import re

path = r'D:\assignment\TenderExtractPro\frontend\src\components\Header.jsx'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

pattern = r'<div className="brand"[\s\S]*?</div>'
replacement = '''<div className="brand" style={{ fontSize: '20px', display: 'flex', alignItems: 'baseline', gap: '6px' }}>
            <span className="brand-name" style={{ fontWeight: 800, color: 'var(--text-ink)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>TENDER</span>
            <span className="brand-suffix" style={{ fontWeight: 700, color: 'var(--accent-primary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>MATCHING</span>
          </div>'''

content = re.sub(pattern, replacement, content, count=1)
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Updated brand in Header.jsx')
