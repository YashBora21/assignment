"""Product rows retain model/spec alignment and original review evidence."""

from tender_extraction.manufacturer_tables import normalize_product_table


def test_headers_units_spans_and_rejected_rows():
    table = {
        "table_id": "t1", "page": 7,
        "raw": [["Model", "Power", "Pressure"], [None, "kW", "bar"],
                ["M100", "10", "16"], [None, "12", "20"],
                ["", "11", "18"], ["M200", "5"]],
        "cells": [[(0, 0, 1, 2), (1, 0, 2, 1), (2, 0, 3, 1)],
                  [None, (1, 1, 2, 2), (2, 1, 3, 2)],
                  [(0, 2, 1, 4), (1, 2, 2, 3), (2, 2, 3, 3)],
                  [None, (1, 3, 2, 4), (2, 3, 3, 4)],
                  [(0, 4, 1, 5), (1, 4, 2, 5), (2, 4, 3, 5)]],
    }
    normalized = normalize_product_table(table)
    assert normalized["headers"] == ["Model", "Power / kW", "Pressure / bar"]
    assert normalized["rows"] == [["M100", "10 kW", "16 bar"], ["M100", "12 kW", "20 bar"]]
    assert normalized["products"][1]["model"] == "M100"
    assert [r["reason"] for r in normalized["rejected"]] == ["missing model", "misaligned column count"]
    assert normalized["raw"] == table["raw"]


def test_blank_models_are_not_guessed_from_previous_rows():
    normalized = normalize_product_table({"headers": ["Model", "Flow (m3/h)"],
                                          "rows": [["A1", "15"], [None, "20"]]})
    assert normalized["rows"] == [["A1", "15 m³/h"]]
    assert normalized["rejected"][0]["reason"] == "missing model"


def test_flow_unit_subheader_is_not_mistaken_for_numeric_data():
    normalized = normalize_product_table({"raw": [["Model", "Flow"], [None, "m3/h"], ["A1", "15"]]})
    assert normalized["rows"] == [["A1", "15 m³/h"]]


def test_no_model_column_rejects_table():
    table = normalize_product_table({"headers": ["Parameter", "Value"], "rows": [["Pressure", "16 bar"]]})
    assert not table["products"]
    assert table["rejected"][0]["reason"] == "missing or ambiguous model column"


def test_technical_type_is_not_a_product_identifier():
    table = normalize_product_table({"headers": ["Type of lubrication", "Region"],
                                     "rows": [["Grease", "Europe"], ["Oil", "Asia"]]})
    assert not table["products"]


def test_horizontal_merged_headers_are_flattened_from_geometry():
    table = normalize_product_table({
        "raw": [["Model", "Performance", None], [None, "Power (kW)", "Pressure (bar)"], ["A1", "5", "16"]],
        "cells": [[(0, 0, 1, 2), (1, 0, 3, 1), None],
                  [None, (1, 1, 2, 2), (2, 1, 3, 2)],
                  [(0, 2, 1, 3), (1, 2, 2, 3), (2, 2, 3, 3)]],
    })
    assert table["headers"] == ["Model", "Performance / Power (kW)", "Performance / Pressure (bar)"]
    assert table["rows"] == [["A1", "5 kW", "16 bar"]]


def test_real_pdf_merged_model_cells(tmp_path):
    import pymupdf
    from tender_extraction.table_extraction import extract_tables

    pdf_path = tmp_path / "products.pdf"
    document = pymupdf.open()
    page = document.new_page(width=400, height=250)
    for x in (30, 130, 230, 330):
        page.draw_line((x, 30), (x, 150))
    for y in (30, 70, 150):
        page.draw_line((30, y), (330, y))
    page.draw_line((130, 110), (330, 110))
    for x, text in ((40, "Model"), (140, "Power (kW)"), (240, "Pressure (bar)")):
        page.insert_text((x, 55), text, fontsize=9)
    for x, y, text in ((40, 95, "A1"), (140, 95, "5"), (240, 95, "16"),
                       (140, 135, "7"), (240, 135, "20")):
        page.insert_text((x, y), text, fontsize=9)
    document.save(pdf_path)
    document.close()
    tables = extract_tables(str(pdf_path), preserve_geometry=True)
    products = [normalize_product_table(table) for table in tables]
    assert any(table["rows"] == [["A1", "5 kW", "16 bar"], ["A1", "7 kW", "20 bar"]]
               for table in products)
