path = r'D:\assignment\TenderExtractPro\frontend\src\components\CostTokenWidget.jsx'
with open(path, 'r', encoding='utf-8') as f:
    code = f.read()

# Replace blue gradient with warm terracotta/peach accents
code = code.replace(
    "linear-gradient(135deg, rgba(37,99,235,0.2), rgba(59,130,246,0.3))",
    "linear-gradient(135deg, rgba(217,107,50,0.15), rgba(232,134,80,0.25))"
)
code = code.replace(
    "border: '1px solid var(--accent-glow)'",
    "border: '1px solid rgba(217,107,50,0.3)'"
)
code = code.replace(
    "color: 'var(--accent-glow)'",
    "color: 'var(--accent-primary)'"
)
code = code.replace(
    "boxShadow: '0 4px 20px rgba(0,0,0,0.15)'",
    "boxShadow: '0 4px 20px rgba(217,107,50,0.06)'"
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(code)

print('Updated CostTokenWidget.jsx styling to match warm terracotta theme')
