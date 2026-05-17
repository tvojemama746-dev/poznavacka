#!/usr/bin/env python3
import argparse
import html
import json
import re
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "biologie-poznavacka/1.0 (local educational project; https://openai.com)"
IMAGE_LIMIT = 3

PREFERRED_QUERIES = {
    1: [
        "Hedera helix leaves",
        "Hedera helix vine",
        "Hedera helix plant",
        "břečťan Hedera helix",
    ],
    2: [
        "Ranunculus acris",
        "Ranunculus repens",
        "Ranunculus bulbosus",
        "pryskyřník prudký",
    ],
}

PLANT_TITLE_BLOCKLIST = {
    1: ("inflorescence", "inflorescences", "flower", "flowers", "fruit", "berries"),
    2: ("glacialis",),
}

try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

BAD_TITLE_PARTS = (
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
    " hybrid",
    " garden",
    " ornamental",
    " svg",
)

BAD_EXTENSIONS = (".svg", ".gif", ".tif", ".tiff", ".pdf", ".djvu")


def request_json(url, params, retries=5):
    encoded = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{encoded}",
        headers={"User-Agent": USER_AGENT},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if attempt == retries - 1:
                raise RuntimeError(f"Request failed: {error}") from error
            retry_after = int(error.headers.get("Retry-After", "0") or "0")
            wait = retry_after or (8 * (attempt + 1) if error.code == 429 else 2 * (attempt + 1))
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == retries - 1:
                raise RuntimeError(f"Request failed: {error}") from error
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
                raise RuntimeError(f"Download failed for {url}: {error}") from error
            retry_after = int(error.headers.get("Retry-After", "0") or "0")
            wait = retry_after or (10 * (attempt + 1) if error.code == 429 else 2 * (attempt + 1))
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == retries - 1:
                raise RuntimeError(f"Download failed for {url}: {error}") from error
            time.sleep(1.5 * (attempt + 1))


def slugify(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value or "plant"


def clean_html(value):
    value = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(value).strip()


def without_tracking_query(url):
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def parse_plants(markdown):
    plants = []
    for line in markdown.splitlines():
        match = re.match(r"^(\d+)\.\s+(.+)$", line.strip())
        if not match:
            continue

        number = int(match.group(1))
        rest = match.group(2)
        latin_chunks = re.findall(r"_([^_]+)_", rest)
        czech = rest.split("_", 1)[0].strip()

        latin_names = []
        for chunk in latin_chunks:
            latin_names.extend(part.strip() for part in chunk.split("/") if part.strip())

        note_text = re.sub(r"_[^_]+_", "", rest)
        note_text = note_text.replace(czech, "", 1).strip()
        note_text = re.sub(r"^\s*/\s*", "", note_text).strip()

        plants.append(
            {
                "number": number,
                "czech": czech,
                "latin": latin_names,
                "note": note_text,
                "slug": f"{number:03d}-{slugify(czech)}",
            }
        )
    return plants


def commons_search(query, limit=20):
    data = request_json(
        COMMONS_API,
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
        },
    )
    pages = data.get("query", {}).get("pages", [])
    pages.sort(key=lambda page: page.get("index", 10_000))
    return pages


def is_good_candidate(page, plant):
    title = page.get("title", "")
    title_lower = title.lower()
    title_without_prefix = title_lower.removeprefix("file:")
    if any(term in title_lower for term in PLANT_TITLE_BLOCKLIST.get(plant["number"], ())):
        return False
    if any(title_lower.endswith(extension) for extension in BAD_EXTENSIONS):
        return False
    if any(part in title_lower for part in BAD_TITLE_PARTS):
        return False
    if "'" in title_without_prefix or '"' in title_without_prefix:
        return False
    for latin in plant["latin"]:
        latin_lower = re.escape(latin.lower())
        if re.search(rf"\bon\s+{latin_lower}\b", title_lower):
            return False

    info = (page.get("imageinfo") or [{}])[0]
    mime = info.get("mime", "")
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        return False

    width = info.get("width") or 0
    height = info.get("height") or 0
    if width < 350 or height < 350:
        return False

    metadata = info.get("extmetadata") or {}
    license_short = clean_html(metadata.get("LicenseShortName", {}).get("value"))
    if "non-free" in license_short.lower():
        return False

    return True


def image_extension(url, mime):
    path = urllib.parse.urlparse(url).path.lower()
    suffix = Path(path).suffix
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    if mime == "image/png":
        return ".png"
    if mime == "image/webp":
        return ".webp"
    return ".jpg"


def metadata_from_page(page, local_path):
    info = page["imageinfo"][0]
    metadata = info.get("extmetadata") or {}
    title = page.get("title", "")
    source_url = info.get("descriptionurl")
    download_url = without_tracking_query(info.get("thumburl") or info.get("url"))
    return {
        "title": title,
        "local_path": str(local_path),
        "source_url": source_url,
        "download_url": download_url,
        "mime": info.get("mime"),
        "width": info.get("thumbwidth") or info.get("width"),
        "height": info.get("thumbheight") or info.get("height"),
        "author": clean_html(metadata.get("Artist", {}).get("value")),
        "license": clean_html(metadata.get("LicenseShortName", {}).get("value")),
        "license_url": clean_html(metadata.get("LicenseUrl", {}).get("value")),
        "credit": clean_html(metadata.get("Credit", {}).get("value")),
    }


