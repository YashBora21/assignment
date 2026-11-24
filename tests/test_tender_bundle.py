"""GeM bundles: no network/model calls, authoritative BOQ rows, fail-closed matching."""
import hashlib
import json
import socket
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from tender_extraction import tender_bundle as bundle

SPEC_URL = "https://mkp.gem.gov.in/uploaded_documents/BoqDocument/specification.pdf"
BOQ_URL = "https://mkp.gem.gov.in/uploaded_documents/BoqLineItemsDocument/items.csv"
NAMES = ["Tank for Drinking Water", "Valve Drain", "Relief Valve Vaccum and Pressure",
         "Winter Sweeping Device Right Hand 527 08", "Tyre Valve Core"]
CSV = "S.No.,Item Description,Quantity,Unit\n" + "".join(
    f"{i},{name},{qty},Nos\n" for i, (name, qty) in enumerate(zip(NAMES, [18, 5, 14, 3, 160]), 1))


def wrapper(path):
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((30, 40), "GeMARPTS\nTotal Quantity\n200\n" + "\n".join(NAMES))
        for y, uri in [(80, SPEC_URL), (110, BOQ_URL)]:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(30, y, 180, y+20), "uri": uri})
    doc.save(path)
    doc.close()
    return path


class Response:
    def __init__(self, content, status=200, headers=None):
        self.content, self.status_code, self.headers = content, status, headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def raise_for_status(self):
        assert self.status_code == 200
    def iter_content(self, size):
        yield self.content


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])


def test_discovery_deduplicates_repeated_links(tmp_path):
    detected, links = bundle.discover_attachments(wrapper(tmp_path / "wrapper.pdf"))
    assert detected and len(links) == 2
    assert all(link["wrapper_pages"] == [1, 2] for link in links)


SPEC_DOC_URL = ("https://mkp.gem.gov.in/catalog_data/catalog_support_document/buyer_documents/"
                "280820/54/78/703/CatalogAttrs/SpecificationDocument/2026/10/24/rfq_specification.pdf")
ATC_DOC_URL = ("https://fulfilment.gem.gov.in/contract/slafds?fileDownloadPath=SLA_UPLOAD_PATH/"
               "2026/Oct/GEM_2026_B_8197773/CLM0010/ATC_terms.pdf")
PRINTED_BOQ_URL = "https://mkp.gem.gov.in/BoqLineItemsDocument/items.csv"


def test_discovery_reads_specification_links_and_printed_urls(tmp_path):
    path = tmp_path / "wrapper.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((30, 40), "Specification Document")
    page.insert_text((30, 80), "ATC Document")
    page.insert_text((30, 120), PRINTED_BOQ_URL)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(30, 40, 220, 55), "uri": SPEC_DOC_URL})
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(30, 80, 220, 95), "uri": ATC_DOC_URL})
    doc.save(path)
    doc.close()

    detected, links = bundle.discover_attachments(path)
    roles = {link["url"]: link["role"] for link in links}
    assert detected
    assert roles[SPEC_DOC_URL] == "specification"
    assert roles[ATC_DOC_URL] == "atc"
    assert roles[PRINTED_BOQ_URL] == "boq"


def test_download_uses_browser_user_agent(tmp_path, monkeypatch, public_dns):
    seen = {}

    def get(url, **kwargs):
        seen.update(kwargs.get("headers") or {})
        return Response(b"%PDF-1.4 test")

    monkeypatch.setattr(bundle.requests, "get", get)
    bundle._fetch(SPEC_URL, tmp_path / "spec.pdf", "specification")
    assert "Mozilla" in seen.get("User-Agent", "")
    assert seen.get("Referer", "").startswith("https://")


def test_wrapper_schedule_recovers_item_quantities():
    pages = [
        {"page": 1, "document": "wrapper.pdf", "text": (
            "Total Quantity\n29\nItem Category\n"
            "A Valve , B Valve , C Valve , D Valve\nGeMARPTS\n")},
        {"page": 5, "document": "wrapper.pdf", "text": (
            "Evaluation Schedules\nSchedule 1\nA Valve\n8\nSchedule 2\nB Valve\n6\n"
            "Schedule 3\nC Valve\n10\nSchedule 4\nD Valve\n5\n")},
    ]
    items = bundle.read_wrapper_items(pages)
    assert [item["description"] for item in items] == ["A Valve", "B Valve", "C Valve", "D Valve"]
    assert [item["quantity"] for item in items] == ["8", "6", "10", "5"]
    assert sum(int(item["quantity"]) for item in items) == 29
    assert all(item["source_type"] == "wrapper" and item["source"]["page"] == 5 for item in items)

    pages[0]["text"] = pages[0]["text"].replace("29", "30")
    assert bundle.read_wrapper_items(pages) == []


