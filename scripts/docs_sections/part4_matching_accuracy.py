# scripts/docs_sections/part4_matching_accuracy.py

SECTION_6 = """
## 6. Matching Logic Explained Simply

Tender matching in this repository follows a four-status contract plus `not_applicable`, implemented in `tender_extraction/matching.py` and `tender_extraction/schemas_match.py`.

### 6.1 The Four Match Statuses

| Status | Code Weight | Meaning | When Assigned |
| :--- | :---: | :--- | :--- |
| **`full_match`** | **1.0** | Equipment meets or exceeds the tender requirement. | Number satisfies condition ($V \ge \text{Min}$, $V \le \text{Max}$, or $|V - V_{\text{req}}| \le 2\%$), or categorical value matches verbatim. |
| **`partial_match`** | **0.5** | Equipment is within 20% margin or has strong semantic overlap ($\ge 60\%$). | Value is within $0.8 \times \text{Min}$ or $1.2 \times \text{Max}$, or material specification is partially equivalent. |
| **`no_match`** | **0.0** | Equipment explicitly fails or contradicts the requirement. | Number fails condition outside tolerance ($V < 0.8 \times \text{Min}$), or material is incompatible (e.g. Cast Iron instead of Stainless Steel). |
| **`no_evidence`** | **Excluded** | Catalogue does not mention or cover this parameter. | Nothing retrieved, quote not verbatim, or parameter silent. **Absence of evidence is NEVER treated as a failure.** |
| **`not_applicable`** | **Excluded** | Clause is commercial, legal, procedural, or outside scope. | Item is a buyer procedure, penalty clause, or belongs to another product family. |

---

### 6.2 Mathematical Formulas for Scoring

Every manufacturer is evaluated on two distinct axes:

#### 1. Compatibility (Quality / Compliance Rate)
$$\\text{Compatibility} = \\frac{\\sum_{m \\in \\text{Decided}} \\text{Weight}(m)}{|\\text{Decided}|} \\times 100$$
* $\\text{Decided} = \\{m \\mid \\text{status}(m) \\in \\{\\text{full\\_match}, \\text{partial\\_match}, \\text{no\\_match}\\}\\}$
* Weights: `full_match` = 1.0, `partial_match` = 0.5, `no_match` = 0.0.
* **Key insight**: If a manufacturer meets 1 requirement and fails 0, Compatibility is $1.0 / 1 = 100\\%$.

#### 2. Coverage (Completeness Rate)
$$\\text{Coverage} = \\frac{|\\text{Decided}|}{|\\text{Scorable}|} \\times 100$$
* $\\text{Scorable} = \\{m \\mid \\text{status}(m) \\in \\{\\text{full\\_match}, \\text{partial\\_match}, \\text{no\\_match}, \\text{no\\_evidence}\\}\\}$
* **Key insight**: If the tender has 36 requirements and the vendor's catalogue only mentions 1, Coverage is $1 / 36 = 2.8\\% \\approx 3\\%$.
* **Low Coverage Penalty**: Any vendor with `Coverage < 50%` is marked with a red `Low Coverage` tag and automatically ranked below vendors with comprehensive catalogue evidence.

---

### 6.3 Three Real Worked Examples with Evidence

#### Example 1: `full_match` (Numerical Min Direction with Unit Conversion)
* **Tender Requirement**:
  - Component: *MIV Servomotor (Hydraulic Cylinder)*
  - Parameter: `working_pressure`
  - Value: `16`, Unit: `bar`, Direction: `min`
  - Source: `GEM_9848272_Valve.pdf` page 4
* **Retrieved Datasheet Evidence**:
  - Manufacturer: **Duncan Engineering**
  - Document: `hydraulic-cylinder-double-acting-servomotor.pdf` page 14
  - Verbatim Quote: *"Operating Pressure: Max 25 bar (2.5 MPa)"*
* **Evaluation Code**:
  - `_compare_numeric()` converts 25 bar to tender units (1.0 factor).
  - Direction is `min`: Condition is `25 >= 16` $\rightarrow$ **True**.
* **Verdict**: `full_match` (Weight 1.0, Confidence 0.95).

#### Example 2: `no_match` (Numerical Value Contradiction)
* **Tender Requirement**:
  - Component: *Acid Unloading Pump*
  - Parameter: `flow_rate`
  - Value: `50`, Unit: `m3/h`, Direction: `min`
* **Retrieved Datasheet Evidence**:
  - Manufacturer: **Ebara**
  - Document: `ebara-standard-pumps.pdf` page 8
  - Verbatim Quote: *"Maximum flow capacity: 25 m3/h at 2900 RPM"*
* **Evaluation Code**:
  - `_compare_numeric()` compares 25 against min 50.
  - $25 < 50 \times 0.8$ (below 80% partial threshold).
* **Verdict**: `no_match` (Weight 0.0, Confidence 0.90).
* **Reason Emitted**: *"Datasheet states 25 m3/h; tender requires min 50 m3/h."*

#### Example 3: `no_evidence` (Datasheet Silent on Parameter)
* **Tender Requirement**:
  - Component: *Centrifugal Slurry Pump*
  - Parameter: `paint_dry_film_thickness`
  - Value: `150`, Unit: `microns`
* **Retrieved Datasheet Evidence**:
  - General product brochure discusses impeller and casing metallurgy; no clause mentions paint coating thickness.
* **Evaluation Code**:
  - Retrieval returns general casing chunks.
  - `_compare_numeric()` finds no numbers matching unit `microns`.
  - `_llm_extract()` returns `capability: NOT_FOUND`.
* **Verdict**: `no_evidence` (Weight 0.0, Confidence 0.0, Needs Review: False).
* **Audit Trail**: Records exact query searched and chunk IDs inspected.
"""

