"""Resolve a GeM wrapper's specification/BOQ files with local provenance."""
from __future__ import annotations

import hashlib
import csv
import ipaddress
import io
import json
import logging
import re
import socket
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests

from tender_extraction.config import config
from tender_extraction.categories import build_vocabulary, classify_text

logger = logging.getLogger(__name__)

_CATEGORY_VOCABULARY = None


def _category_vocabulary():
    """Manifest-derived product vocabulary, cached for the process."""
    global _CATEGORY_VOCABULARY
    if _CATEGORY_VOCABULARY is None:
        import os

        root = os.getenv("MANUFACTURERS_DIR") or str(
            Path(__file__).resolve().parent.parent / "data" / "manufacturers"
        )
        _CATEGORY_VOCABULARY = build_vocabulary(root)
    return _CATEGORY_VOCABULARY


def _checked_url(url: str, resolve: bool = True) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not (host == "gem.gov.in" or host.endswith(".gem.gov.in"))):
        raise ValueError("Attachments must use official GeM HTTPS URLs")
    if resolve:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
            raise ValueError("Attachment host does not resolve exclusively to public addresses")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


_SPEC_PATTERN = re.compile(r"specification|rfq[_\s-]*spec|\bspec\b", re.I)
_BOQ_PATTERN = re.compile(r"\bboq\b|line[\s_-]*item|price[\s_-]*schedule|item[\s_-]*rate", re.I)
_ATC_PATTERN = re.compile(r"\batc\b|additional\s+terms|bid[\s_-]*specific", re.I)
_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)
_ROLE_PRIORITY = {"atc": 1, "specification": 2, "boq": 3}
# GeM's WAF rejects the default python-requests User-Agent with an HTML
# "Request Rejected" page. A normal browser UA returns the real attachment.
_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "application/pdf,text/csv,*/*",
    "Referer": "https://bidplus.gem.gov.in/",
}


def _official(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "gem.gov.in" or host.endswith(".gem.gov.in"))


def _classify_attachment(url: str, label: str) -> tuple[str, str] | None:
    """Identify the document from its link label and URL path.

    Content validation happens after download; this only decides what to fetch.
    """
    blob = f"{label} {unquote(url)}"
    if _BOQ_PATTERN.search(blob):
        return "boq", " ".join(label.split())
    if _SPEC_PATTERN.search(blob):
        return "specification", " ".join(label.split())
    if _ATC_PATTERN.search(blob):
        return "atc", " ".join(label.split())
    return None


def _link_row_text(page, rect, tables) -> str:
    import pymupdf

    center = (rect.tl + rect.br) / 2
    for table in tables:
        for row in table.rows:
            if pymupdf.Rect(row.bbox).contains(center):
                return " ".join(page.get_textbox(pymupdf.Rect(row.bbox)).split())
    line = pymupdf.Rect(page.rect.x0, rect.y0 - 2, page.rect.x1, rect.y1 + 2)
    return " ".join(page.get_textbox(line).split())


def discover_attachments(path: Path) -> tuple[bool, list[dict]]:
    """Collect official attachment links from hyperlinks *and* printed URLs."""
    if path.suffix.lower() != ".pdf":
        return False, []
    import pymupdf
    links = {}
    wrapper = False
    try:
        pdf = pymupdf.open(path)
    except (RuntimeError, ValueError):
        return False, []  # The ordinary ingestion path reports corrupt PDFs.
    with pdf:
        for number, page in enumerate(pdf, start=1):
            text = page.get_text()
            wrapper |= "GeMARPTS" in text or bool(re.search(r"Specification\s+Document", text) and re.search(r"BOQ\s+Detail\s+Document", text))
            candidates: list[tuple[str, str, str]] = []
            page_links = page.get_links()
            tables = page.find_tables().tables if page_links else []
            for link in page_links:
                url = (link.get("uri") or "").strip()
                if not url:
                    continue
                rect = link.get("from")
                label = " ".join(page.get_textbox(rect).split()) if rect is not None else ""
                row_text = _link_row_text(page, rect, tables) if rect is not None else label
                candidates.append((url, label, row_text))
            for match in _URL_RE.finditer(text):
                url = match.group(0).rstrip(".,;:")
                start = text.rfind("\n", 0, match.start()) + 1
                end = text.find("\n", match.end())
                label = text[start:end if end != -1 else len(text)].strip()
                candidates.append((url, label, label))
            for url, label, row_text in candidates:
                logger.info("Discovered link document=%s page=%s url=%r row_text=%r", path.name, number, url, row_text)
                if not _official(url):
                    continue
                classified = _classify_attachment(url, row_text)
                if classified is None:
                    continue
                role, _ = classified
                parsed = urlsplit(url)
                url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
                record = links.get(url)
                if record is None:
                    record = {"role": role, "url": url, "label": label, "row_text": row_text,
                              "document_type": "boq_checklist" if re.search(r"boq\s+checklist", row_text, re.I) else role,
                              "wrapper_pages": []}
                    links[url] = record
                elif _ROLE_PRIORITY.get(role, 0) > _ROLE_PRIORITY.get(record["role"], 0):
                    record["role"] = role
                if number not in record["wrapper_pages"]:
                    record["wrapper_pages"].append(number)
                wrapper = True
    return wrapper, list(links.values())


def _validate_attachment(path: Path, role: str) -> None:
    size = path.stat().st_size
    if not 0 < size <= config.max_file_size_mb * 1024 * 1024:
        raise ValueError(f"Attachment is empty or exceeds {config.max_file_size_mb} MB")
    with path.open("rb") as stream:
        prefix = stream.read(1024)
    if prefix.lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("Server returned an HTML response instead of an attachment (GeM may have rejected access)")
    if role in ("specification", "atc") and b"%PDF-" not in prefix:
        raise ValueError(f"{role.title()} attachment is not a PDF")
    if role == "boq" and b"%PDF-" not in prefix and (b"\x00" in prefix or prefix.startswith(b"PK")):
        raise ValueError("Only PDF or text CSV BOQs are supported")


