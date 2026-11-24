# Manufacturer corpus

Half A's matching engine compares every tender requirement against the
catalogues/datasheets in this directory. Half B owns the layout and the
provenance checker; the matching code reads it read-only.

## Layout

```
data/manufacturers/
  registry.json                 # optional category hint + manufacturer list
  <manufacturer-slug>/
    sources.json                # required provenance manifest
    <catalogue>.pdf             # datasheets, brochures, manuals (any supported format)
    <another>.docx
  _template/                    # copy this when adding a manufacturer
```

Rules:

* One directory per manufacturer, named with a short slug (`ksb`, `duncan`,
  `delval`, `intervalve-poonawala`). Directories starting with `_` or `.` are
  ignored by the loader and the validator.
* Every document must appear in `sources.json`. A catalogue without a source
  URL and retrieval date is not evidence the evaluator can trust.
* Keep the original file name; do not rename downloaded PDFs to hide the
  source.
* Prefer official public datasheets. Note the exact URL and the download date.

## `sources.json` schema

```json
{
  "manufacturer": "ksb",
  "display_name": "KSB",
  "category": "pumps",
  "documents": [
    {
      "file": "ksb-etanorm-datasheet.pdf",
      "title": "Etanorm standardised water pump — technical data",
      "url": "https://www.ksb.com/.../etanorm.pdf",
      "retrieved_at": "2026-11-18",
      "pages": 12,
      "doc_type": "datasheet"
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `manufacturer` | yes | Must match the directory slug |
| `display_name` | no | Used in the report; defaults to `manufacturer` |
| `category` | no | e.g. `valves`, `pumps`; used to group reports |
| `documents[].file` | yes | Path relative to the manufacturer directory |
| `documents[].url` | yes | `http(s)://` public source |
| `documents[].retrieved_at` | yes | ISO date (`YYYY-MM-DD`) |
| `documents[].pages` | yes | Positive integer |
| `documents[].doc_type` | yes | `datasheet`, `catalogue`, `brochure`, `manual`, `other` |
| `documents[].title` | no | Human-readable document title |
| `documents[].category` | no | Per-document category (`pumps`, `valves`, `motors`, ...). Falls back to the manufacturer-level `category`, then `general`. Selects the `cat_<category>` Qdrant collection. |
| `documents[].subtype` | no | Product subtype or family (`butterfly valve`, `Etanorm`). Soft payload filter at retrieval, never a hard boundary. |
| `documents[].sha256` | no | Content hash of the file. Computed at ingest when absent; identical hashes across manufacturers are indexed once (distributor copies). |

## Indexing (category-scoped retrieval)

`scripts/ingest_manufacturers.py` reads these manifests and builds one Qdrant
collection per category (`cat_valves`, `cat_pumps`, `cat_motors`, `cat_general`)
with payload `{manufacturer, subtype, doc_type, document, category, sha256}` on
every point. A requirement is retrieved only inside its own category; the
manufacturer/subtype payload narrows it further. Storage stays
manufacturer-first so provenance lives in one manifest per manufacturer.

Manufacturer ingestion also extracts structured tables. Accepted product rows
have repeated flattened headers and payload `page`, `table_id`, `model`, and
`specs`, alongside the provenance fields. Unit spellings are normalized without
converting measurements. Vertical merged cells are filled only when PDF cell
geometry proves the span; blank models and misaligned rows are rejected.
Tables without an explicit model/type/series column remain review-only.

`scripts/ingest_manufacturers.py` exports a reproducible random sample of up to
25 extracted tables per category to `outputs/table_review/<category>.html` and
`.json` (seed 42). Samples include raw cells, accepted normalized rows, rejected
rows with reasons, and links to 1-based PDF page numbers. These are samples of
all detected tables, including rejected ones, rather than only successful rows.
Use `--review-only` to export without downloading embeddings or indexing.
Use `--category valves` to resume only valves without reprocessing motors or pumps.
Scanned pages require the existing optional multimodal table extractor for
structured cells; PaddleOCR text alone does not recover table geometry.

## Validate before running matches

```powershell
python scripts/validate_manufacturer_corpus.py
# or
python scripts/validate_manufacturer_corpus.py --root data/manufacturers
```

The script checks the manifest, the source URL, the retrieval date and that
every referenced file exists. It exits non-zero on errors so it can run in CI.

## Quick-start with two manufacturers

Pick the category that matches your two tenders (for example valves). Then:

```powershell
# 1. Create the folders
New-Item -ItemType Directory -Force data\manufacturers\ksb, data\manufacturers\duncan

# 2. Download public datasheets (replace the URLs with the real ones)
Invoke-WebRequest -Uri "https://example.com/ksb-datasheet.pdf" `
  -OutFile "data\manufacturers\ksb\ksb-datasheet.pdf"
Invoke-WebRequest -Uri "https://example.com/duncan-datasheet.pdf" `
  -OutFile "data\manufacturers\duncan\duncan-datasheet.pdf"

# 3. Copy the template and edit it
Copy-Item data\manufacturers\_template\sources.json data\manufacturers\ksb\sources.json
Copy-Item data\manufacturers\_template\sources.json data\manufacturers\duncan\sources.json

# 4. Validate
python scripts\validate_manufacturer_corpus.py
```

On macOS/Linux use `curl -L -o <path> <url>` instead of `Invoke-WebRequest`.

The tenders themselves already live in `dataset/`; upload them through the UI
or point the pipeline at them. If you want a different pair, add the PDFs to
`dataset/` and list them in your evaluation notes.