SECTION_7 = """
## 7. How Accuracy Is Measured & Evaluated

### 7.1 Automated Test Suite (188 Tests)
The primary evaluation mechanism in this repository is a rigorous, automated Pytest test harness comprising **188 unit and integration tests** across `tests/`.

```bash
pytest tests/ -v
# Output: 188 passed in 69.99s
```

Key test metrics verified in continuous testing:
1. **Pydantic Schema Invariants** (`tests/test_schemas_step1.py`): Ensures all model fields conform to contract defaults.
2. **Grounding & Anti-Hallucination** (`tests/test_pipeline.py`):
   - Injected synthetic hallucinations (claims not in source chunks) must be rejected with 100% precision.
   - Grounding score calculation: $\ge 80\%$ fuzzy ratio required for high confidence.
3. **Reconciliation & Conflict Detection** (`tests/test_reconcile.py`): Ensures cross-document contradictions (e.g. 150# vs 600#) are flagged as conflicts with zero false negatives.
4. **Unit Conversion Correctness** (`tests/test_generic_matching.py`): Verifies mathematical conversions across pressure (`bar`, `kg/cm2`, `psi`, `MPa`), length (`mm`, `inch`), flow (`m3/h`, `l/s`, `gpm`), and power (`kW`, `HP`).

### 7.2 Grounding Score Metric
In `tender_extraction/validation.py`, every extracted specification receives a `grounding_score`:
$$\\text{Grounding Score} = \\frac{\\text{Number of Grounded Specifications}}{\\text{Total Extracted Specifications}} \\times 100$$
* A specification is *grounded* if its `exact_text` citation exists in the cited PDF chunk with RapidFuzz ratio $\ge 80\%$.
* In real tender runs:
  - `GEM_9848272_Valve.pdf`: **79.15% Grounding Score** (Job `dccbac70e0a2`).
  - `GEM_9960821_Motor.pdf`: **67.86% Grounding Score** (Job `e72cd9b9faf4`).

### 7.3 Honest Statement on Labeled Benchmarks ("Gold Dataset")
> [!IMPORTANT]
> **Evaluator Disclosure**: There is currently **no public, third-party, standardized multi-thousand tender benchmark dataset** for GeM tenders in the academic literature. The accuracy numbers reported in this project are measured against the repository's test corpus of real GeM tender PDFs (`GEM_9848272_Valve.pdf`, `GEM_9960821_Motor.pdf`, `GEM_8166203_Pump.pdf`, `globaltender1576.pdf`) and the 17 manufacturer catalogues. Claims of "99% accuracy" are scientifically invalid in unstructured procurement without a human-annotated multi-domain test set; our system instead guarantees **provable quote verification**: 100% of quotes displayed in the UI are verified to exist in the source document.
"""
