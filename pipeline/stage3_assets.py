"""Stage 3 - figure assets.

Most figures in this book are vector diagrams, so `pdfimages` would only
recover the handful of raster photographs. Everything is instead cropped from
a rasterised page using the figure bbox that Stage 2 agreed on, which works
uniformly for vector and raster content.

Each figure is written as a PNG and a WebP, and every entry in assets.json
carries the dimensions, source page, and alt text that Gate 4 checks.

Run:  python -m pipeline.stage3_assets --chapter 2
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import fitz
from PIL import Image

from . import config

# A little breathing room so strokes on the bbox edge are not clipped.
PAD_POINTS = 4.0

# Below this, a crop is a rule or a stray mark rather than a figure.
MIN_SIDE_POINTS = 24.0


def _label_slug(label: str | None, fallback: str) -> str:
    if not label:
        return fallback
    return "fig-" + label.replace(".", "-")


def crop_figure(
    doc: fitz.Document,
    page_no: int,
    bbox: tuple[float, float, float, float],
    out_png: Path,
    out_webp: Path,
) -> tuple[int, int]:
    page = doc[page_no - 1]
    clip = fitz.Rect(*bbox)
    clip.x0 = max(page.rect.x0, clip.x0 - PAD_POINTS)
    clip.y0 = max(page.rect.y0, clip.y0 - PAD_POINTS)
    clip.x1 = min(page.rect.x1, clip.x1 + PAD_POINTS)
    clip.y1 = min(page.rect.y1, clip.y1 + PAD_POINTS)

    pixmap = page.get_pixmap(dpi=config.FIGURE_DPI, clip=clip)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(out_png)

    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    image.save(out_webp, format="WEBP", quality=90, method=6)
    return image.width, image.height


def build_assets(chapter: int, pdf: Path) -> list[dict]:
    cid = config.chapter_id(chapter)
    model_path = config.RECONCILE_DIR / f"{cid}.json"
    if not model_path.exists():
        raise SystemExit(f"FATAL: {model_path} missing. Run stage2 first.")
    model = json.loads(model_path.read_text())

    triage = json.loads(config.TRIAGE_JSON.read_text())
    offset = triage["boundary_map"]["printed_to_pdf_offset"]

    doc = fitz.open(pdf)
    out_dir = config.STATIC_IMG / cid
    entries: list[dict] = []
    seen_hashes: dict[str, str] = {}

    figures = [b for b in model["blocks"] if b.get("type") == "figure"]
    for index, block in enumerate(figures, start=1):
        bbox = block.get("bbox")
        if not bbox:
            entries.append(
                {
                    "label": block.get("label"),
                    "issue": "no_bbox",
                    "page": block.get("page"),
                }
            )
            continue

        # Measured after padding, since that is what is actually rendered.
        # A raw bbox a little under the threshold (a thin single-row diagram
        # such as Figure 12.14's 4-node path, 23pt tall before padding) would
        # otherwise be rejected as a stray mark even though the crop it
        # produces looks exactly like every other accepted figure.
        width_pt = bbox[2] - bbox[0] + 2 * PAD_POINTS
        height_pt = bbox[3] - bbox[1] + 2 * PAD_POINTS
        if width_pt < MIN_SIDE_POINTS or height_pt < MIN_SIDE_POINTS:
            entries.append(
                {
                    "label": block.get("label"),
                    "issue": "too_small_in_pdf",
                    "page": block.get("page"),
                    "bbox": bbox,
                }
            )
            continue

        slug = _label_slug(block.get("label"), f"fig-unlabelled-{index:02d}")
        png = out_dir / f"{slug}.png"
        webp = out_dir / f"{slug}.webp"
        pixel_width, pixel_height = crop_figure(doc, block["page"], bbox, png, webp)

        digest = hashlib.sha256(png.read_bytes()).hexdigest()
        entry = {
            "label": block.get("label"),
            "block": block["id"],
            "file": f"img/{cid}/{png.name}",
            "webp": f"img/{cid}/{webp.name}",
            "width": pixel_width,
            "height": pixel_height,
            "page": block["page"],
            "printed_page": block["page"] - offset,
            "bbox": bbox,
            "alt": block.get("alt") or "",
            "caption": block.get("caption") or "",
            "sha256": digest,
            "source": block.get("source", ""),
        }
        if digest in seen_hashes:
            # Two figures rendering to identical bytes means the crop window
            # did not move -- a silent bug Gate 4 also checks for.
            entry["issue"] = f"duplicate_of_{seen_hashes[digest]}"
        else:
            seen_hashes[digest] = block.get("label") or slug
        entries.append(entry)

    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 3: crop figure assets")
    ap.add_argument("--chapter", type=int, action="append", required=True)
    ap.add_argument("--pdf", type=Path, default=config.DEFAULT_PDF)
    args = ap.parse_args()

    all_assets: dict = {}
    if config.ASSETS_JSON.exists():
        all_assets = json.loads(config.ASSETS_JSON.read_text())

    for number in args.chapter:
        entries = build_assets(number, args.pdf)
        all_assets[config.chapter_id(number)] = entries
        problems = [e for e in entries if e.get("issue")]
        print(
            f"ch{number:02d}: {len(entries)} figures cropped, "
            f"{len(problems)} with issues"
        )
        for problem in problems:
            print(f"    {problem.get('label')}: {problem['issue']}")

    config.ASSETS_JSON.parent.mkdir(parents=True, exist_ok=True)
    config.ASSETS_JSON.write_text(json.dumps(all_assets, indent=1, ensure_ascii=False))
    print(f"wrote {config.ASSETS_JSON.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