def _fetch(url: str, target: Path, role: str) -> str:
    limit = config.max_file_size_mb * 1024 * 1024
    partial = target.with_suffix(target.suffix + ".part")
    try:
        for _ in range(6):
            url = _checked_url(url)
            with requests.get(url, headers=_REQUEST_HEADERS, stream=True,
                              timeout=(5, 30), allow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                size = 0
                digest = hashlib.sha256()
                with partial.open("wb") as stream:
                    for block in response.iter_content(65536):
                        size += len(block)
                        if size > limit:
                            raise ValueError(f"Attachment exceeds {config.max_file_size_mb} MB")
                        stream.write(block)
                        digest.update(block)
                _validate_attachment(partial, role)
                partial.replace(target)
                return digest.hexdigest()
        raise ValueError("Too many attachment redirects")
    finally:
        partial.unlink(missing_ok=True)


def prepare_bundle(wrapper: str | Path, directory: str | Path, local_files: dict | None = None) -> dict:
    path = Path(wrapper)
    detected, references = discover_attachments(path)
    with path.open("rb") as stream:
        wrapper_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    local_files = {str(role): str(file) for role, file in (local_files or {}).items() if role in ("specification", "boq", "atc")}

    # A manually supplied file is authoritative for its role. Add roles the
    # wrapper did not link (e.g. no BOQ link) and ignore duplicate remote links
    # for a role that was supplied locally.
    if local_files:
        for role in local_files:
            if not any(reference["role"] == role for reference in references):
                references.append({"role": role, "url": "", "label": Path(local_files[role]).name,
                                   "wrapper_pages": [], "user_supplied": True})
        seen_roles = set()
        collapsed = []
        for reference in references:
            if reference["role"] in local_files and not reference.get("user_supplied"):
                if reference["role"] in seen_roles:
                    continue
                seen_roles.add(reference["role"])
            collapsed.append(reference)
        references = collapsed

    bundle = {"wrapper": path.name, "wrapper_sha256": wrapper_hash, "is_gem_wrapper": detected, "complete": True,
              "boq_referenced": any(item["role"] == "boq" for item in references),
              "attachments": [], "missing": [], "manual_required": [],
              "directory": str(Path(directory).resolve())}
    if not detected and not local_files:
        return bundle
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "bundle.json"
    previous = {}
    if manifest.exists():
        try:
            previous = {item["url"]: item for item in json.loads(manifest.read_text(encoding="utf-8"))["attachments"] if item.get("url")}
        except (ValueError, KeyError, TypeError):
            pass
    content_files = {}
    if len(references) > 16:
        bundle["missing"].append("More than 16 attachment links require manual review")
        references = references[:16]
    for reference in references:
        record = dict(reference)
        role, url = record["role"], record.get("url", "")
        extension = ".csv" if role == "boq" and not urlsplit(url or local_files[role]).path.lower().endswith(".pdf") else ".pdf"
        if url:
            name = re.sub(r"[^A-Za-z0-9._-]", "_", unquote(urlsplit(url).path.rsplit("/", 1)[-1]))[:110]
            name = f"{hashlib.sha256(url.encode()).hexdigest()[:12]}_{name}"
        else:
            name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(local_files[role]).name)[:110]
        if not name.lower().endswith(extension):
            name += extension
        cached = previous.get(url, {}) if url else {}
        cached_name = cached.get("file", "")
        if cached_name and Path(cached_name).name == cached_name and (root / cached_name).is_file():
            name = cached_name
        target = root / name
        record["file"] = name
        try:
            if role in local_files:
                import shutil
                supplied = Path(local_files[role])
                _validate_attachment(supplied, role)
                if supplied.resolve() != target.resolve():
                    partial = target.with_suffix(target.suffix + ".part")
                    try:
                        shutil.copyfile(supplied, partial)
                        _validate_attachment(partial, role)
                        partial.replace(target)
                    finally:
                        partial.unlink(missing_ok=True)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                record.update(acquisition="user_provided", provided_name=supplied.name)
            else:
                if not url:
                    raise ValueError("no source URL")
                _checked_url(url, resolve=False)
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == cached.get("sha256"):
                    _validate_attachment(target, role)
                    digest = cached["sha256"]
                    record.update({k: cached[k] for k in ("acquisition", "provided_name") if k in cached})
                else:
                    digest = _fetch(url, target, role)
                    record["acquisition"] = "download"
            if digest in content_files and content_files[digest] != name:
                target.unlink()
                record["file"] = content_files[digest]
            else:
                content_files[digest] = name
            record.update(status="ready", sha256=digest, size_bytes=(root / record["file"]).stat().st_size,
                          retrieved_at=(cached.get("retrieved_at") if cached.get("sha256") == digest else None)
                          or datetime.now(timezone.utc).isoformat())
        except (ValueError, OSError, requests.RequestException, KeyError) as exc:
            record.update(status="missing", error=str(exc))
            bundle["missing"].append(f"{role}: {exc}")
        bundle["attachments"].append(record)

    ready = {item["role"] for item in bundle["attachments"] if item["status"] == "ready"}
    if "specification" not in ready:
        bundle["missing"].append("specification: no usable attachment link found")
    if bundle["boq_referenced"] and "boq" not in ready:
        if not any(message.startswith("boq:") for message in bundle["missing"]):
            bundle["missing"].append("boq: referenced BOQ could not be read")
    elif not bundle["boq_referenced"]:
        bundle.setdefault("notes", []).append(
            "BOQ is not linked in the wrapper; the wrapper item list is used if it is unambiguous.")

    # Manual upload is requested only for a failed/blocked download or a role
    # with no usable link. A missing BOQ link alone is not a failure.
    for attachment in bundle["attachments"]:
        if attachment["status"] != "ready":
            bundle["manual_required"].append({
                "role": attachment["role"], "url": attachment.get("url", ""),
                "reason": attachment.get("error") or "download blocked",
            })
    if "specification" not in ready and not any(item["role"] == "specification" for item in bundle["manual_required"]):
        bundle["manual_required"].append({"role": "specification", "url": "",
                                          "reason": "no usable specification link in the wrapper"})
    if bundle["boq_referenced"] and "boq" not in ready and not any(item["role"] == "boq" for item in bundle["manual_required"]):
        bundle["manual_required"].append({"role": "boq", "url": "",
                                          "reason": "referenced BOQ could not be downloaded"})

    bundle["complete"] = not bundle["missing"]
    manifest.write_text(json.dumps({k: v for k, v in bundle.items() if k != "directory"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle


def _label(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def read_boq(path: Path) -> list[dict]:
    with path.open("rb") as stream:
        is_pdf = stream.read(5) == b"%PDF-"
    if not is_pdf:
        return _read_boq_text(path.read_text(encoding="utf-8-sig"), path.name)
    import pymupdf

    items = []
    with pymupdf.open(path) as pdf:
        for page_number, page in enumerate(pdf, 1):
            for table in page.find_tables().tables:
                rows = [[" ".join(page.get_textbox(pymupdf.Rect(cell)).split()) if cell else ""
                         for cell in row.cells] for row in table.rows]
                if not rows or not any(_label(cell) in {"description", "itemdescription", "boqitemdescription"} for cell in rows[0]):
                    continue
                text = io.StringIO()
                csv.writer(text).writerows(rows)
                parsed = _read_boq_text(text.getvalue(), path.name, page_number)
                for item in parsed:
                    item["source"]["exact_text"] = " | ".join(rows[item["source"]["row"] - 1])
                items.extend(parsed)
    if not items:
        raise ValueError("BOQ PDF has no readable table with description, quantity and unit columns")
    if len({item["item_id"] for item in items}) != len(items):
        raise ValueError("BOQ PDF has duplicate item identifiers")
    return items


def _read_boq_text(text: str, document: str, page: int = 0) -> list[dict]:
    if "\x00" in text or text.lstrip().lower().startswith(("<html", "<!doctype")):
        raise ValueError("BOQ must be a UTF-8 text CSV")
    try:
        dialect = csv.Sniffer().sniff(text[:16384], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    aliases = {
        "description": {"description", "itemdescription", "itemname", "nameofitem", "nameoftheitem", "itemcategory", "materialdescription", "productname", "boqitemdescription"},
        "quantity": {"quantity", "qty", "requiredquantity", "totalquantity", "boqitemquantity"},
        "unit": {"unit", "units", "uom", "unitofmeasurement", "unitofmeasure"},
        "item_id": {"sno", "srno", "serialno", "serialnumber", "itemno", "itemnumber", "lineitemno", "boqitemnumber"},
    }
    lines = text.splitlines(keepends=True)
    reader = csv.reader(lines, dialect)
    columns = None
    items = []
    ids = set()
    previous_line = 0
    for row in reader:
        line = previous_line + 1
        previous_line = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        labels = [_label(cell) for cell in row]
        mapping = {key: [i for i, label in enumerate(labels) if label in names] for key, names in aliases.items()}
        if all(len(mapping[key]) == 1 for key in ("description", "quantity", "unit")):
            if columns is None:
                columns = {key: indexes[0] for key, indexes in mapping.items() if len(indexes) == 1}
                width = len(row)
            continue
        if columns is None:
            if line > 50:
                break
            continue
        if len(row) != width:
            raise ValueError(f"BOQ row {line}: misaligned columns")
        description = row[columns["description"]].strip()
        if _label(description) in {"total", "grandtotal"}:
            continue
        quantity, unit = row[columns["quantity"]].strip(), row[columns["unit"]].strip()
        try:
            number = Decimal(quantity)
            if not number.is_finite() or number <= 0:
                raise InvalidOperation
        except InvalidOperation as exc:
            raise ValueError(f"BOQ row {line}: invalid quantity {quantity!r}") from exc
        if not description or not unit:
            raise ValueError(f"BOQ row {line}: description/unit is missing")
        item_id = row[columns["item_id"]].strip() if "item_id" in columns else str(line)
        if not item_id or item_id in ids:
            raise ValueError(f"BOQ row {line}: empty/duplicate item identifier")
        ids.add(item_id)
        raw = "".join(lines[line-1:reader.line_num]).rstrip("\r\n")
        items.append({"item_id": item_id, "description": description, "quantity": quantity, "unit": unit,
                      "source_type": "boq",
                      "source": {"document": document, "page": page, "row": line, "exact_text": raw}})
    if not items:
        raise ValueError("BOQ has no valid items with description, quantity, and unit columns")
    return items


def read_wrapper_items(pages: list[dict], issues: list[str] | None = None) -> list[dict]:
    issues = issues if issues is not None else []
    full_text = "\n".join(page.get("text") or "" for page in pages)
    expected_match = re.search(r"Total\s+Quantity\s*[:\r\n]+\s*([\d,.]+)", full_text, re.I)
    expected = Decimal(expected_match.group(1).replace(",", "")) if expected_match else None
    category = re.search(r"Item\s+Category\s*\n(.*?)\n\s*GeMARPTS", full_text, re.S | re.I)
    rows = []
    schedule_count = 0
    for page in pages:
        text = page.get("text") or ""
        schedule_count += len(re.findall(r"(?im)^Schedule\s+\d+\s*$", text))
        for match in re.finditer(
            r"(?im)^Schedule\s+(\d+)\s*\n((?:(?!^Schedule\s+\d+\s*$)[^\n]+\n)+?)(\d+(?:\.\d+)?)\s*(?=\n|$)", text
        ):
            rows.append({"item_id": match[1], "description": " ".join(match[2].split()),
                         "quantity": match[3], "unit": "Nos", "inferred_unit": True,
                         "page": page.get("page", 0), "exact_text": match[0]})
    if schedule_count and len(rows) != schedule_count:
        issues.append(f"wrapper: {schedule_count} schedules but {len(rows)} complete item/quantity rows")
        return []

    # Numbered product headings retain commas and units even across page breaks.
    if not schedule_count:
        for page in pages:
            for match in re.finditer(
                r"(?im)^((?:Gem\s+)?Schedule\s*[- ]\s*(\d+)\s+[^\n]+(?:\n[^\n]+){0,5}?)"
                r"\s*\(\s*(\d+(?:\.\d+)?)\s+(pieces?|nos?|sets?|lit(?:er|re)s?|kg|m)\s*\)",
                page.get("text") or "",
            ):
                rows.append({"item_id": match[2], "description": " ".join(match[1].split()),
                             "quantity": match[3], "unit": match[4], "inferred_unit": False,
                             "page": page.get("page", 0), "exact_text": match[0]})
        declared = set(re.findall(r"(?:Gem\s+)?Schedule\s*[- ]\s*(\d+)", category[1], re.I)) if category else set()
        if declared and declared != {row["item_id"] for row in rows}:
            issues.append(f"wrapper: {len(declared)} declared schedules but {len(rows)} complete item/quantity rows")
            return []

    if not rows and category:
        # GeM uses space-comma-space between categories, unlike commas inside dimensions.
        names = [" ".join(part.split()) for part in re.split(r"\s+,\s+", category[1])]
        quantities = []
        for page in pages:
            text = page.get("text") or ""
            if "Consignees/Reporting Officer and Quantity" not in text:
                continue
            lines = [" ".join(line.split()) for line in text.splitlines()]
            for index, line in enumerate(lines):
                if line != "1":
                    continue
                numbers = [entry for entry in lines[index + 1:index + 8] if re.fullmatch(r"\d{1,6}", entry)]
                if len(numbers) >= 2 and Decimal(numbers[0]) > 0 and int(numbers[1]) <= 365:
                    quantities.append((numbers[0], page.get("page", 0), "\n".join(lines[index:index + 8])))
        if len(names) != len(quantities):
            issues.append(f"wrapper: {len(names)} item names but {len(quantities)} quantity rows; no assignment made")
            return []
        rows = [{"item_id": str(i), "description": name, "quantity": qty, "unit": "Nos", "inferred_unit": True,
                 "page": page, "exact_text": quote}
                for i, (name, (qty, page, quote)) in enumerate(zip(names, quantities), 1)]

    if not rows:
        if expected is None or schedule_count or re.search(r"Schedule\s*[- ]\s*\d+|BOQ\s+Item\s+list", full_text, re.I):
            issues.append("wrapper: item list is missing or could not be read")
            return []
        rows = [{"item_id": "wrapper-total", "description": "Unspecified wrapper procurement",
                 "quantity": str(expected), "unit": "", "inferred_unit": True, "synthetic": True,
                 "page": next(p.get("page", 0) for p in pages if expected_match[0] in p.get("text", "")),
                 "exact_text": expected_match[0]}]
        issues.append("wrapper: synthetic total-only item requires product identity and unit review")

    if len({row["item_id"] for row in rows}) != len(rows) or any(Decimal(row["quantity"]) <= 0 for row in rows):
        issues.append("wrapper: duplicate schedules or invalid quantities")
        return []
    if expected is not None and sum((Decimal(row["quantity"]) for row in rows), Decimal(0)) != expected:
        issues.append("wrapper: item quantity total differs from the wrapper")
        return []
    wrapper_name = next((p["document"] for p in pages if p.get("document")), "wrapper.pdf")
    return [{**{k: v for k, v in row.items() if k not in ("page", "exact_text")}, "source_type": "wrapper",
             "source": {"document": wrapper_name, "page": row["page"], "row": None, "exact_text": row["exact_text"]}}
            for row in rows]


def load_bundle_sources(wrapper: Path, bundle: dict) -> tuple[list, list, list]:
    """Load cited sources; wrapper conditions and product specifications stay distinct."""
    import pymupdf
    from tender_extraction.ingestion import ingest_document
    from tender_extraction.table_extraction import extract_tables
    root = Path(bundle["directory"])
    with pymupdf.open(wrapper) as pdf:
        wrapper_pages = [{"page": n, "text": page.get_text(), "is_ocr": False, "headings": [],
                          "document": wrapper.name, "role": "wrapper"} for n, page in enumerate(pdf, 1)]
    items, pages, tables = [], list(wrapper_pages), []
    loaded = set()
    for attachment in bundle["attachments"]:
        if attachment["status"] != "ready":
            continue
        path = root / attachment["file"]
        role = attachment["role"]
        if (path.name, role) in loaded:
            continue
        loaded.add((path.name, role))
        try:
            if role == "boq" and attachment.get("document_type") == "boq_checklist":
                source_pages = ingest_document(str(path))
                if not source_pages or any(p.get("ocr_failed") for p in source_pages):
                    raise ValueError("BOQ checklist pages could not be read reliably")
                pages.extend({**p, "document": path.name, "role": "atc"} for p in source_pages)
            elif role == "boq":
                parsed = read_boq(path)
                items.extend(parsed)
                for item in parsed:
                    pages.append({"page": item["source"]["page"], "document": path.name, "role": "boq", "row": item["source"]["row"],
                                  "is_ocr": False, "headings": [], "text": (
                                      f"BOQ item {item['item_id']}: {item['description']}; Quantity: {item['quantity']}; Unit: {item['unit']}\n"
                                      + item["source"]["exact_text"])})
            elif role in ("specification", "atc"):
                source_pages = ingest_document(str(path))
                pages.extend({**page, "document": path.name, "role": role} for page in source_pages)
                if any(page.get("ocr_failed") for page in source_pages) or not source_pages:
                    raise ValueError(f"{role.title()} pages could not be read reliably")
                if role == "specification":
                    for table in extract_tables(str(path), pages=source_pages):
                        tables.append({**table, "table_id": f"{path.stem}:{table['table_id']}",
                                       "document": path.name, "role": role})
        except Exception as exc:  # A failed attachment must not become a complete tender.
            attachment.update(status="unreadable", error=str(exc))
            bundle["missing"].append(f"{attachment['role']}: {exc}")
            bundle["complete"] = False
    wrapper_issues = []
    wrapper_items = read_wrapper_items(wrapper_pages, wrapper_issues)
    bundle["wrapper_item_candidates"] = wrapper_items
    bundle["wrapper_item_issues"] = wrapper_issues
    if not bundle.get("boq_referenced"):
        bundle["missing"].extend(wrapper_issues)
        if wrapper_items:
            items = wrapper_items
    elif not items:
        bundle["missing"].append("boq: referenced documents contain no readable authoritative item list")
    if not bundle.get("boq_referenced") and not items:
        bundle["missing"].append("boq: no BOQ link and no unambiguous wrapper item list; upload the BOQ manually")
        if not any(entry["role"] == "boq" for entry in bundle.get("manual_required", [])):
            bundle.setdefault("manual_required", []).append({
                "role": "boq", "url": "", "reason": "no BOQ link and no unambiguous wrapper item list"})
        if not wrapper_items:
            bundle["missing"].append("wrapper: item list is missing or ambiguous")
    identifiers = [item["item_id"] for item in items]
    if len(set(identifiers)) != len(identifiers):
        bundle["missing"].append("Item identifiers conflict across sources")
    expected = re.search(r"Total\s+Quantity\s*[:\r\n]+\s*([\d,.]+)", "\n".join(p["text"] for p in wrapper_pages), re.I)
    actual = sum((Decimal(item["quantity"]) for item in items), Decimal(0))
    expected_value = Decimal(expected.group(1).replace(",", "")) if expected else None
    bundle["quantity_check"] = {"expected": str(expected_value) if expected_value is not None else None,
                                "actual": format(actual, "f") if items else None, "matches": bool(items) and expected_value == actual}
    expected_page = next(
        (page["page"] for page in wrapper_pages
         if re.search(r"Total\s+Quantity\s*[:\r\n]+\s*([\d,.]+)", page["text"] or "", re.I)),
        0,
    )
    bundle["quantity_citation"] = {
        "document": wrapper.name,
        "page": expected_page,
        "row": None,
        "exact_text": expected.group(0) if expected else "Total Quantity",
    }
    if expected_value is not None and items and expected_value != actual:
        bundle["missing"].append("Item quantity total differs from the wrapper")
    bundle["complete"] = not bundle["missing"]
    return pages, tables, items


def _item_tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - {"for", "and", "of", "the", "a", "an"}


def _page_item_index(pages: list[dict], items: list[dict]) -> dict:
    """Map a specification page to one item when its text names that item.

    Digit-bearing tokens (``2in``, ``600``, ``pa``, ``47``) are distinctive, so
    an item can only match a page that contains all of them. That keeps a page
    describing "2IN 600" from also matching "2IN 150". Ties are left unmapped.
    """
    descriptions = {
        str(item["item_id"]): _item_tokens(str(item.get("description") or "")) for item in items
    }
    index = {}
    for page in pages:
        if page.get("role") != "specification":
            continue
        text = _item_tokens(str(page.get("text") or ""))
        scored = []
        for item in items:
            item_id = str(item["item_id"])
            tokens = descriptions.get(item_id) or set()
            if len(tokens) < 2:
                continue
            distinctive = {token for token in tokens if any(ch.isdigit() for ch in token)}
            if distinctive and not distinctive <= text:
                continue
            ratio = len(tokens & text) / len(tokens)
            if ratio >= 0.6:
                scored.append((ratio, item_id))
        scored.sort(reverse=True)
        if not scored:
            continue
        if len(scored) == 1:
            index[(page.get("document"), page.get("page"))] = scored[0][1]
    return index


def _association_parameters(text: str) -> dict[str, set[Decimal]]:
    # Remove piston rod diameter phrases so they are not confused with main cylinder bore/diameter
    clean_text = re.sub(r"\bpiston\s+rod\s+(?:diameter|dia)[^;,\n]*", "", text, flags=re.I)
    parameters: dict[str, set[Decimal]] = {}
    for key, pattern in {
        "diameter": r"\b(?:diameter|bore|bore[ _]dia)\s*(?:mm\s*)?[:= -]*\s*(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in)?\b",
        "stroke": r"\bstroke\s*(?:mm\s*)?[:= -]*\s*(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in)?\b",
        "size": r"\b(\d+(?:\.\d+)?)\s*(inches|inch|in)\b",
    }.items():
        for value, unit in re.findall(pattern, clean_text, re.I):
            scale = Decimal("25.4") if unit.lower().startswith("in") else Decimal(10) if unit.lower() == "cm" else Decimal(1)
            parameters.setdefault(key, set()).add(Decimal(value) * scale)
    for m in re.finditer(r"(?:\b(?:class|rating)\s*#?\s*(150|300|600|900|1500|2500)\b|\b(150|300|600|900|1500|2500)\s*(?:#|lbs\b|ibr\b|class\b|rating\b))", clean_text, re.I):
        val = m.group(1) or m.group(2)
        if val:
            parameters.setdefault("pressure_class", set()).add(Decimal(val))
    return parameters


def classify_item_type(description: str, vocabulary=None) -> tuple[str, str]:
    """Classify a BOQ item using generic patterns and the manifest vocabulary.

    Returns (item_type, reason) where item_type is one of:
    main_product, spares, tools, consumables, review

    Product families are never hardcoded: a description is a main product when
    the manifest-derived category vocabulary recognises it.  Only generic
    ancillary and procedure words are matched literally.
    """
    text = description.strip()
    upper = text.upper()
    if re.search(r"\bSPARES?\b.*\bAS PER\b", upper) or re.match(r"\bSPARES?\b", upper):
        return ("spares", "SPARES AS PER pattern")
    if re.search(r"\bTOOLS?\b.*\bAS PER\b", upper) or re.match(r"\bTOOLS?\b", upper):
        return ("tools", "TOOLS AS PER pattern")
    if re.match(r"\bPAINT\b", upper) or re.search(r"\bPAINT\b.*\bAS PER\b", upper):
        return ("consumables", "PAINT pattern")
    if re.match(r"\bPRIMER\b", upper):
        return ("consumables", "PRIMER pattern")
    if re.match(r"\bLUBRICANT\b", upper) or re.match(r"\bGREASE\b", upper):
        return ("consumables", "LUBRICANT/GREASE pattern")

    category, _ = classify_text(text, vocabulary or _category_vocabulary())
    if category:
        return ("main_product", f"catalogued product category: {category}")

    # A short line that only describes an activity is a procedure row, not a
    # product.  It stays reviewable but never blocks matching.
    if re.search(
        r"\b(supply|installation|erection|testing|commissioning|training|transport|"
        r"insurance|packing|forwarding|service|maintenance|inspection)\b",
        text,
        re.IGNORECASE,
    ) and len(_item_tokens(text)) <= 6:
        return ("review", "service/procedure line")
    return ("main_product", "BOQ product line")


def detect_standard_references(description: str) -> list[str]:
    """Extract referenced standards from an item description.

    Patterns: "AS PER CLAUSE X.X OF (PRODUCT STANDARD|IS|BS|DIN|ASME|HT) CODE"
    Returns list of standard codes found.
    """
    patterns = [
        r"AS\s+PER\s+(?:CLAUSE\s+[\d.]+\s+OF\s+)?(?:PRODUCT\s+STANDARD|STANDARD|IS|BS|DIN|ASME)\s+([\w\s]+)",
        r"AS\s+PER\s+(?:CLAUSE\s+[\d.]+\s+OF\s+)?(HT\s*[\d]+)",
        r"(?:PRODUCT\s+STANDARD|STANDARD)\s+(HT\s*[\d]+)",
    ]
    found = []
    text_upper = description.upper()
    for pat in patterns:
        for m in re.finditer(pat, text_upper):
            code = m.group(1).strip()
            code = re.sub(r"(HT)\s*(\d+)", r"HT \2", code)
            code = code.strip()
            if code and code not in found:
                found.append(code)
    return found


def scan_attachments_for_standard(standard_code: str, attachments: list, root: Any) -> bool:
    """Check if the full text of a standard (not just a reference) is in the attachments."""
    import re as _re
    root = Path(root)
    code_pattern = _re.compile(_re.escape(standard_code.replace(" ", r"\s*")), _re.I)
    for att in attachments:
        if att.get("status") not in ("ready", "ocr"):
            continue
        role = att.get("role", "")
        if role in ("boq", "checklist"):
            continue
        sha = att.get("sha256", "")
        text = ""
        for candidate in root.glob(f"{sha[:8]}*"):
            try:
                if candidate.suffix in (".txt", ".json"):
                    text = candidate.read_text(encoding="utf-8", errors="ignore")
                    break
            except Exception:
                pass
        if not text:
            path = att.get("path") or att.get("local_path") or ""
            if path:
                try:
                    p = Path(path)
                    if p.exists():
                        text = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
        if not text:
            continue
        occurrences = len(code_pattern.findall(text))
        has_section = bool(_re.search(
            rf"(?:CLAUSE|SECTION|ANNEX)\s+[\d.]+.*{_re.escape(standard_code[:5])}",
            text, _re.I
        ))
        if occurrences > 50 or has_section:
            return True
    return False


def match_spec_item(spec: dict, items: list[dict], page_target: str = "") -> tuple[str, str]:
    """Pick the BOQ item a specification belongs to by candidate scoring.

    Conflicts are a negative signal, never a hard veto: the same parameter can
    legitimately appear on different components (bore vs rod diameter, pump vs
    motor power).  The highest-scoring compatible item wins; ties stay
    unresolved with the candidate list in the reason.
    """
    from tender_extraction.parameters import extract_parameter_values

    candidates = [item for item in items if not item.get("synthetic")]
    if not candidates:
        return "", "no BOQ items available"
    comp_name = str(spec.get("component") or "")
    text = comp_name + " " + " ".join(
        f"{key.replace('_', ' ')}: {value}" for key, value in (spec.get("specs") or {}).items())

    spec_params = extract_parameter_values(text, spec.get("specs"))
    tokens = _item_tokens(comp_name)

    scored: list[tuple[float, str, list[str]]] = []
    for item in candidates:
        item_id = str(item["item_id"])
        item_desc = str(item.get("description") or "")
        item_type = str(item.get("item_type") or "main_product")
        item_tokens = _item_tokens(item_desc)
        item_params = extract_parameter_values(item_desc)

        conflict = False
        for parameter, spec_values in spec_params.items():
            if parameter not in item_params:
                continue
            for spec_value in spec_values:
                for item_value in item_params[parameter]:
                    if spec_value.is_compatible(item_value) is False:
                        conflict = True
                        break
                if conflict:
                    break
            if conflict:
                break

        name_match = len(tokens) >= 2 and tokens <= item_tokens
        overlap = len(tokens & item_tokens) / max(len(tokens), 1) if tokens else 0.0
        common_parameters = set(spec_params) & set(item_params)

        score = 0.0
        reasons: list[str] = []
        if name_match:
            score += 4.0
            reasons.append("name match")
        elif overlap >= 0.5:
            score += 2.0
            reasons.append("partial name match")
        if page_target:
            if item_id == page_target:
                score += 5.0
                reasons.append("cited page")
            else:
                score -= 6.0
                reasons.append("contradicts cited page")
        if common_parameters:
            score += 2.0
            reasons.append("parameter overlap")
        if conflict:
            score -= 6.0
            reasons.append("contradicting parameter")
        if item_type == "main_product":
            score += 1.0
        elif item_type in ("spares", "tools", "consumables"):
            if name_match:
                score += 1.0
            else:
                score -= 2.0
                reasons.append("ancillary item not named")
        scored.append((score, item_id, reasons))

    if not scored:
        return "", "no BOQ items available"

    scored.sort(key=lambda entry: entry[0], reverse=True)
    best_score, best_id, best_reasons = scored[0]
    candidates_summary = "; ".join(
        f"{item_id}={score:g}" for score, item_id, _ in scored[:5]
    )

    # Single Main Product Rule: when the tender has exactly one main product,
    # any compatible mechanical spec belongs to it unless an ancillary item is
    # explicitly named.
    main_products = [item for item in candidates if item.get("item_type") == "main_product"]
    if len(main_products) == 1:
        main_id = str(main_products[0]["item_id"])
        main_entry = next((entry for entry in scored if entry[1] == main_id), None)
        ancillary_named = any(
            entry[0] >= 4.0 and entry[1] != main_id
            for entry in scored
            if next(item for item in candidates if str(item["item_id"]) == entry[1]).get("item_type")
            in ("spares", "tools", "consumables")
        )
        if main_entry and main_entry[0] > -6.0 and not ancillary_named:
            return main_id, "associated with the unique main product item"

    if best_score >= 3.0:
        tie = [entry for entry in scored if entry[0] == best_score]
        if len(tie) == 1:
            return best_id, f"best candidate ({', '.join(best_reasons) or 'score'})"
        return "", f"ambiguous parent items: {candidates_summary}"
    return "", f"no parent item scored high enough: {candidates_summary}"


def assign_spec_items(specs: list[dict], pages: list[dict], items: list[dict]) -> None:
    from tender_extraction.reconcile import is_generic_spec

    index = _page_item_index(pages, items)
    for spec in specs:
        source = spec.get("source") or {}
        page_target = index.get((source.get("document"), source.get("page")), "")
        target, reason = match_spec_item(spec, items, page_target)
        spec["boq_item_id"] = target
        if target:
            spec["association_status"] = "matched"
        elif is_generic_spec(spec):
            spec["association_status"] = "tender_level"
        else:
            spec["association_status"] = "review"
        spec["association_reason"] = reason


def finish_bundle_result(result: dict, bundle: dict, items: list[dict], pages: list[dict] | None = None) -> dict:
    """Attach authoritative BOQ values and structured review notes."""
    from tender_extraction.reconcile import is_generic_spec

    def words(text):
        return _item_tokens(text)

    # Classify each item type and detect standard references
    root_dir = Path(bundle.get("directory", "."))
    attachments = bundle.get("attachments", [])
    per_item_status: dict = {}
    for item in items:
        item_id = str(item["item_id"])
        desc = str(item.get("description") or "")
        
        # Classify item type (strong patterns only)
        if not item.get("item_type") or item.get("item_type") == "main_product":
            item_type, type_reason = classify_item_type(desc)
            item["item_type"] = item_type
        else:
            item_type = item["item_type"]
            type_reason = "pre-classified"
        
        # Detect standard references
        std_refs = detect_standard_references(desc)
        item["referenced_standards"] = std_refs
        
        # Determine processing_status
        if item_type in ("spares", "tools", "consumables") and std_refs:
            std_found = any(
                scan_attachments_for_standard(code, attachments, root_dir)
                for code in std_refs
            )
            if not std_found:
                item["processing_status"] = "reference_unresolved"
            else:
                item["processing_status"] = "ready"
        elif item_type == "review":
            item.setdefault("processing_status", "needs_review")
        else:
            item.setdefault("processing_status", "ready")
        
        per_item_status[item_id] = {
            "item_type": item["item_type"],
            "processing_status": item.get("processing_status", "ready"),
            "referenced_standards": std_refs,
            "has_requirements": False,
        }

    if bundle["complete"] and not result.get("technical_specifications"):
        bundle["missing"].append("No product specifications recovered; manual review is required")
    specs = result.get("technical_specifications", [])
    assign_spec_items([spec for spec in specs if not spec.get("association_status")], pages or [], items)

    for spec in specs:
        item_id = str(spec.get("boq_item_id") or "")
        if item_id in per_item_status:
            per_item_status[item_id]["has_requirements"] = True

    valid_ids = {str(item["item_id"]) for item in items if not item.get("synthetic")}
    for spec in specs:
        if spec.get("association_status") == "matched" and str(spec.get("boq_item_id") or "") in valid_ids:
            continue
        if spec.get("association_status") == "tender_level" and is_generic_spec(spec):
            continue
        # Unmapped or ambiguous specs stay attached at tender level with a note; they do not mark bundle incomplete
        result.setdefault("notes", []).append(f"Requirement at tender level / needs BOQ review: {spec['component']} ({spec.get('association_reason', 'unresolved')})")
    if bundle["complete"]:
        linked = {spec.get("boq_item_id") for spec in result.get("technical_specifications", [])}
        for item in items:
            item_id = item["item_id"]
            item_type = item.get("item_type", "main_product")
            processing_status = item.get("processing_status", "ready")
            if item_id not in linked:
                if item_type in ("spares", "tools", "consumables") and processing_status == "reference_unresolved":
                    result.setdefault("notes", []).append(
                        f"BOQ item {item_id} ({item_type}): {processing_status} — standard reference not extracted"
                    )
                elif item_type == "main_product" or processing_status == "ready":
                    result.setdefault("notes", []).append(f"No product requirement recovered for BOQ item {item_id}")
    scope = result.setdefault("scope_of_work", {})
    clauses = scope.get("deliverables", [])
    def quantity_item_clause(clause):
        return bool(re.search(r"\b(?:quantity|qty)\b", clause, re.I)) and any(
            len(words(clause) & words(item["description"])) >= 2 for item in items)
    scope["deliverables"] = [f"{item['description']}: {item['quantity']} {item['unit']} (BOQ item {item['item_id']})" for item in items] + [c for c in clauses if not quantity_item_clause(c)]
    bundle["complete"] = not bundle["missing"]
    wrapper_candidates = bundle.get("wrapper_item_candidates") or []
    if not isinstance(wrapper_candidates, list):
        wrapper_candidates = []
    result.update(procurement_items=items,
                  attachments=bundle["attachments"], quantity_check=bundle.get("quantity_check", {}),
                  wrapper_item_candidates=wrapper_candidates,
                  manual_attachments=bundle.get("manual_required", []),
                  wrapper=bundle.get("wrapper", ""), wrapper_sha256=bundle.get("wrapper_sha256", ""),
                  boq_referenced=bool(bundle.get("boq_referenced")),
                  quantity_citation=bundle.get("quantity_citation", {}),
                  per_item_status=per_item_status)
    result.setdefault("notes", []).extend(bundle["missing"])

    # Cross-document reconciliation runs after item association and before
    # matching. It preserves both sides of every disagreement and emits
    # structured, non-blocking review notes.
    from tender_extraction.reconcile import reconcile_result
    reconciliation = reconcile_result(result)
    result.update(
        reconciliation=reconciliation,
        canonical_requirements=reconciliation["canonical_requirements"],
        compliance_requirements=reconciliation["compliance_requirements"],
        vendor_eligibility=reconciliation["vendor_eligibility"],
        review_notes=reconciliation.get("review_notes", []),
        category=reconciliation.get("category", ""),
        categories=reconciliation.get("categories", []),
        blocking_reasons=reconciliation.get("blocking_reasons", []),
    )
    return result


def resolve_spec_sources(specs: list[dict], hits: list[dict]) -> None:
    """Fill citation identity only when an exact quote identifies one source location."""
    def normalize(text):
        return " ".join(text.casefold().split())
    for spec in specs:
        source = spec.setdefault("source", {})
        quote = normalize(str(source.get("exact_text") or ""))
        if not quote or quote == "not_found":
            continue
        candidates = [hit["chunk"] for hit in hits if quote in normalize(hit["chunk"].text)
                      and (not source.get("document") or source["document"] == hit["chunk"].metadata.document)
                      and (not source.get("page") or source["page"] == hit["chunk"].metadata.page)]
        locations = {(c.metadata.document, c.metadata.page, c.metadata.row) for c in candidates}
        if len(locations) == 1:
            chunk = candidates[0]
            source.update(document=chunk.metadata.document, page=chunk.metadata.page,
                          row=chunk.metadata.row, chunk_id=chunk.chunk_id)


def extract_bundle(wrapper: Path, bundle: dict, output_path=None, progress_callback=None) -> dict:
    """GeM bundles use role-separated full-text batches; no redundant tender vector index."""
    from tender_extraction.chunking import create_chunks
    from tender_extraction.extraction import extract_specifications, extract_scope_of_work
    from tender_extraction.validation import validate_extractions, validate_schema
    if progress_callback:
        progress_callback(10, "Reading specification and BOQ attachments...")
    pages, tables, items = load_bundle_sources(wrapper, bundle)
    result = {"technical_specifications": [], "scope_of_work": {}, "accuracy_score": 0, "notes": []}
    from tender_extraction.schemas import Chunk, ChunkMetadata
    chunks = [Chunk(chunk_id=f"{p['document']}:{p.get('row') or p['page']}", text=p["text"],
                    metadata=ChunkMetadata(page=p["page"], document=p["document"],
                                           role=p["role"], row=p.get("row"))) for p in pages]
    chunks.extend(create_chunks([], tables=tables, use_semantic=False))
    if output_path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_name(target.stem + "_chunks.json").write_text(
            json.dumps([c.model_dump() for c in chunks], ensure_ascii=False, indent=2), encoding="utf-8")
    specification_ready = any(
        item.get("role") == "specification" and item.get("status") == "ready"
        for item in bundle.get("attachments", [])
    )
    quantity_check = bundle.get("quantity_check") or {}
    quantity_conflict = (
        quantity_check.get("expected") is not None
        and quantity_check.get("actual") is not None
        and not quantity_check.get("matches")
    )
    if specification_ready and not quantity_conflict:
        if progress_callback:
            progress_callback(60, "Extracting product requirements from attachments...")
        # One page per chunk preserves continuation context and avoids prefix-based evidence loss.
        hits = [{"chunk": chunk, "score": 1.0} for chunk in chunks]
        product_hits = [h for h in hits if h["chunk"].metadata.role == "specification"]
        wrapper_hits = [h for h in hits if h["chunk"].metadata.role == "wrapper"]
        atc_hits = [h for h in hits if h["chunk"].metadata.role == "atc"][:6]
        condition_hits = wrapper_hits if wrapper_hits else atc_hits
        topic = "BOQ item mapping: " + "; ".join(f"{i['item_id']}: {i['description']}" for i in items)
        specs = extract_specifications(product_hits, topic + ". Extract product attributes, not procurement quantities or eligibility conditions.")
        resolve_spec_sources(specs, product_hits)
        assign_spec_items(specs, pages, items)
        scope = extract_scope_of_work(condition_hits, "Bid conditions only. Supply line items and quantities are added deterministically from the BOQ; do not infer them from wrapper continuation tables. GeMARPTS search suggestions are not requirements.")
        result = validate_schema(validate_extractions({"technical_specifications": specs, "scope_of_work": scope}, hits)).model_dump()
    result = finish_bundle_result(result, bundle, items, pages)
    result["notes"].append("GeM attachment bundle: product requirements come from specifications; quantities come from the BOQ; wrapper supplies bid conditions.")
    from tender_extraction import PIPELINE_VERSION
    result["pipeline_version"] = PIPELINE_VERSION
    result = validate_schema(result).model_dump()
    root = Path(bundle["directory"])
    (root / "bundle.json").write_text(json.dumps({k: v for k, v in bundle.items() if k != "directory"}, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_path:
        Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress_callback:
        progress_callback(100, "Extraction complete")
    return result
