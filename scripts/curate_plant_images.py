#!/usr/bin/env python3
import csv
import hashlib
import html
import json
import re
import shutil
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw, ImageFont


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "biologie-poznavacka-curation/1.0 (local educational project; https://openai.com)"
IMAGE_LIMIT = 3
PPTX_SOURCE = Path("/Users/bubak/Downloads/Poznávačka.pptx")
PPTX_MEDIA_DIR = Path("pptx-extracted-images/poznavacka/media")
PPTX_MANIFEST = Path("pptx-extracted-images/poznavacka/manifest.json")

try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()


# Manual fixes for slide titles that are split or misspelled in the source deck.
MANUAL_PPT_SLIDES = {
    42: 38,  # Bršlice appears as "B | ršlice"
    53: 48,  # Hvozdík is misspelled as "Hvozdník"
}


# For plants not covered by the PPTX, use common Czech species or typical Czech
# representatives. These exact species names are deliberately narrower than the
# original genus-level list.
CURATED_COMMONS_SPECIES = {
    2: ["Ranunculus acris", "Ranunculus repens", "Ranunculus bulbosus"],
    81: ["Campanula rotundifolia", "Campanula patula", "Campanula persicifolia"],
    82: ["Cichorium intybus"],
    83: ["Lapsana communis"],
    84: ["Hieracium umbellatum", "Hieracium murorum", "Pilosella officinarum"],
    85: ["Taraxacum officinale", "Taraxacum sect. Ruderalia"],
    86: ["Achillea millefolium"],
    87: ["Matricaria chamomilla", "Matricaria recutita"],
    88: ["Leucanthemum vulgare"],
    89: ["Tanacetum vulgare"],
    90: ["Artemisia vulgaris", "Artemisia absinthium"],
    91: ["Bellis perennis"],
    92: ["Tussilago farfara"],
    93: ["Petasites hybridus"],
    94: ["Arctium lappa", "Arctium tomentosum"],
    95: ["Centaurea cyanus", "Centaurea jacea"],
    96: ["Cirsium arvense", "Cirsium vulgare"],
    97: ["Lilium martagon"],
    98: ["Allium ursinum", "Allium vineale"],
    99: ["Convallaria majalis"],
    100: ["Paris quadrifolia"],
    101: ["Galanthus nivalis"],
    102: ["Leucojum vernum"],
    103: ["Iris pseudacorus"],
    104: ["Dactylorhiza majalis", "Dactylorhiza fuchsii"],
    105: ["Juncus effusus"],
    106: ["Carex hirta", "Carex pendula", "Carex riparia"],
    108: ["Alopecurus pratensis"],
    110: ["Poa pratensis", "Poa annua"],
    112: ["Lemna minor"],
    113: ["Typha latifolia", "Typha angustifolia"],
    114: ["Phragmites australis"],
    115: ["Acer platanoides", "Acer pseudoplatanus", "Acer campestre"],
    116: ["Tilia cordata", "Tilia platyphyllos"],
    117: ["Fagus sylvatica"],
    118: ["Carpinus betulus"],
    119: ["Betula pendula"],
    120: ["Corylus avellana"],
    121: ["Quercus robur", "Quercus petraea"],
    122: ["Sorbus aucuparia"],
    123: ["Fraxinus excelsior"],
    124: ["Alnus glutinosa"],
    125: ["Salix caprea", "Salix alba"],
    126: ["Robinia pseudoacacia"],
    127: ["Populus tremula", "Populus nigra"],
    128: ["Juglans regia"],
    129: ["Picea abies"],
    130: ["Pinus sylvestris"],
    131: ["Larix decidua"],
    132: ["Abies alba"],
    133: ["Taxus baccata"],
    134: ["Juniperus communis"],
}


COMMONS_TITLE_BLOCKLIST = (
    " distribution",
    " range",
    " map",
    " locator",
    " diagram",
    " drawing",
    " illustration",
    " plate",
    " herbarium",
    " herbier",
    " logo",
    " icon",
    " symbol",
    " stamp",
    " cultivar",
    " garden",
    " ornamental",
    " variegat",
    " bonsai",
    " seedling",
    " fruit only",
    " berries only",
)

KNOWN_BAD_TERMS = (
    "immature inflorescences",
    "thricops semicinereus",
    "ranunculus glacialis",
    "fragaria × ananassa",
    "fragaria x ananassa",
    "garden strawberry",
)

BAD_EXTENSIONS = (".svg", ".gif", ".tif", ".tiff", ".pdf", ".djvu")


