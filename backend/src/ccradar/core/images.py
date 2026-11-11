"""Uploaded images (partner logos, 8.13): size and format checks, and SVG sanitising.

Accepted: PNG, WebP and SVG, at most ``MAX_BYTES``. Formats are recognised from the
bytes, never from the file name or content type. SVGs are rebuilt from an allow-list of
drawing elements and attributes: no scripts, event handlers, external references,
``foreignObject``, DOCTYPEs or entities. Logos are shown with ``<img>``, where SVG
scripts never run anyway; sanitising is the second guard.
"""

from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET

MAX_BYTES = 200_000
SVG_NS = "http://www.w3.org/2000/svg"
ELEMENTS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "defs", "lineargradient", "radialgradient", "stop", "title", "desc", "clippath", "mask",
}  # fmt: skip
ATTRIBUTES = {
    "viewbox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap", "stroke-linejoin",
    "opacity", "transform", "id", "class", "offset", "stop-color", "stop-opacity", "gradientunits", "gradienttransform",
    "clip-path", "mask", "font-family", "font-size", "font-weight", "text-anchor", "xmlns", "version", "preserveaspectratio",
    "clip-rule", "dominant-baseline", "letter-spacing",
}  # fmt: skip
URL_REF = re.compile(r"url\(\s*['\"]?(?!#)", re.I)


class ImageError(ValueError):
    pass


def kind_of(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:1000].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        return "image/svg+xml"
    raise ImageError("Use a PNG, WebP or SVG image.")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def sanitize_svg(data: bytes) -> bytes:
    text = data.decode("utf-8", "strict")
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        raise ImageError("SVGs with DOCTYPE or entity declarations are not accepted.")
    try:
        root = ET.fromstring(text)  # noqa: S314 - no DOCTYPE/ENTITY possible here
    except ET.ParseError as exc:
        raise ImageError("The SVG could not be read.") from exc
    if _local(root.tag) != "svg":
        raise ImageError("The file is not an SVG image.")

    def clean(el: ET.Element) -> ET.Element | None:
        name = _local(el.tag)
        if name not in ELEMENTS:
            return None
        out = ET.Element(f"{{{SVG_NS}}}{name}")
        for k, v in el.attrib.items():
            key = _local(k)
            if key not in ATTRIBUTES or key.startswith("on"):
                continue
            if URL_REF.search(v) or "javascript:" in v.lower():
                continue
            out.set(key, v)
        out.text = el.text if name in ("text", "tspan", "title", "desc") else None
        for child in el:
            c = clean(child)
            if c is not None:
                out.append(c)
                c.tail = None
        return out

    cleaned = clean(root)
    assert cleaned is not None  # noqa: S101 - root is an svg element
    ET.register_namespace("", SVG_NS)
    return ET.tostring(cleaned, encoding="utf-8")


def check_logo(data: bytes) -> tuple[str, bytes]:
    """(media type, safe bytes) or ImageError."""
    if not data:
        raise ImageError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ImageError(f"The image is too large ({MAX_BYTES // 1000} KB at most).")
    kind = kind_of(data)
    if kind == "image/svg+xml":
        data = sanitize_svg(data)
    return kind, data


def data_url(kind: str, data: bytes) -> str:
    return f"data:{kind};base64,{base64.b64encode(data).decode()}"
