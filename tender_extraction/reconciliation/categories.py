"""categories.py — Product-category vocabulary derived from manufacturer manifests.

The only source of truth for category names and product vocabulary is
``data/manufacturers/*/sources.json``.  Nothing in this module hardcodes a
manufacturer, a product family, or a keyword list of tenders.

A category vocabulary is the union of:
  * the manifest ``category`` value (e.g. ``pumps``),
  * the ``title`` / ``subtype`` / ``doc_type`` of every catalogued document.

Classification is deterministic token overlap.  Category-name tokens weigh
more than document-title tokens.  A weak or ambiguous result returns ``None``
so callers can keep the requirement at tender level instead of guessing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Generic document words carry no product meaning.
_GENERIC_TERMS = {
    "catalogue", "catalog", "datasheet", "data", "sheet", "brochure", "guide",
    "manual", "handbook", "series", "general", "purpose", "product", "products",
    "industrial", "industry", "technical", "specification", "specifications",
    "selection", "rev", "revision", "en", "pdf", "docx", "volume", "part",
    "section", "model", "models", "range", "type", "types", "new",
}

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "has", "have", "had", "not", "but", "all", "any", "can", "will", "shall",
    "must", "may", "its", "their", "our", "your", "per", "into", "onto", "than",
    "then", "when", "where", "which", "who", "how", "why", "also", "such",
    "other", "more", "most", "less", "least", "each", "every", "some", "only",
    "used", "use", "using", "based", "made", "make", "including", "include",
    "includes", "available", "suitable", "required", "requirement",
    "requirements", "supply", "supplied", "supplying", "provided", "provide",
    "offer", "offered", "according", "accordance", "as", "at", "by", "in",
    "of", "on", "or", "to", "is", "be", "an", "a",
}


def _stem(token: str) -> str:
    """Very small, deterministic plural normaliser (not a linguistic stemmer)."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


# "Pump without motor", "valve excluding actuator": the negated product must
# not contribute a category vote.  This is generic phrasing, not a product list.
_NEGATION_RE = re.compile(
    r"\b(?:without|excluding|except|less)\b(?:\s+[a-z0-9][a-z0-9/.\-]*){1,3}",
    re.IGNORECASE,
)


def strip_negated_products(text: Any) -> str:
    """Remove "without <product>" style phrases before classification."""
    return _NEGATION_RE.sub(" ", str(text or ""))


def tokens(text: Any) -> Set[str]:
    """Normalised content tokens used for manifest and tender text alike."""
    raw = strip_negated_products(text).lower()
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    out: Set[str] = set()
    for word in raw.split():
        if len(word) < 3:
            continue
        if word in _STOPWORDS or word in _GENERIC_TERMS:
            continue
        if word.isdigit():
            continue
        out.add(_stem(word))
    return out


def normalize_category(raw: Any) -> str:
    """Category slug as used by the ingest payload and ``cat_<category>``."""
    return re.sub(r"\s+", "-", str(raw or "").strip().lower())


@dataclass
class CategoryVocabulary:
    """Category names and their manifest-derived product terms."""

    names: Dict[str, Set[str]] = field(default_factory=dict)
    document_terms: Dict[str, Set[str]] = field(default_factory=dict)
    sources: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def categories(self) -> List[str]:
        return sorted(self.names)

    def add(self, category: str, *, name_terms: Iterable[str], doc_terms: Iterable[str], source: str) -> None:
        self.names.setdefault(category, set()).update(name_terms)
        self.document_terms.setdefault(category, set()).update(doc_terms)
        if source not in self.sources.setdefault(category, []):
            self.sources[category].append(source)


def build_vocabulary(manufacturers_dir: str | Path) -> CategoryVocabulary:
    """Read every ``sources.json`` and return the category vocabulary."""
    root = Path(manufacturers_dir)
    vocab = CategoryVocabulary()
    if not root.is_dir():
        return vocab
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith(("_", ".")):
            continue
        manifest_path = sub / "sources.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        category = normalize_category(manifest.get("category"))
        if not category or category in {"not_found", "unknown", "none"}:
            continue
        doc_terms: Set[str] = set()
        for doc in manifest.get("documents") or []:
            if not isinstance(doc, dict):
                continue
            doc_terms |= tokens(doc.get("title"))
            doc_terms |= tokens(doc.get("subtype"))
            doc_terms |= tokens(doc.get("doc_type"))
        vocab.add(
            category,
            name_terms=tokens(category.replace("-", " ")),
            doc_terms=doc_terms,
            source=sub.name,
        )
    return vocab


def score_categories(text: Any, vocab: CategoryVocabulary) -> Dict[str, float]:
    """Return category -> score for one piece of text."""
    found = tokens(text)
    if not found:
        return {}
    scores: Dict[str, float] = {}
    for category in vocab.names:
        name_hits = found & vocab.names.get(category, set())
        doc_hits = found & vocab.document_terms.get(category, set())
        score = 3.0 * len(name_hits) + 1.0 * len(doc_hits)
        if score:
            scores[category] = score
    return scores


def classify_text(
    text: Any,
    vocab: CategoryVocabulary,
    *,
    min_score: float = 2.0,
    margin: float = 1.25,
) -> Tuple[Optional[str], Dict[str, float]]:
    """Classify one text; return ``(category|None, all_scores)``.

    A category is returned only when it clears ``min_score`` and beats the
    runner-up by ``margin``.  Otherwise the caller keeps the requirement
    unscoped rather than guessing.
    """
    scores = score_categories(text, vocab)
    if not scores:
        return None, {}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_category, best_score = ranked[0]
    if best_score < min_score:
        return None, scores
    if len(ranked) > 1 and best_score < ranked[1][1] * margin:
        return None, scores
    return best_category, scores


def classify_tender(
    item_descriptions: Iterable[Tuple[str, str]],
    requirement_texts: Iterable[str],
    vocab: CategoryVocabulary,
) -> Tuple[Optional[str], Dict[str, float]]:
    """Classify a tender from its items, falling back to its requirements.

    ``item_descriptions`` is an iterable of ``(item_type, description)``;
    main products weigh double.
    """
    votes: Dict[str, float] = {}
    total: Dict[str, float] = {}
    for item_type, description in item_descriptions:
        category, scores = classify_text(description, vocab)
        for name, value in scores.items():
            total[name] = total.get(name, 0.0) + value
        if category:
            weight = 2.0 if str(item_type) == "main_product" else 1.0
            votes[category] = votes.get(category, 0.0) + weight
    if votes:
        ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
        return ranked[0][0], total
    if total:
        ranked = sorted(total.items(), key=lambda item: (-item[1], item[0]))
        if ranked[0][1] >= 2.0:
            return ranked[0][0], total
    # No usable items: classify the concatenated requirement text.
    joined = " ".join(str(text or "") for text in requirement_texts)
    return classify_text(joined, vocab)


def classify_requirement(
    text: Any,
    item_category: Optional[str],
    tender_category: Optional[str],
    vocab: CategoryVocabulary,
) -> Optional[str]:
    """Requirements inherit their item category, then the tender category."""
    if item_category:
        return normalize_category(item_category)
    category, _ = classify_text(text, vocab)
    return category or tender_category