def test_download_rejects_html_and_cleans_partial(tmp_path, monkeypatch, public_dns):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return Response(b"<html><title>Request Rejected</title></html>")
    monkeypatch.setattr(bundle.requests, "get", get)
    state = bundle.prepare_bundle(wrapper(tmp_path / "wrapper.pdf"), tmp_path / "attachments")
    assert not state["complete"] and len(calls) == 2
    assert all(a["status"] == "missing" for a in state["attachments"])
    assert {entry["role"] for entry in state["manual_required"]} == {"specification", "boq"}
    assert all(entry["reason"] for entry in state["manual_required"])
    assert not list((tmp_path / "attachments").glob("*.part"))


@pytest.mark.parametrize("url", ["http://mkp.gem.gov.in/a.pdf", "https://gem.gov.in.evil.test/a.pdf",
                                    "https://user:password@mkp.gem.gov.in/a.pdf", "https://mkp.gem.gov.in:8443/a.pdf"])
def test_rejects_nonofficial_or_credential_urls(url):
    with pytest.raises(ValueError):
        bundle._checked_url(url)


def test_rejects_private_dns_and_redirect(tmp_path, monkeypatch, public_dns):
    monkeypatch.setattr(bundle.requests, "get", lambda *a, **kw: Response(b"", 302, {"Location": "https://127.0.0.1/private"}))
    with pytest.raises(ValueError):
        bundle._fetch(SPEC_URL, tmp_path / "spec.pdf", "specification")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="public"):
        bundle._checked_url(SPEC_URL)


def test_cache_reuses_verified_files_without_network(tmp_path, monkeypatch, public_dns):
    monkeypatch.setattr(bundle.requests, "get", lambda url, **kw: Response(b"%PDF-1.4 test" if url == SPEC_URL else CSV.encode()))
    source = wrapper(tmp_path / "wrapper.pdf")
    first = bundle.prepare_bundle(source, tmp_path / "attachments")
    def offline(*args, **kwargs):
        raise AssertionError("Verified cached attachments must not need DNS or HTTP")
    monkeypatch.setattr(socket, "getaddrinfo", offline)
    monkeypatch.setattr(bundle.requests, "get", offline)
    second = bundle.prepare_bundle(source, tmp_path / "attachments")
    assert first["complete"] and second["complete"]
    assert first["attachments"] == second["attachments"]


def test_boq_preserves_item_quantity_units_and_csv_rows(tmp_path):
    path = tmp_path / "items.csv"
    path.write_text(CSV, encoding="utf-8-sig")
    items = bundle.read_boq(path)
    assert [item["description"] for item in items] == NAMES
    assert [item["quantity"] for item in items] == ["18", "5", "14", "3", "160"]
    assert sum(Decimal(item["quantity"]) for item in items) == 200
    assert [item["source"]["row"] for item in items] == [2, 3, 4, 5, 6]
    assert all(item["unit"] == "Nos" and item["source"]["document"] == "items.csv" for item in items)


def test_supplied_files_work_without_geM_network_access(tmp_path, monkeypatch):
    spec, boq = tmp_path / "supplied.pdf", tmp_path / "supplied.csv"
    spec.write_bytes(b"%PDF-1.4 supplied file")
    boq.write_text(CSV, encoding="utf-8")
    def never(*args, **kwargs):
        raise AssertionError("Supplied files must not need HTTP or DNS")
    monkeypatch.setattr(bundle.requests, "get", never)
    monkeypatch.setattr(socket, "getaddrinfo", never)
    state = bundle.prepare_bundle(wrapper(tmp_path / "wrapper.pdf"), tmp_path / "attachments",
                                  local_files={"specification": spec, "boq": boq})
    assert state["complete"]
    assert all(a["acquisition"] == "user_provided" for a in state["attachments"])
    assert {a["provided_name"] for a in state["attachments"]} == {"supplied.pdf", "supplied.csv"}


