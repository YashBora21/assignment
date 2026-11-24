path = r'D:\assignment\TenderExtractPro\frontend\src\components\ResultViewer.jsx'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 1. Import
for i, l in enumerate(lines):
    if 'import ReconciliationPanel' in l:
        lines[i] = l + "import CostTokenWidget from './CostTokenWidget';\n"
        break

# 2. Badge
for i, l in enumerate(lines):
    if 'span className={`badge ${matchingBlocked' in l:
        lines[i] = "                <span className={`badge ${result.tender_complete === false ? 'error' : result.tender_status === 'partially_matched' ? 'warning' : matchingBlocked ? 'error' : 'done'}`}>\n"
        lines[i+1] = "                  {result.tender_complete === false ? 'Tender Incomplete' : result.tender_status === 'partially_matched' ? 'Partially Matched' : (matchingBlocked ? 'Reconciliation Blocked' : 'Analysis Complete')}\n"
        break

# 3. Widget
for i, l in enumerate(lines):
    if '{warnings.length > 0 && (' in l:
        lines.insert(i, "      <CostTokenWidget result={result} job={job} />\n\n")
        break

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Updated ResultViewer.jsx cleanly')