def request_json(params, retries=5):
    request = urllib.request.Request(
        f"{COMMONS_API}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": USER_AGENT},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if attempt == retries - 1:
                raise
            retry_after = int(error.headers.get("Retry-After", "0") or "0")
            time.sleep(retry_after or (8 * (attempt + 1) if error.code == 429 else 2 * (attempt + 1)))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def download(url, target, retries=5):
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=45, context=SSL_CONTEXT) as response:
                target.write_bytes(response.read())
            return
        except urllib.error.HTTPError as error:
            if attempt == retries - 1:
                raise
            retry_after = int(error.headers.get("Retry-After", "0") or "0")
            time.sleep(retry_after or (10 * (attempt + 1) if error.code == 429 else 2 * (attempt + 1)))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def normalize(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def slugify(value):
    return re.sub(r"[^a-zA-Z0-9]+", "-", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()).strip("-")


def clean_html(value):
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def strip_query(url):
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def parse_plants(path):
    plants = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(\d+)\.\s+(.+)$", line.strip())
        if not match:
            continue
        number = int(match.group(1))
        rest = match.group(2)
        czech = rest.split("_", 1)[0].strip()
        latin = []
        for chunk in re.findall(r"_([^_]+)_", rest):
            latin.extend(part.strip() for part in chunk.split("/") if part.strip())
        note = re.sub(r"_[^_]+_", "", rest).replace(czech, "", 1).strip()
        note = re.sub(r"^\s*/\s*", "", note).strip()
        plants.append(
            {
                "number": number,
                "czech": czech,
                "latin": latin,
                "note": note,
                "slug": f"{number:03d}-{slugify(czech)}",
                "norm": normalize(czech),
            }
        )
    return plants


def load_ppt_slide_titles(pptx_path):
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    titles = {}
    with zipfile.ZipFile(pptx_path) as deck:
        for name in deck.namelist():
            match = re.match(r"ppt/slides/slide(\d+)\.xml$", name)
            if not match:
                continue
            slide_no = int(match.group(1))
            root = ET.fromstring(deck.read(name))
            texts = [item.text for item in root.findall(".//a:t", ns) if item.text]
            titles[slide_no] = " ".join(texts).strip()
    return titles


def build_ppt_map(plants, slide_titles):
    slide_map = {}
    for plant in plants:
        for slide_no, title in slide_titles.items():
            if slide_no < 4 or slide_no > 73:
                continue
            title_norm = normalize(title)
            if plant["norm"] == title_norm or title_norm.startswith(f"{plant['norm']} "):
                slide_map[plant["number"]] = slide_no
                break
    slide_map.update(MANUAL_PPT_SLIDES)
    return slide_map


def load_ppt_manifest():
    records = json.loads(PPTX_MANIFEST.read_text(encoding="utf-8"))
    by_slide = {}
    for record in records:
        for slide in record["slides"]:
            by_slide.setdefault(slide, []).append(record)
    return by_slide


def ppt_score(record):
    width = record.get("width") or 0
    height = record.get("height") or 0
    if width <= 0 or height <= 0:
        return 0
    area = width * height
    ratio = max(width / height, height / width)
    ratio_penalty = 0 if ratio <= 2.5 else area * 0.25
    small_penalty = 2_000_000 if min(width, height) < 170 else 0
    return area - ratio_penalty - small_penalty


def commons_search(query, limit=40):
    data = request_json(
        {
            "action": "query",
            "generator": "search",
            "gsrnamespace": 6,
            "gsrsearch": query,
            "gsrlimit": limit,
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
            "iiurlwidth": 1200,
            "format": "json",
            "formatversion": 2,
        }
    )
    pages = data.get("query", {}).get("pages", [])
    pages.sort(key=lambda page: page.get("index", 10_000))
    return pages


def species_in_title(species, title):
    title_norm = normalize(title)
    species_norm = normalize(species)
    return species_norm in title_norm


def is_good_commons_page(page, target_species):
    title = page.get("title", "")
    lower = title.lower()
    if any(lower.endswith(ext) for ext in BAD_EXTENSIONS):
        return False, "unsupported extension"
    if any(term in lower for term in KNOWN_BAD_TERMS):
        return False, "known bad term"
    if any(term in lower for term in COMMONS_TITLE_BLOCKLIST):
        return False, "blocked title term"
    if "'" in lower.removeprefix("file:") or '"' in lower.removeprefix("file:"):
        return False, "cultivar-style quoted name"
    if not any(species_in_title(species, title) for species in target_species):
        return False, "title lacks curated species"
    info = (page.get("imageinfo") or [{}])[0]
    if info.get("mime") not in {"image/jpeg", "image/png", "image/webp"}:
        return False, "unsupported mime"
    if (info.get("width") or 0) < 350 or (info.get("height") or 0) < 350:
        return False, "too small"
    license_short = clean_html((info.get("extmetadata") or {}).get("LicenseShortName", {}).get("value"))
    if "non-free" in license_short.lower():
        return False, "non-free license"
    return True, ""


def image_extension(url, mime):
    suffix = Path(urllib.parse.urlparse(url).path.lower()).suffix
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return ".png" if mime == "image/png" else ".webp" if mime == "image/webp" else ".jpg"


def commons_metadata(page, local_path, query, species):
    info = page["imageinfo"][0]
    metadata = info.get("extmetadata") or {}
    download_url = strip_query(info.get("thumburl") or info.get("url"))
    return {
        "title": page.get("title", ""),
        "local_path": str(local_path),
        "source": "Wikimedia Commons",
        "source_url": info.get("descriptionurl"),
        "download_url": download_url,
        "mime": info.get("mime"),
        "width": info.get("thumbwidth") or info.get("width"),
        "height": info.get("thumbheight") or info.get("height"),
        "author": clean_html(metadata.get("Artist", {}).get("value")) or "autor neuveden",
        "license": clean_html(metadata.get("LicenseShortName", {}).get("value")) or "licence neuvedena",
        "license_url": clean_html(metadata.get("LicenseUrl", {}).get("value")),
        "query": query,
        "species_or_taxon": species,
        "confidence": "high",
    }


def copy_ppt_image(record, local_path, plant, slide_no):
    source = PPTX_MEDIA_DIR / record["filename"]
    local_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, local_path)
    data = local_path.read_bytes()
    return {
        "title": f"PPTX slide {slide_no}: {record['filename']}",
        "local_path": str(local_path),
        "source": "Poznávačka.pptx",
        "source_url": str(PPTX_SOURCE),
        "download_url": "",
        "mime": f"image/{record['extension']}",
        "width": record.get("width"),
        "height": record.get("height"),
        "author": "Poznávačka.pptx",
        "license": "source PPTX (license not specified)",
        "license_url": "",
        "query": f"slide {slide_no}",
        "species_or_taxon": " / ".join(plant["latin"]),
        "sha256": hashlib.sha256(data).hexdigest(),
        "confidence": "high",
    }