def test_no_boq_link_uses_wrapper_items_without_manual_request(tmp_path, monkeypatch, public_dns):
    from tender_extraction import ingestion, table_extraction

    path = tmp_path / "wrapper.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((30, 40), (
        "GeMARPTS\nTotal Quantity\n29\nItem Category\n"
        "A Valve , B Valve , C Valve , D Valve\nGeMARPTS\n"
        "Evaluation Schedules\nSchedule 1\nA Valve\n8\nSchedule 2\nB Valve\n6\n"
        "Schedule 3\nC Valve\n10\nSchedule 4\nD Valve\n5\n"))
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(30, 260, 200, 275),
                      "uri": SPEC_DOC_URL})
    doc.save(path)
    doc.close()

    monkeypatch.setattr(bundle.requests, "get", lambda url, **kw: Response(b"%PDF-1.4 test"))
    state = bundle.prepare_bundle(path, tmp_path / "attachments")
    assert state["complete"] and not state["boq_referenced"]
    assert state["manual_required"] == []

    monkeypatch.setattr(ingestion, "ingest_document",
                        lambda path: [{"page": 1, "text": "A Valve 8 bar", "is_ocr": False, "headings": []}])
    monkeypatch.setattr(table_extraction, "extract_tables", lambda *a, **kw: [])
    pages, tables, items = bundle.load_bundle_sources(path, state)
    assert [item["quantity"] for item in items] == ["8", "6", "10", "5"]
    assert sum(int(item["quantity"]) for item in items) == 29
    assert state["quantity_check"]["matches"] and state["complete"]
    assert not any(entry["role"] == "boq" for entry in state["manual_required"])


def test_quantity_mismatch_prevents_extraction(tmp_path, monkeypatch, public_dns):
    from tender_extraction import ingestion, table_extraction, extraction
    wrong_csv = CSV.replace("Water,18,", "Water,19,")
    monkeypatch.setattr(bundle.requests, "get", lambda url, **kw: Response(b"%PDF-1.4 test" if url == SPEC_URL else wrong_csv.encode()))
    source = wrapper(tmp_path / "wrapper.pdf")
    state = bundle.prepare_bundle(source, tmp_path / "attachments")
    monkeypatch.setattr(ingestion, "ingest_document", lambda path: [{"page": 1, "text": "Product data", "is_ocr": False}])
    monkeypatch.setattr(table_extraction, "extract_tables", lambda *a, **kw: [])
    def never(*args, **kwargs):
        raise AssertionError("Do not extract or score a bundle with inconsistent BOQ quantities")
    monkeypatch.setattr(extraction, "extract_specifications", never)
    result = bundle.extract_bundle(source, state)
    assert result["technical_specifications"] == []
    assert result["quantity_check"] == {"expected": "200", "actual": "201", "matches": False}


def test_single_main_product_absorbs_a_compatible_spec():
    state = {"complete": True, "missing": [], "attachments": []}
    items = [{"item_id": "1", "description": "Drain Valve", "quantity": "5", "unit": "Nos"}]
    result = bundle.finish_bundle_result(
        {"technical_specifications": [{"component": "Valve", "specs": {"pressure": "16 bar"}}]},
        state, items, [],
    )
    assert result["technical_specifications"][0]["boq_item_id"] == "1"
    assert "tender_complete" not in result
    assert "matching_blocked" not in result


def test_shared_headers_do_not_remove_different_boq_rows():
    from tender_extraction.extraction import _deduplicate_chunks, _build_context
    from tender_extraction.schemas import Chunk, ChunkMetadata
    prefix = "Repeated BOQ header " * 20
    hits = [{"chunk": Chunk(chunk_id=f"boq:{row}", text=prefix + text,
                            metadata=ChunkMetadata(document="boq.csv", role="boq", row=row)), "score": 1}
            for row, text in [(2, "Tank quantity 18"), (3, "Valve quantity 5")]]
    assert len(_deduplicate_chunks(hits)) == 2
    context = _build_context(hits)
    assert "CSV row=2" in context and "CSV row=3" in context and "document=boq.csv" in context


@pytest.mark.parametrize("text", ["Description,Quantity,Unit\nValve,NaN,Nos\n",
                                      "Description,Quantity,Unit\nValve,-1,Nos\n",
                                      "Description,Quantity,Unit\nValve,5\n",
                                      "Description,Quantity\nValve,5\n",
                                      "Description,Quantity,Unit\nValve,5,\n"])
def test_invalid_boq_never_guesses_columns_or_quantities(tmp_path, text):
    path = tmp_path / "bad.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        bundle.read_boq(path)


