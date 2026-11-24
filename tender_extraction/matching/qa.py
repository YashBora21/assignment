from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from tender_extraction.chunking import create_chunks
from tender_extraction.extraction import answer_question
from tender_extraction.ingestion import ingest_document
from tender_extraction.main import discover_document_topic
from tender_extraction.retrieval import HybridRetriever, expand_query
from tender_extraction.schemas import Chunk
from tender_extraction.table_extraction import extract_docx_tables, extract_image_tables, extract_tables
from tender_extraction.vision import release_multimodal_model

logger = logging.getLogger(__name__)


class DocumentChatSession:
    """Cached retrieval session for grounded document Q&A."""

    def __init__(self, file_path: str, persist_dir: Optional[str] = None, force_reindex: bool = False):
        self.file_path = str(file_path)
        self.persist_dir = persist_dir
        self.force_reindex = force_reindex
        self.topic = ""
        self._retriever: Optional[HybridRetriever] = None
        self._ready = False
        self._build_lock = threading.Lock()
        self._ask_lock = threading.Lock()

    def build(self) -> "DocumentChatSession":
        # Idempotent even when two requests race on the first question.
        with self._build_lock:
            return self._build_locked()

    def _build_locked(self) -> "DocumentChatSession":
        if self._ready:
            return self

        chunks: List[Chunk] = []
        topic = ""

        # Try to load pre-computed chunks first
        chunks_path = Path(self.file_path).with_name(f"{Path(self.file_path).stem}_chunks.json")
        # In the API context, output_path is in outputs/, but file_path is in uploads/
        # api/main.py uses outputs/{job_id}.json as output_path, so chunks are in outputs/{job_id}_chunks.json
        # We need to find the chunks file.
        
        possible_chunks_paths = [
            chunks_path,
            Path("outputs") / f"{Path(self.file_path).stem}_chunks.json",
        ]

        loaded_chunks = False
        for p in possible_chunks_paths:
            if p.exists():
                try:
                    import json
                    from tender_extraction.schemas import Chunk
                    data = json.loads(p.read_text(encoding="utf-8"))
                    chunks = [Chunk(**c) for c in data]
                    loaded_chunks = True
                    logger.info("Loaded %d pre-computed chunks from %s", len(chunks), p)
                    break
                except Exception as e:
                    logger.warning("Failed to load chunks from %s: %s", p, e)

        if not loaded_chunks:
            pages = ingest_document(self.file_path)
            suffix = Path(self.file_path).suffix.lower()
            if suffix == ".pdf":
                tables = extract_tables(self.file_path, pages=pages)
            elif suffix == ".docx":
                tables = extract_docx_tables(self.file_path)
            elif suffix in {".jpg", ".jpeg", ".png"}:
                tables = extract_image_tables(self.file_path)
            else:
                tables = []
            release_multimodal_model()
            # Use faster chunking for QA if not pre-computed
            chunks = create_chunks(pages, tables, use_semantic=False)
            topic = discover_document_topic(pages)
        else:
            # Derive the topic from the cached chunks. The old code re-ran
            # ingest_document() here, which meant a full OCR pass on scanned
            # PDFs just to build the query prefix for the first question.
            topic = _topic_from_chunks(chunks)

        retriever = HybridRetriever(persist_dir=self.persist_dir)
        digest = hashlib.md5(Path(self.file_path).resolve().as_posix().encode("utf-8")).hexdigest()[:10]
        collection_name = f"qa_{Path(self.file_path).stem[:20]}_{digest}"
        retriever.build_index(chunks, collection_name=collection_name, force_rebuild=self.force_reindex)

        self.topic = topic
        self._retriever = retriever
        self._ready = True
        return self

    def ask(self, question: str) -> Dict[str, Any]:
        self.build()
        with self._ask_lock:
            retriever = self._retriever
            assert retriever is not None

            queries: List[str] = [question, expand_query(question)]
            if self.topic:
                queries.append(f"{self.topic} {question}")

            seen = set()
            merged: List[Dict[str, Any]] = []
            for query in queries:
                for hit in retriever.retrieve_question_chunks(query, top_k=8):
                    chunk_id = hit["chunk"].chunk_id
                    if chunk_id in seen:
                        continue
                    seen.add(chunk_id)
                    merged.append(hit)

            merged.sort(key=lambda item: item.get("score", 0.0), reverse=True)
            return answer_question(merged[:8], question, self.topic)

    def close(self) -> None:
        with self._build_lock:
            if self._retriever is not None:
                self._retriever.close()
                self._retriever = None
            self._ready = False


def _topic_from_chunks(chunks: List[Chunk], max_chars: int = 800) -> str:
    """Cheap topic discovery from already-ingested chunks (no OCR)."""
    sample = " ".join(chunk.text for chunk in chunks[:4]).strip()
    if not sample:
        return ""
    try:
        return discover_document_topic([{"text": sample[:max_chars]}])
    except Exception:  # noqa: BLE001 - topic is a retrieval hint, never fatal
        return sample[:300]