def collect_commons_images(plant, output_dir, pause):
    target_species = CURATED_COMMONS_SPECIES.get(plant["number"])
    if not target_species:
        raise RuntimeError(f"Missing curated species for {plant['number']} {plant['czech']}")

    selected = []
    rejected = []
    seen_titles = set()
    candidates_for_sheet = []

    query_variants = []
    for species in target_species:
        query_variants.extend([species, f"{species} plant", f"{species} flower", f"{species} leaves"])

    for query in query_variants:
        if len(selected) >= IMAGE_LIMIT:
            break
        pages = commons_search(query)
        time.sleep(pause)
        for page in pages:
            title = page.get("title", "")
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            ok, reason = is_good_commons_page(page, target_species)
            info = (page.get("imageinfo") or [{}])[0]
            if len(candidates_for_sheet) < 12 and info.get("thumburl"):
                candidates_for_sheet.append(("commons", title, strip_query(info["thumburl"])))
            if not ok:
                rejected.append(f"{title} ({reason})")
                continue
            download_url = strip_query(info.get("thumburl") or info.get("url"))
            suffix = image_extension(download_url, info.get("mime", ""))
            local_path = output_dir / plant["slug"] / f"{len(selected) + 1:02d}{suffix}"
            download(download_url, local_path)
            metadata = commons_metadata(page, local_path, query, " / ".join(target_species))
            selected.append(metadata)
            time.sleep(pause)
            if len(selected) >= IMAGE_LIMIT:
                break

    if len(selected) != IMAGE_LIMIT:
        raise RuntimeError(f"{plant['number']} {plant['czech']} selected {len(selected)} Commons images")

    return selected, candidates_for_sheet, rejected


def old_candidates(plant, old_data):
    old = next((item for item in old_data if item["number"] == plant["number"]), None)
    if not old:
        return []
    result = []
    for image in old.get("images", []):
        path = Path(image["local_path"])
        if path.exists():
            result.append(("old", image.get("title", path.name), str(path)))
    return result


