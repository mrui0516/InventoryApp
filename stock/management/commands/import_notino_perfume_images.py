import re
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import html

from django.core.files import File
from django.core.management.base import BaseCommand
from django.db.models import Prefetch
from django.utils.text import slugify

from PIL import Image

from stock.models import Category, Product, ProductImage


SEARCH_URL = "https://r.jina.ai/http://https://www.notino.pt/search.asp?exps={query}"
DETAIL_URL = "https://r.jina.ai/http://{product_url}"
BING_IMAGE_SEARCH_URL = "https://www.bing.com/images/search?q={query}"
DEFAULT_ROOT = Path(r"F:\Perfumes")
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0 Safari/537.36",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}

SEARCH_RESULT_RE = re.compile(
    r"!\[Image\s+\d+:\s*(?P<title>[^\]]+)\]\((?P<image>https://cdn\.notinoimg\.com/[^\)]+)\)"
    r".*?\]\((?P<url>https://www\.notino\.pt/[^\)\s]+)\)",
    re.IGNORECASE | re.DOTALL,
)
DETAIL_IMAGE_RE = re.compile(
    r"https://cdn\.notinoimg\.com/(?P<path>(?:detail|list)[^)\s]+?\.(?:jpg|jpeg|png|webp))",
    re.IGNORECASE,
)
BING_IMAGE_RE = re.compile(
    r'murl&quot;:&quot;(?P<image>https?://[^"&]+?)&quot;.*?'
    r'&quot;t&quot;:&quot;(?P<title>[^"]*?)&quot;.*?'
    r'&quot;desc&quot;:&quot;(?P<desc>[^"]*?)&quot;',
    re.IGNORECASE | re.DOTALL,
)

TOKEN_STOPWORDS = {
    "eau", "de", "parfum", "perfume", "para", "homens", "homem", "mulheres", "mulher",
    "unissexo", "ml", "edp", "spray", "tester", "the", "and", "for", "oil", "body", "mist",
}


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def build_tokens(*parts: str) -> set[str]:
    tokens = set()
    for part in parts:
        for token in normalize_text(part).split():
            if len(token) < 2 or token in TOKEN_STOPWORDS:
                continue
            tokens.add(token)
    return tokens


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def build_queries(product: Product) -> list[str]:
    base_parts = [product.brand, product.model, product.name, product.spec]
    compact = " ".join(part.strip() for part in base_parts if part)
    compact = re.sub(r"(\d+)\s*ml", r"\1 ml", compact, flags=re.IGNORECASE)

    variants = [
        compact,
        " ".join(part.strip() for part in [product.brand, product.model, product.name] if part),
        " ".join(part.strip() for part in [product.brand, product.name] if part),
        " ".join(part.strip() for part in [product.brand, product.model] if part),
        product.display_name.replace(" - ", " "),
    ]
    return dedupe_preserve_order(variants)


