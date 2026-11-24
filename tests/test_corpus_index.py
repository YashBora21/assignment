"""
test_corpus_index.py — Category-scoped corpus indexing (Half B).

Fake embedding model and cross-encoder; no real models, no network, no LLM.

Run:
    venv\\Scripts\\python.exe -m pytest tests/test_corpus_index.py -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("qdrant_client")

import tender_extraction.retrieval as retrieval
from tender_extraction.schemas import Chunk, ChunkMetadata

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeEmbed:
    def get_sentence_embedding_dimension(self):
        return 8

    def encode(self, texts, convert_to_numpy=True, show_progress_bar=False,
               batch_size=32, normalize_embeddings=True):
        rows = []
        for text in texts:
            rng = np.random.default_rng(zlib.crc32(text.encode("utf-8")))
            vec = rng.random(8)
            rows.append(vec / np.linalg.norm(vec))
        return np.array(rows, dtype="float32")


class FakeCrossEncoder:
    def predict(self, pairs):
        return [0.5] * len(pairs)


@pytest.fixture()
def fake_models(monkeypatch):
    monkeypatch.setattr(retrieval, "get_embedding_model", lambda: FakeEmbed())
    monkeypatch.setattr(retrieval, "get_embedding_dimension", lambda model=None: 8)
    monkeypatch.setattr(retrieval, "_get_cross_encoder", lambda: FakeCrossEncoder())


def _load_ingest_module():
    spec = importlib.util.spec_from_file_location(
        "ingest_manufacturers", REPO_ROOT / "scripts" / "ingest_manufacturers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(chunk_id=chunk_id, text=text, metadata=ChunkMetadata(page=1))


def test_payload_filter_scopes_bm25_and_dense(fake_models, tmp_path):
    retriever = retrieval.HybridRetriever(persist_dir=str(tmp_path / "qdrant"))
    chunks = [
        _chunk("mfr_a:valve", "Ball valve DN100 PN16 stainless steel body."),
        _chunk("mfr_b:motor", "Electric motor 5 hp 415 V three phase."),
    ]
    payloads = {
        "mfr_a:valve": {"manufacturer": "mfr_a", "category": "valves"},
        "mfr_b:motor": {"manufacturer": "mfr_b", "category": "motors"},
    }
    try:
        retriever.build_index(chunks, collection_name="cat_test", payload_by_chunk=payloads)

        scoped = retriever.retrieve("valve motor", top_k=5, payload_filter={"manufacturer": "mfr_a"})
        assert scoped, "filter must not empty the result when matching chunks exist"
        assert all(hit["chunk"].chunk_id.startswith("mfr_a:") for hit in scoped)

        unscoped = retriever.retrieve("valve motor", top_k=5)
        prefixes = {hit["chunk"].chunk_id.split(":")[0] for hit in unscoped}
        assert prefixes == {"mfr_a", "mfr_b"}
    finally:
        retriever.close()


def _write_corpus(root: Path) -> bytes:
    content = b"%PDF-1.4 fake catalogue bytes for hashing"
    digest = hashlib.sha256(content).hexdigest()

    (root / "mfr_a").mkdir(parents=True)
    (root / "mfr_a" / "a.pdf").write_bytes(content)
    (root / "mfr_a" / "dup.pdf").write_bytes(content)
    (root / "mfr_a" / "sources.json").write_text(json.dumps({
        "manufacturer": "mfr_a",
        "category": "pumps",
        "documents": [
            {"file": "a.pdf", "category": "valves", "subtype": "ball valve",
             "doc_type": "datasheet", "sha256": digest},
            {"file": "dup.pdf"},
        ],
    }), encoding="utf-8")

    (root / "mfr_b").mkdir()
    (root / "mfr_b" / "b.pdf").write_bytes(b"%PDF-1.4 different bytes")
    (root / "mfr_b" / "sources.json").write_text(json.dumps({
        "manufacturer": "mfr_b",
        "documents": [{"file": "b.pdf"}],
    }), encoding="utf-8")

    (root / "mfr_c").mkdir()
    (root / "mfr_c" / "c.pdf").write_bytes(b"%PDF-1.4 motor bytes")
    (root / "mfr_c" / "sources.json").write_text(json.dumps({
        "manufacturer": "mfr_c",
        "category": "motors",
        "documents": [{"file": "c.pdf"}],
    }), encoding="utf-8")
    return content


def test_index_corpus_category_fallback_and_dedup(fake_models, tmp_path, monkeypatch):
    ingest = _load_ingest_module()
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)

    monkeypatch.setattr(ingest, "ingest_document", lambda path: [
        {"page": 1, "text": f"Catalogue text for {Path(path).name}.", "is_ocr": False, "headings": []},
    ])

    chunks_by_cat, payloads, notes = ingest.collect(corpus)
    # doc category beats manufacturer category; absent both -> general;
    # manufacturer category used when the doc is silent.
    assert set(chunks_by_cat) == {"valves", "general", "motors"}
    assert any("duplicate" in note for note in notes)

    valve_payloads = [p for p in payloads.values() if p["category"] == "valves"]
    assert valve_payloads and all(p["manufacturer"] == "mfr_a" for p in valve_payloads)
    assert valve_payloads[0]["subtype"] == "ball valve"
    assert valve_payloads[0]["doc_type"] == "datasheet"
    assert len(valve_payloads) == len(chunks_by_cat["valves"])  # dup.pdf indexed once

    counts = ingest.index_corpus(corpus, persist_dir=str(tmp_path / "qdrant"))
    assert counts == {cat: len(chunks) for cat, chunks in chunks_by_cat.items()}

    retriever = retrieval.HybridRetriever(persist_dir=str(tmp_path / "qdrant"))
    try:
        # Same chunks + payloads -> manifest fingerprint match -> reuse path.
        retriever.build_index(
            chunks_by_cat["valves"], collection_name="cat_valves", payload_by_chunk=payloads
        )
        hits = retriever.retrieve("catalogue", top_k=5, payload_filter={"manufacturer": "mfr_a"})
        assert hits
        assert all(h["chunk"].chunk_id.startswith("mfr_a:") for h in hits)
    finally:
        retriever.close()


def test_product_payload_and_review_export(fake_models, tmp_path, monkeypatch):
    ingest = _load_ingest_module()
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    monkeypatch.setattr(ingest, "ingest_document", lambda path: [])
    monkeypatch.setattr(ingest, "extract_tables", lambda *a, **kw: [
        {"table_id": "table_001", "page": 3, "raw": [["Model", "Power (kW)"], ["A1", "10"], ["", "9"]]},
    ])
    review = tmp_path / "review"
    chunks, payloads, notes = ingest.collect(corpus, review_dir=review)
    assert len(chunks["valves"]) == 1
    payload = payloads[chunks["valves"][0].chunk_id]
    assert payload["model"] == "A1"
    assert payload["specs"] == {"Power (kW)": "10 kW"}
    assert payload["page"] == 3 and payload["table_id"] == "table_001"
    assert payload["sha256"] and payload["manufacturer"] == "mfr_a"
    assert "[Table Headers]" in chunks["valves"][0].text
    sample = json.loads((review / "valves.json").read_text(encoding="utf-8"))
    assert sample["sample_count"] == 1
    assert sample["tables"][0]["rejected"][0]["reason"] == "missing model"
    assert "#page=3" in (review / "valves.html").read_text(encoding="utf-8")


def test_table_row_stays_one_qdrant_point(fake_models, tmp_path):
    retriever = retrieval.HybridRetriever(persist_dir=str(tmp_path / "qdrant"))
    row = Chunk(chunk_id="long_table_row", text="Model A1 " + "pressure 16 bar " * 100,
                metadata=ChunkMetadata(page=3, table_id="t1", chunk_type="table"))
    try:
        retriever.build_index([row], collection_name="table_test")
        assert retriever._qdrant.count("table_test").count == 1
    finally:
        retriever.close()


def test_review_sample_contains_25_distinct_tables(tmp_path, monkeypatch):
    ingest = _load_ingest_module()
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    monkeypatch.setattr(ingest, "ingest_document", lambda path: [])
    monkeypatch.setattr(ingest, "extract_tables", lambda *a, **kw: [
        {"table_id": f"table_{index}", "page": index + 1,
         "raw": [["Model", "Power (kW)"], [f"M{index}", "10"]]}
        for index in range(40)
    ])
    review = tmp_path / "review"
    ingest.collect(corpus, review_dir=review)
    for category in ("valves", "motors", "general"):
        data = json.loads((review / f"{category}.json").read_text(encoding="utf-8"))
        assert data["total_extracted_tables"] == 40
        assert data["sample_count"] == 25
        assert len({table["table_id"] for table in data["tables"]}) == 25


def test_review_category_cannot_escape_output_directory(tmp_path):
    ingest = _load_ingest_module()
    with pytest.raises(ValueError, match="Invalid category slug"):
        ingest.export_reviews(tmp_path / "review", {"../escaped": []}, {"../escaped": 0})
    assert not (tmp_path / "escaped.json").exists()


def test_category_resume_skips_other_documents_and_preserves_collections(fake_models, tmp_path, monkeypatch):
    ingest = _load_ingest_module()
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    visited = []
    def load(path):
        visited.append(Path(path).name)
        return [{"page": 1, "text": "Valve pressure 16 bar.", "is_ocr": False, "headings": []}]
    monkeypatch.setattr(ingest, "ingest_document", load)
    monkeypatch.setattr(ingest, "extract_tables", lambda *a, **kw: [])
    storage = str(tmp_path / "qdrant")
    retriever = retrieval.HybridRetriever(persist_dir=storage)
    try:
        retriever.build_index([_chunk("motor", "Existing motor evidence")], collection_name="cat_motors")
    finally:
        retriever.close()
    manifest = tmp_path / "qdrant" / "_hybrid_manifest.json"
    motor_before = json.loads(manifest.read_text())["cat_motors"]
    counts = ingest.index_corpus(corpus, persist_dir=storage, category_filter="valves")
    assert set(counts) == {"valves"}
    assert visited == ["a.pdf"]
    assert json.loads(manifest.read_text())["cat_motors"] == motor_before