def make_contact_sheet(plant, candidates, selected_paths, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    thumb_w, thumb_h = 220, 165
    pad = 18
    label_h = 50
    cols = 3
    rows = max(1, (len(candidates) + cols - 1) // cols)
    width = cols * thumb_w + (cols + 1) * pad
    height = 70 + rows * (thumb_h + label_h + pad) + pad
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    font_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    font = ImageFont.truetype(str(font_path), 14) if font_path.exists() else ImageFont.load_default()
    draw.text((pad, pad), f"{plant['number']}. {plant['czech']} ({' / '.join(plant['latin'])})", fill="black", font=font)
    selected_set = {str(Path(path)) for path in selected_paths}
    for idx, (source, title, path_or_url) in enumerate(candidates):
        col = idx % cols
        row = idx // cols
        x = pad + col * (thumb_w + pad)
        y = 58 + row * (thumb_h + label_h + pad)
        try:
            if str(path_or_url).startswith("http"):
                target = output_path.parent / "_tmp_candidate.jpg"
                download(path_or_url, target, retries=2)
                image = Image.open(target).convert("RGB")
            else:
                image = Image.open(path_or_url).convert("RGB")
            image.thumbnail((thumb_w, thumb_h))
            ox = x + (thumb_w - image.width) // 2
            oy = y + (thumb_h - image.height) // 2
            canvas.paste(image, (ox, oy))
        except Exception:
            draw.rectangle((x, y, x + thumb_w, y + thumb_h), outline="red")
            draw.text((x + 8, y + 8), "load failed", fill="red", font=font)
        border = "green" if str(Path(path_or_url)) in selected_set else "#999999"
        draw.rectangle((x, y, x + thumb_w, y + thumb_h), outline=border, width=4 if border == "green" else 1)
        label = f"{source}: {title}"
        draw.text((x, y + thumb_h + 5), label[:42], fill="black", font=font)
        draw.text((x, y + thumb_h + 22), label[42:84], fill="#444444", font=font)
    canvas.save(output_path, quality=88)
    tmp = output_path.parent / "_tmp_candidate.jpg"
    if tmp.exists():
        tmp.unlink()


def render_markdown(plants, output):
    lines = [
        "# Seznam divokých rostlin ČR pro poznávání - kurátorované obrázky",
        "",
        "Sada vznikla porovnáním původních Wikimedia Commons kandidátů, obrázků z prezentace Poznávačka.pptx a cíleného dohledání běžných českých druhů.",
        "",
    ]
    for plant in plants:
        latin = " / ".join(plant["latin"])
        lines.append(f"## {plant['number']}. {plant['czech']} _{latin}_")
        if plant.get("note"):
            lines.append("")
            lines.append(f"Poznámka: {plant['note']}")
        lines.append("")
        for idx, image in enumerate(plant["images"], 1):
            lines.append(f"![{plant['czech']} {idx}]({image['local_path']})")
            source_url = image.get("source_url") or ""
            source_label = image.get("title") or f"{plant['czech']} {idx}"
            license_text = image.get("license") or "licence neuvedena"
            author = image.get("author") or "autor neuveden"
            if source_url:
                lines.append(f"{idx}. [{source_label}]({source_url}) - {license_text}, {author}")
            else:
                lines.append(f"{idx}. {source_label} - {license_text}, {author}")
            lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def write_review_log(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "number",
        "czech",
        "selected_files",
        "source",
        "species_or_taxon",
        "rejected_reason",
        "confidence",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate(plants):
    assert len(plants) == 121, f"expected 121 plants, got {len(plants)}"
    image_count = sum(len(plant.get("images", [])) for plant in plants)
    assert image_count == 363, f"expected 363 images, got {image_count}"
    bad_hits = []
    for plant in plants:
        assert len(plant["images"]) == 3, f"{plant['number']} {plant['czech']} does not have 3 images"
        for image in plant["images"]:
            path = Path(image["local_path"])
            assert path.exists(), f"missing {path}"
            assert path.stat().st_size > 0, f"empty {path}"
            title = image.get("title", "").lower()
            for term in KNOWN_BAD_TERMS:
                if term in title:
                    bad_hits.append((plant["number"], plant["czech"], image["title"]))
    assert not bad_hits, f"known bad image titles found: {bad_hits}"


def main():
    root = Path(".")
    plants = parse_plants(root / "seznam-divokych-rostlin.md")
    old_data = json.loads((root / "data" / "plant-images.json").read_text(encoding="utf-8"))
    ppt_by_slide = load_ppt_manifest()
    slide_titles = load_ppt_slide_titles(PPTX_SOURCE)
    ppt_map = build_ppt_map(plants, slide_titles)

    build_root = root / "data" / "curation-build"
    build_images = build_root / "images"
    contact_dir = root / "data" / "contact-sheets"
    if build_root.exists():
        shutil.rmtree(build_root)
    build_images.mkdir(parents=True, exist_ok=True)
    if contact_dir.exists():
        shutil.rmtree(contact_dir)
    contact_dir.mkdir(parents=True, exist_ok=True)

    reviewed = []
    log_rows = []
    changed = []

    for plant in plants:
        print(f"{plant['number']}. {plant['czech']}", flush=True)
        candidates = old_candidates(plant, old_data)
        rejected = []
        if plant["number"] in ppt_map:
            slide_no = ppt_map[plant["number"]]
            slide_records = sorted(ppt_by_slide.get(slide_no, []), key=ppt_score, reverse=True)
            chosen_records = slide_records[:IMAGE_LIMIT]
            selected = []
            for idx, record in enumerate(chosen_records, 1):
                suffix = Path(record["filename"]).suffix.lower()
                local_path = build_images / plant["slug"] / f"{idx:02d}{suffix}"
                selected.append(copy_ppt_image(record, local_path, plant, slide_no))
            candidates.extend(("pptx", record["filename"], str(PPTX_MEDIA_DIR / record["filename"])) for record in slide_records)
            rejected = [record["filename"] for record in slide_records[IMAGE_LIMIT:]]
            source = f"Poznávačka.pptx slide {slide_no}"
        else:
            selected, commons_candidates, rejected = collect_commons_images(plant, build_images, pause=0.5)
            candidates.extend(commons_candidates)
            source = "Wikimedia Commons curated species search"

        reviewed_plant = {key: value for key, value in plant.items() if key != "norm"}
        reviewed_plant["images"] = selected
        reviewed_plant["status"] = "complete"
        reviewed_plant["confidence"] = "high"
        reviewed.append(reviewed_plant)

        selected_files = [image["local_path"] for image in selected]
        final_selected_files = [
            str(Path("images") / Path(path).relative_to(build_images))
            for path in selected_files
        ]
        make_contact_sheet(plant, candidates[:15], selected_files, contact_dir / f"{plant['slug']}.jpg")
        old_titles = [image.get("title") for image in next((item for item in old_data if item["number"] == plant["number"]), {}).get("images", [])]
        new_titles = [image.get("title") for image in selected]
        if old_titles != new_titles:
            changed.append(f"{plant['number']}. {plant['czech']}: {source}")

        log_rows.append(
            {
                "number": plant["number"],
                "czech": plant["czech"],
                "selected_files": " | ".join(final_selected_files),
                "source": source,
                "species_or_taxon": " | ".join(image.get("species_or_taxon", "") for image in selected),
                "rejected_reason": "; ".join(rejected[:12]),
                "confidence": "high",
            }
        )

    validate(reviewed)

    final_images = root / "images"
    if final_images.exists():
        shutil.rmtree(final_images)
    shutil.copytree(build_images, final_images)
    for plant in reviewed:
        for image in plant["images"]:
            image["local_path"] = str(Path(image["local_path"]).relative_to(build_root))

    (root / "data" / "plant-images.json").write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "data" / "ppt-slide-map.json").write_text(json.dumps(ppt_map, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_review_log(log_rows, root / "data" / "review-log.csv")
    render_markdown(reviewed, root / "seznam-divokych-rostlin-obrazky.md")

    report = [
        "# Review report",
        "",
        f"- Plants reviewed: {len(reviewed)}",
        f"- Final images: {sum(len(plant['images']) for plant in reviewed)}",
        f"- PPT-backed plants: {sum(1 for plant in reviewed if plant['number'] in ppt_map)}",
        f"- Commons-backed plants: {sum(1 for plant in reviewed if plant['number'] not in ppt_map)}",
        f"- Changed plants: {len(changed)}",
        "- Botanical reference used for Czech-flora relevance: Pladias (https://pladias.cz/)",
        "- Commons metadata source: Wikimedia Commons API",
        "",
        "## Changed plants",
        "",
    ]
    report.extend(f"- {item}" for item in changed)
    (root / "data" / "review-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    shutil.rmtree(build_root)

    print(json.dumps({"plants": len(reviewed), "images": 363, "ppt_plants": len(ppt_map), "changed": len(changed)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