def fetch_text(url: str) -> str:
    last_error = None
    for attempt in range(4):
        try:
            request = Request(url, headers=REQUEST_HEADERS)
            with urlopen(request, timeout=45) as response:
                payload = response.read().decode("utf-8", errors="replace")
            time.sleep(0.7)
            return payload
        except HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except (URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError("Unable to fetch text")


def fetch_bytes(url: str) -> bytes:
    last_error = None
    for attempt in range(4):
        try:
            request = Request(url, headers=REQUEST_HEADERS)
            with urlopen(request, timeout=45) as response:
                payload = response.read()
            time.sleep(0.7)
            return payload
        except HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except (URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError("Unable to fetch image bytes")


def parse_search_results(markdown: str) -> list[dict]:
    results = []
    for match in SEARCH_RESULT_RE.finditer(markdown):
        results.append({
            "title": match.group("title").strip(),
            "image_url": match.group("image").strip(),
            "product_url": match.group("url").strip(),
        })
    return results


def parse_detail_images(markdown: str) -> list[str]:
    seen = set()
    urls = []
    for match in DETAIL_IMAGE_RE.finditer(markdown):
        image_url = "https://cdn.notinoimg.com/" + match.group("path").lstrip("/")
        if "/detail" not in image_url:
            continue
        if image_url in seen:
            continue
        seen.add(image_url)
        urls.append(image_url)
    return urls


def score_result(product: Product, result: dict) -> int:
    product_tokens = build_tokens(product.brand, product.model, product.name, product.spec, product.color)
    title_tokens = build_tokens(result["title"], result["product_url"])
    if not title_tokens:
        return 0

    shared = product_tokens & title_tokens
    score = len(shared) * 3

    brand_normalized = normalize_text(product.brand)
    model_normalized = normalize_text(product.model or "")
    name_normalized = normalize_text(product.name)
    title_normalized = normalize_text(result["title"])

    if brand_normalized and brand_normalized in title_normalized:
        score += 4
    if model_normalized and model_normalized in title_normalized:
        score += 3
    if name_normalized and name_normalized in title_normalized:
        score += 2

    return score


def score_external_image(product: Product, candidate: dict) -> int:
    score = score_result(product, {
        "title": f"{candidate.get('title', '')} {candidate.get('desc', '')}",
        "product_url": candidate.get("image_url", ""),
    })
    image_url = normalize_text(candidate.get("image_url", ""))
    if "box" in image_url:
        score += 2
    if "bottle" in image_url:
        score += 2
    return score


def choose_candidate(product: Product, results: list[dict]) -> dict | None:
    if not results:
        return None

    ranked = sorted(
        results,
        key=lambda item: (
            score_result(product, item),
            len(item["title"]),
        ),
        reverse=True,
    )
    best = ranked[0]
    return best if score_result(product, best) >= 4 else None


def parse_bing_image_results(html_text: str) -> list[dict]:
    results = []
    seen = set()
    for match in BING_IMAGE_RE.finditer(html_text):
        image_url = html.unescape(match.group("image")).strip()
        if image_url in seen:
            continue
        seen.add(image_url)
        results.append({
            "image_url": image_url,
            "title": html.unescape(match.group("title")).strip(),
            "desc": html.unescape(match.group("desc")).strip(),
        })
    return results


def choose_external_candidate(product: Product, results: list[dict]) -> dict | None:
    if not results:
        return None
    ranked = sorted(
        results,
        key=lambda item: (
            score_external_image(product, item),
            len(item.get("title", "")),
        ),
        reverse=True,
    )
    best = ranked[0]
    return best if score_external_image(product, best) >= 4 else None


def fetch_external_candidate(product: Product) -> dict | None:
    external_queries = dedupe_preserve_order([
        f"{product.brand} {product.model or ''} {product.name} perfume box bottle",
        f"{product.brand} {product.name} perfume box bottle",
        f"{product.display_name.replace(' - ', ' ')} perfume box bottle",
    ])
    for query in external_queries:
        search_html = fetch_text(BING_IMAGE_SEARCH_URL.format(query=quote(query)))
        results = parse_bing_image_results(search_html)
        candidate = choose_external_candidate(product, results)
        if candidate:
            candidate["query"] = query
            candidate["score"] = score_external_image(product, candidate)
            return candidate
    return None


def fetch_candidate(product: Product) -> dict | None:
    for query in build_queries(product):
        search_markdown = fetch_text(SEARCH_URL.format(query=quote(query)))
        results = parse_search_results(search_markdown)
        candidate = choose_candidate(product, results)
        if candidate:
            try:
                detail_markdown = fetch_text(DETAIL_URL.format(product_url=candidate["product_url"]))
                detail_images = parse_detail_images(detail_markdown)
            except (HTTPError, URLError, TimeoutError):
                detail_images = []

            candidate["detail_images"] = detail_images
            candidate["source"] = "notino"
            candidate["chosen_image_url"] = detail_images[1] if len(detail_images) > 1 else ""
            candidate["query"] = query
            candidate["score"] = score_result(product, candidate)
            if candidate["chosen_image_url"]:
                return candidate

    fallback = fetch_external_candidate(product)
    if fallback:
        fallback["source"] = "external"
        fallback["product_url"] = ""
        fallback["chosen_image_url"] = fallback["image_url"]
        fallback["detail_images"] = []
        return fallback
    return None


def build_product_folder(root_dir: Path, product: Product) -> Path:
    brand_slug = slugify(product.brand) or "unknown-brand"
    product_slug = slugify(product.display_name) or slugify(product.name) or product.barcode
    return root_dir / brand_slug / f"{product_slug}-{product.barcode}"


def is_auto_import_image(product_image: ProductImage) -> bool:
    name = (product_image.image.name or "").lower()
    return "notino_cover" in name or "notino_packaging" in name or "fallback_packaging" in name


def is_legacy_auto_import_image(product_image: ProductImage) -> bool:
    name = (product_image.image.name or "").lower()
    return "notino_cover" in name


def clear_auto_import_assets(product: Product, folder: Path) -> None:
    auto_images = [image for image in product.images.all() if is_auto_import_image(image)]
    for image in auto_images:
        image.delete()

    if folder.exists():
        for pattern in ("notino_cover*.jpg", "notino_packaging*.jpg", "fallback_packaging*.jpg"):
            for file_path in folder.glob(pattern):
                file_path.unlink(missing_ok=True)


class Command(BaseCommand):
    help = "Import perfume photos that prioritize package-plus-bottle images, replacing prior auto-imported single-bottle photos when needed."

    def add_arguments(self, parser):
        parser.add_argument("--category", default="Perfumes", help="Product category name to process.")
        parser.add_argument("--barcode", help="Only process a single product barcode.")
        parser.add_argument("--limit", type=int, help="Maximum number of products to process.")
        parser.add_argument("--root-dir", default=str(DEFAULT_ROOT), help="Root folder to save local JPG copies.")
        parser.add_argument("--force", action="store_true", help="Re-download even if the product already has images.")
        parser.add_argument("--refresh-auto", action="store_true", default=True, help="Also refresh products whose current images were auto-imported by this command.")
        parser.add_argument("--commit", action="store_true", help="Actually write files and create ProductImage rows.")

    def handle(self, *args, **options):
        category_name = options["category"]
        root_dir = Path(options["root_dir"])
        barcode = (options.get("barcode") or "").strip()
        limit = options.get("limit")
        force = bool(options.get("force"))
        refresh_auto = bool(options.get("refresh_auto"))
        commit = bool(options.get("commit"))

        try:
            category = Category.objects.get(name__iexact=category_name)
        except Category.DoesNotExist:
            self.stderr.write(self.style.ERROR(f'Category "{category_name}" not found.'))
            return

        qs = (
            Product.objects
            .filter(category=category)
            .prefetch_related(Prefetch("images", queryset=ProductImage.objects.order_by("id")))
            .order_by("brand", "model", "name", "id")
        )

        if barcode:
            qs = qs.filter(barcode=barcode)
        qs = list(qs)

        if not force:
            filtered = []
            for product in qs:
                images = list(product.images.all())
                if not images:
                    filtered.append(product)
                    continue
                if refresh_auto and images and all(is_legacy_auto_import_image(image) for image in images):
                    filtered.append(product)
            qs = filtered

        if limit:
            qs = list(qs)[:limit]

        if not qs:
            self.stdout.write(self.style.WARNING("No matching products to process."))
            return

        if commit:
            root_dir.mkdir(parents=True, exist_ok=True)

        stats = {
            "processed": 0,
            "matched": 0,
            "imported": 0,
            "skipped": 0,
            "failed": 0,
        }

        for product in qs:
            stats["processed"] += 1
            label = f"{product.id} | {product.display_name}"
            try:
                candidate = fetch_candidate(product)
            except Exception as exc:
                stats["failed"] += 1
                self.stderr.write(self.style.ERROR(f"[FAILED] {label}: search error {exc}"))
                time.sleep(1.0)
                continue

            if not candidate:
                stats["skipped"] += 1
                self.stdout.write(self.style.WARNING(f"[SKIP] {label}: no confident Notino match"))
                time.sleep(1.0)
                continue

            stats["matched"] += 1
            image_url = candidate["chosen_image_url"]
            product_url = candidate["product_url"]
            message = (
                f"[MATCH] {label}\n"
                f"  source: {candidate['source']}\n"
                f"  query: {candidate['query']}\n"
                f"  title: {candidate['title']}\n"
                f"  score: {candidate['score']}\n"
                f"  page: {product_url or '-'}\n"
                f"  image: {image_url}"
            )

            if not commit:
                self.stdout.write(message)
                time.sleep(1.0)
                continue

            try:
                folder = build_product_folder(root_dir, product)
                folder.mkdir(parents=True, exist_ok=True)

                existing_images = list(product.images.all())
                if force or (existing_images and all(is_auto_import_image(image) for image in existing_images)):
                    clear_auto_import_assets(product, folder)

                image = Image.open(BytesIO(fetch_bytes(image_url))).convert("RGB")
                jpg_name = "notino_packaging.jpg" if candidate["source"] == "notino" else "fallback_packaging.jpg"
                jpg_path = folder / jpg_name
                image.save(jpg_path, format="JPEG", quality=92)

                with jpg_path.open("rb") as handle:
                    product_image = ProductImage(product=product)
                    product_image.image.save(jpg_path.name, File(handle), save=True)

                stats["imported"] += 1
                self.stdout.write(self.style.SUCCESS(f"[IMPORTED] {label} -> {jpg_path}"))
            except Exception as exc:
                stats["failed"] += 1
                self.stderr.write(self.style.ERROR(f"[FAILED] {label}: import error {exc}"))
            finally:
                time.sleep(1.0)

        summary = (
            f"processed={stats['processed']} matched={stats['matched']} imported={stats['imported']} "
            f"skipped={stats['skipped']} failed={stats['failed']} commit={commit}"
        )
        self.stdout.write(self.style.SUCCESS(summary))