def test_complete_bundle_uses_specifications_and_boq_authority(tmp_path, monkeypatch, public_dns):
    from tender_extraction import ingestion, table_extraction, extraction
    monkeypatch.setattr(bundle.requests, "get", lambda url, **kw: Response(b"%PDF-1.4 test" if url == SPEC_URL else CSV.encode()))
    source = wrapper(tmp_path / "wrapper.pdf")
    state = bundle.prepare_bundle(source, tmp_path / "attachments")
    text = "\n".join(f"{name}: material SS316." for name in NAMES)
    monkeypatch.setattr(ingestion, "ingest_document", lambda path: [{"page": 1, "text": text, "is_ocr": False, "headings": []}])
    monkeypatch.setattr(table_extraction, "extract_tables", lambda *a, **kw: [])
    def specs(hits, topic):
        assert all(h["chunk"].metadata.role == "specification" for h in hits)
        return [{"component": name, "specs": {"material": "SS316"},
                 "source": {"page": 1, "exact_text": f"{name}: material SS316."}} for name in NAMES]
    monkeypatch.setattr(extraction, "extract_specifications", specs)
    monkeypatch.setattr(extraction, "extract_scope_of_work", lambda hits, topic: {"deliverables": ["Valve Drain (quantity 18)"]})
    result = bundle.extract_bundle(source, state, str(tmp_path / "result.json"))
    assert result["quantity_check"]["matches"]
    assert result["reconciliation"]["status"] == "ready"
    assert len(result["procurement_items"]) == 5
    assert [spec["boq_item_id"] for spec in result["technical_specifications"]] == ["1", "2", "3", "4", "5"]
    assert all(spec["source"]["document"].endswith("specification.pdf") for spec in result["technical_specifications"])
    assert "Valve Drain (quantity 18)" not in result["scope_of_work"]["deliverables"]
    assert any("Valve Drain: 5 Nos" in clause for clause in result["scope_of_work"]["deliverables"])
    saved = json.loads((tmp_path / "result_chunks.json").read_text(encoding="utf-8"))
    assert any(c["metadata"]["role"] == "boq" and c["metadata"]["row"] == 2 for c in saved)


def test_missing_attachments_skip_llm_and_block_all_matching(tmp_path, monkeypatch, public_dns):
    from tender_extraction import extraction, matching, scoring
    from api import matching_adapter
    monkeypatch.setattr(bundle.requests, "get", lambda *a, **kw: Response(b"<html>Request Rejected</html>"))
    source = wrapper(tmp_path / "wrapper.pdf")
    state = bundle.prepare_bundle(source, tmp_path / "attachments")
    def never(*args, **kwargs):
        raise AssertionError("Incomplete tenders must not invoke the LLM or manufacturer collector")
    monkeypatch.setattr(extraction, "extract_specifications", never)
    monkeypatch.setattr(extraction, "extract_scope_of_work", never)
    result = bundle.extract_bundle(source, state)
    assert result["technical_specifications"] == []
    assert result["quantity_check"]["actual"] is None
    with pytest.raises(ValueError, match="No requirements could be extracted"):
        matching.generate_match_report(result, corpus_indexer=never)
    monkeypatch.setattr(matching_adapter, "_engine_module", never)
    assert matching_adapter.generate_match_report(result)[0] is None
    monkeypatch.setattr(scoring, "load_model", never)
    score = scoring.score_tender_match({}, result)
    assert "error" in score and "match_score" not in score


def test_bundle_citations_cannot_ground_against_another_file_or_row():
    from tender_extraction.schemas import Chunk, ChunkMetadata
    from tender_extraction.validation import verify_grounding
    hits = [{"chunk": Chunk(chunk_id="a:2", text="Pressure 16 bar.", metadata=ChunkMetadata(document="a.pdf", page=2))}]
    spec = {"component": "Valve", "specs": {"pressure": "16 bar"},
            "source": {"document": "other.pdf", "page": 2, "exact_text": "Pressure 16 bar."}}
    assert verify_grounding(spec, hits) == 0
    spec["source"]["document"] = "a.pdf"
    assert verify_grounding(spec, hits) == 1
    spec["source"]["row"] = 99
    assert verify_grounding(spec, hits) == 0


def test_empty_bundle_metadata_does_not_invalidate_manufacturer_indexes(tmp_path):
    from tender_extraction.schemas import Chunk, ChunkMetadata
    from tender_extraction.retrieval import HybridRetriever
    chunk = Chunk(chunk_id="existing", text="Valve pressure 16 bar.", metadata=ChunkMetadata(page=2))
    legacy = {key: value for key, value in chunk.metadata.model_dump().items() if key not in {"document", "role", "row"}}
    expected = hashlib.sha256(json.dumps([{"chunk_id": chunk.chunk_id, "text": chunk.text, "metadata": legacy}], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    retriever = HybridRetriever(persist_dir=":memory:")
    try:
        assert retriever._fingerprint_chunks([chunk]) == expected
    finally:
        retriever.close()