def plant_queries(plant):
    if plant["number"] in PREFERRED_QUERIES:
        return PREFERRED_QUERIES[plant["number"]]

    queries = []
    for latin in plant["latin"]:
        queries.extend(
            [
                f"{latin} plant",
                f"{latin} flower",
                f"{latin} leaves",
                f"{plant['czech']} {latin}",
            ]
        )
    queries.append(plant["czech"])
    return queries


def fetch_images_for_plant(plant, images_root, pause, dry_run=False):
    seen_titles = set()
    chosen = []
    considered_queries = []

    for query in plant_queries(plant):
        if len(chosen) >= IMAGE_LIMIT:
            break
        considered_queries.append(query)
        try:
            pages = commons_search(query)
        except RuntimeError as error:
            print(f"  ! {plant['number']} {plant['czech']}: {error}")
            continue
        time.sleep(pause)

        for page in pages:
            if len(chosen) >= IMAGE_LIMIT:
                break
            title = page.get("title")
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            if not is_good_candidate(page, plant):
                continue

            info = page["imageinfo"][0]
            download_url = without_tracking_query(info.get("thumburl") or info.get("url"))
            suffix = image_extension(download_url, info.get("mime", ""))
            local_dir = images_root / plant["slug"]
            local_path = local_dir / f"{len(chosen) + 1:02d}{suffix}"
            item = metadata_from_page(page, local_path)
            item["query"] = query

            if not dry_run and not local_path.exists():
                try:
                    download(download_url, local_path)
                    time.sleep(pause)
                except RuntimeError as error:
                    print(f"  ! {plant['number']} {plant['czech']}: {error}", flush=True)
                    continue
            chosen.append(item)

    return chosen, considered_queries


def render_markdown(plants, output):
    lines = [
        "# Seznam divokých rostlin ČR pro poznávání - obrázky",
        "",
        "Obrázky jsou stažené nebo odkazované z Wikimedia Commons. U každého obrázku je uveden původní soubor a licence.",
        "",
    ]
    by_status = {"complete": 0, "partial": 0, "missing": 0}
    for plant in plants:
        images = plant.get("images", [])
        if len(images) >= IMAGE_LIMIT:
            by_status["complete"] += 1
        elif images:
            by_status["partial"] += 1
        else:
            by_status["missing"] += 1

        latin = " / ".join(plant["latin"])
        lines.append(f"## {plant['number']}. {plant['czech']} _{latin}_")
        if plant.get("note"):
            lines.append("")
            lines.append(f"Poznámka: {plant['note']}")
        lines.append("")

        if not images:
            lines.append("_Nepodařilo se automaticky najít vhodný obrázek._")
            lines.append("")
            continue

        for index, image in enumerate(images, 1):
            relative_path = Path(image["local_path"]).as_posix()
            lines.append(f"![{plant['czech']} {index}]({relative_path})")
            source = image.get("source_url") or ""
            license_name = image.get("license") or "licence neuvedena"
            author = image.get("author") or "autor neuveden"
            lines.append(f"{index}. [{image['title']}]({source}) - {license_name}, {author}")
            lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8")
    return by_status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="seznam-divokych-rostlin.md")
    parser.add_argument("--images-dir", default="images")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--markdown-output", default="seznam-divokych-rostlin-obrazky.md")
    parser.add_argument("--pause", type=float, default=0.75)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    images_root = Path(args.images_dir)
    data_root = Path(args.data_dir)
    data_root.mkdir(parents=True, exist_ok=True)

    plants = parse_plants(input_path.read_text(encoding="utf-8"))
    (data_root / "plants.json").write_text(json.dumps(plants, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.limit:
        plants = plants[: args.limit]

    for index, plant in enumerate(plants, 1):
        print(f"[{index}/{len(plants)}] {plant['number']}. {plant['czech']} ({', '.join(plant['latin'])})", flush=True)
        images, queries = fetch_images_for_plant(plant, images_root, args.pause, dry_run=args.dry_run)
        plant["images"] = images
        plant["queries"] = queries
        plant["status"] = "complete" if len(images) >= IMAGE_LIMIT else "partial" if images else "missing"
        print(f"  -> {len(images)} image(s), {plant['status']}", flush=True)

        (data_root / "plant-images.json").write_text(
            json.dumps(plants, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    status_counts = render_markdown(plants, Path(args.markdown_output))
    missing = [f"{plant['number']}. {plant['czech']}" for plant in plants if plant["status"] != "complete"]
    report = {
        "total": len(plants),
        "status_counts": status_counts,
        "needs_review": missing,
    }
    (data_root / "image-fetch-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
