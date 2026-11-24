import json
import logging

import pymupdf
import pytest

from tender_extraction import PIPELINE_VERSION, tender_bundle as bundle
from tender_extraction.schemas import ExtractionResult


def test_view_links_classified_by_separate_rows(tmp_path, caplog):
    path = tmp_path / 'wrapper.pdf'
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((30, 30), 'GeMARPTS')
    labels = ['BOQ Item list', 'BOQ Checklist', 'Specification', 'ATC']
    for i, label in enumerate(labels):
        y = 70 + i * 35
        page.insert_text((30, y), label)
        page.insert_text((230, y), 'View')
        page.insert_link({'kind': pymupdf.LINK_URI, 'from': pymupdf.Rect(228, y-12, 260, y+3),
                          'uri': f'https://bidplus.gem.gov.in/bidding/bid/documentdownload/123/{i}.pdf'})
    doc.save(path)
    doc.close()
    with caplog.at_level(logging.INFO):
        detected, links = bundle.discover_attachments(path)
    assert detected
    assert [link['role'] for link in links] == ['boq', 'boq', 'specification', 'atc']
    assert [link['document_type'] for link in links][:2] == ['boq', 'boq_checklist']
    assert all(link['label'] == 'View' for link in links)
    for label, link in zip(labels, links):
        assert label in link['row_text']
        assert label in caplog.text


def test_comma_description_keeps_four_schedules():
    text = ('Total Quantity\n16\nItem Category\n'
            'Gem Schedule-1 MAIN INLET VALVE DIAMETER 200 MM, STROKE-769 MM , '
            'Gem Schedule-2 SPARES , Gem Schedule-3 TOOLS , Gem Schedule-4 PAINT\nGeMARPTS\n'
            'Gem Schedule-1 MAIN INLET VALVE DIAMETER 200 MM, STROKE-769 MM\n(6 pieces)\n'
            'Gem Schedule-2 SPARES\n(2 set)\nGem Schedule-3 TOOLS\n(2 set)\n'
            'Gem Schedule-4 PAINT\n(6 litre)\n')
    issues = []
    items = bundle.read_wrapper_items([{'text': text, 'page': 1, 'document': 'wrapper.pdf'}], issues)
    assert not issues
    assert len(items) == 4
    assert '200 MM, STROKE-769 MM' in items[0]['description']
    assert [i['quantity'] for i in items] == ['6', '2', '2', '6']
    issues = []
    assert bundle.read_wrapper_items([{'text': text.replace('(6 litre)', ''), 'page': 1}], issues) == []
    assert issues


def test_parameters_resolve_only_unique_parent_and_ignore_claimed_id():
    items = [{'item_id': '1', 'description': 'Cylinder diameter 200 mm stroke 769 mm'},
             {'item_id': '2', 'description': 'Cylinder diameter 200 mm stroke 500 mm'}]
    specs = [{'component': 'Piston', 'specs': {'diameter': '200 mm'}, 'boq_item_id': '1'},
             {'component': 'Piston', 'specs': {'diameter': '200 mm', 'stroke': '769 mm'}},
             {'component': 'Piston', 'specs': {'diameter': '300 mm'}}]
    bundle.assign_spec_items(specs, [], items)
    assert [s['boq_item_id'] for s in specs] == ['', '1', '']
    assert [s['association_status'] for s in specs] == ['review', 'matched', 'review']
    parsed = ExtractionResult(technical_specifications=specs).model_dump()
    assert parsed['technical_specifications'][0]['association_status'] == 'review'


def test_pdf_boq_reads_table_and_preserves_units(tmp_path):
    path = tmp_path / 'boq.pdf'
    doc = pymupdf.open()
    page = doc.new_page()
    xs, ys = [30, 100, 350, 420, 500], [40, 70, 100]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    for row, values in enumerate([['Item No', 'BOQ Item Description', 'Quantity', 'Unit'], ['1', 'Cylinder diameter 200 mm, stroke 769 mm', '6', 'Nos']]):
        for col, value in enumerate(values):
            page.insert_text((xs[col]+3, ys[row]+18), value, fontsize=8)
    doc.save(path)
    doc.close()
    items = bundle.read_boq(path)
    assert len(items) == 1
    assert items[0]['quantity'] == '6' and items[0]['unit'] == 'Nos'
    assert items[0]['source']['page'] == 1
    assert ', stroke 769 mm' in items[0]['description']


def test_old_schema_is_not_stamped_current():
    assert ExtractionResult.model_validate({}).pipeline_version == ''
    assert ExtractionResult(pipeline_version=PIPELINE_VERSION).pipeline_version == PIPELINE_VERSION


def test_empty_output_stamps_version_and_reports_no_requirements(tmp_path):
    from tender_extraction.main import _empty_result
    path = tmp_path / 'result.json'
    result = _empty_result(str(path))
    assert result['pipeline_version'] == PIPELINE_VERSION
    assert result['tender_status'] == 'no_requirements'
    assert 'tender_complete' not in result and 'matching_blocked' not in result
    assert json.loads(path.read_text()) == result
