"""HTML/CSS + Chromium card renderer.

This module owns deterministic typography/layout only. It does not claim OCR or
semantic quality validation; those are separate pipeline gates.
"""
from __future__ import annotations

import base64
import html
import logging
import uuid
from pathlib import Path

from ..adapters.base import Platform
from .visual_specs import build_infographic_components, get_visual_platform_spec

logger = logging.getLogger(__name__)

CARD_SIZE = (1080, 1440)
THEMES = {
    "knowledge_infographic": {"bg": "#F3F5F7", "fg": "#17212B", "accent": "#2563EB", "muted": "#526171"},
    "editorial_magazine": {"bg": "#F4F0E8", "fg": "#25231F", "accent": "#A34F36", "muted": "#6F685F"},
    "business_poster": {"bg": "#101827", "fg": "#FFFFFF", "accent": "#F2B84B", "muted": "#D2D8E2"},
    "lifestyle": {"bg": "#F2EEE8", "fg": "#34332F", "accent": "#66816D", "muted": "#77766E"},
}
def _relative_luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    first, second = sorted((_relative_luminance(foreground), _relative_luminance(background)), reverse=True)
    return (first + 0.05) / (second + 0.05)

def _contrast_text(background: str) -> str:
    """Choose black or white text with the stronger WCAG contrast."""
    return max(("#000000", "#FFFFFF"), key=lambda color: _contrast_ratio(color, background))


def _validate_theme_contrast(theme: str, has_background_image: bool, palette_override: dict | None = None) -> float:
    palette = {**THEMES.get(theme, THEMES["knowledge_infographic"]), **(palette_override or {})}
    if has_background_image and theme == "business_poster":
        # The 58% minimum black overlay bounds the brightest possible image pixel.
        background = "#666B75"
    elif has_background_image:
        # The 72% minimum white overlay bounds the darkest possible image pixel.
        background = "#B8B8B8"
    else:
        background = palette["bg"]
    ratio = _contrast_ratio(palette["fg"], background)
    if ratio < 4.5:
        raise ValueError(f"视觉对比度硬门失败：主题 {theme} 的最差对比度 {ratio:.2f}:1，低于 4.5:1")
    return round(ratio, 2)


def _split_paragraphs(text: str, limit: int = 150) -> list[str]:
    """Split on paragraph/sentence boundaries; never discard source text."""
    result: list[str] = []
    for paragraph in (text or "").splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > limit:
            cut = max(paragraph.rfind(mark, 0, limit) for mark in "。！？；，、 ")
            if cut < limit // 2:
                cut = limit
            else:
                cut += 1
            result.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            result.append(paragraph)
    return result or [""]


def _pages(body: str, max_pages: int, capacity: int = 5,
           max_page_chars: int = 320) -> list[list[str]]:
    """Pack paragraphs by both count and text volume to avoid fixed-count overflow.

    Paragraph count alone is not a useful proxy for rendered height: five 150-character
    chunks can exceed the available canvas by a wide margin. Keep every source character
    while splitting pages at a conservative text budget; Chromium remains the final
    geometry authority and rejects any residual overflow.
    """
    if max_pages < 2:
        raise ValueError("max_pages must allow a cover and at least one body page")
    if capacity < 1 or max_page_chars < 1:
        raise ValueError("page capacity and text budget must be positive")
    blocks = _split_paragraphs(body)
    pages: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for block in blocks:
        if current and (len(current) >= capacity or current_chars + len(block) > max_page_chars):
            pages.append(current)
            current = []
            current_chars = 0
        current.append(block)
        current_chars += len(block)
    if current:
        pages.append(current)
    pages = pages or [[""]]
    if len(pages) + 1 > max_pages:
        raise ValueError(
            f"正文需要 {len(pages) + 1} 页，超过配置上限 {max_pages} 页；"
            "请先由视觉策划提炼内容，不允许静默截断"
        )
    return pages


def _document(*, title: str, subtitle: str, paragraphs: list[str], footer: str,
              page_text: str, theme: str, cover: bool, width: int, height: int,
              background_data_uri: str = "", palette_override: dict | None = None,
              font_family: str = "Noto Sans CJK SC") -> str:
    overrides = palette_override or {}
    component_style = str(overrides.get("component_style") or "card")
    if component_style not in {"card", "minimal", "editorial"}:
        raise ValueError("不支持的组件样式")
    palette = {
        **THEMES.get(theme, THEMES["knowledge_infographic"]),
        **{key: value for key, value in overrides.items() if key in {"bg", "fg", "accent", "muted"}},
    }
    component_style_css = {
        "card": "",
        "minimal": ".visual-component{padding:14px 0;border:0;border-radius:0;background:transparent}.visual-stat{border-left:6px solid " + palette["accent"] + "}",
        "editorial": ".visual-component{padding:18px 22px;border:0;border-bottom:2px solid " + palette["muted"] + ";border-radius:0;background:transparent}.visual-quote{border-left:6px solid " + palette["accent"] + "}",
    }[component_style]
    allowed_fonts = {"Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", "Arial"}
    if font_family not in allowed_fonts:
        raise ValueError("不支持的字体族")
    safe_title = html.escape(title or "")
    safe_subtitle = html.escape(subtitle or "")
    safe_footer = html.escape(footer or "")
    component_markup = []
    for component in build_infographic_components(paragraphs):
        safe_text = html.escape(component.text)
        if component.kind == "step":
            rest = component.text[len(component.label):].lstrip(" ：:、-")
            component_markup.append(
                f'<div class="visual-component visual-step"><span class="step-label">{html.escape(component.label)}</span>'
                f'<p>{html.escape(rest or component.text)}</p></div>'
            )
        elif component.kind == "stat":
            safe_value = html.escape(component.value)
            highlighted = safe_text.replace(safe_value, f"<strong>{safe_value}</strong>", 1)
            component_markup.append(
                f'<div class="visual-component visual-stat"><p>{highlighted}</p></div>'
            )
        elif component.kind == "quote":
            component_markup.append(
                f'<blockquote class="visual-component visual-quote"><p>{safe_text}</p></blockquote>'
            )
        else:
            component_markup.append(
                f'<div class="visual-component visual-text"><p>{safe_text}</p></div>'
            )
    body = "".join(component_markup)
    overlay = (
        "rgba(8,14,24,.60),rgba(8,14,24,.68)"
        if theme == "business_poster"
        else "rgba(255,255,255,.72),rgba(255,255,255,.82)"
    )
    background_css = (
        f"background-image:linear-gradient(180deg,{overlay}),url('{background_data_uri}');"
        "background-size:cover;background-position:center;"
        if background_data_uri else ""
    )
    if cover:
        content = (
            f'<div class="eyebrow">{safe_subtitle}</div>'
            f'<h1>{safe_title}</h1><div class="rule"></div>'
        )
    else:
        content = f'<h2>{safe_title}</h2><div class="body">{body}</div>'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{{box-sizing:border-box}} html,body{{margin:0;width:{width}px;height:{height}px}}
body{{font-family:"{font_family}","Source Han Sans SC","Microsoft YaHei",sans-serif;
background:{palette['bg']};color:{palette['fg']};{background_css}}}
.canvas{{position:relative;width:{width}px;height:{height}px;padding:112px 92px 120px;
overflow:hidden;display:flex;flex-direction:column;justify-content:center}}
.eyebrow{{display:inline-flex;align-self:flex-start;max-width:100%;padding:15px 24px;
border-radius:999px;background:{palette['accent']};color:{_contrast_text(palette['accent'])};font-size:30px;
font-weight:700;overflow-wrap:anywhere;margin-bottom:58px}}
h1{{font-size:82px;line-height:1.22;letter-spacing:-2px;margin:0;font-weight:800;
overflow-wrap:anywhere;word-break:break-word}}
h2{{font-size:54px;line-height:1.25;margin:0 0 42px;font-weight:800}}
.rule{{width:150px;height:8px;background:{palette['accent']};margin-top:58px;border-radius:8px}}
.body{{display:flex;flex-direction:column;gap:20px;overflow:hidden}}
.visual-component{{padding:22px 26px;border-radius:22px;background:rgba(255,255,255,.56);
border:1px solid rgba(23,33,43,.10);overflow-wrap:anywhere;word-break:break-word}}
.visual-component p{{font-size:34px;line-height:1.48;margin:0;overflow-wrap:anywhere;word-break:break-word}}
.visual-stat{{border-left:10px solid {palette['accent']}}}
.visual-stat strong{{display:inline-block;color:{palette['accent']};font-size:48px;font-weight:900;margin:0 5px}}
.visual-step{{display:flex;align-items:flex-start;gap:18px}}
.step-label{{flex:0 0 auto;padding:9px 16px;border-radius:999px;background:{palette['accent']};
color:{_contrast_text(palette['accent'])};font-size:25px;font-weight:800}}
.visual-quote{{margin:0;border-left:10px solid {palette['accent']};font-style:italic}}
.visual-text{{background:rgba(255,255,255,.32)}}
{component_style_css}
.footer{{position:absolute;left:92px;bottom:52px;max-width:760px;color:{palette['muted']};
font-size:22px;line-height:1.35;overflow-wrap:anywhere}}
.page{{position:absolute;right:92px;bottom:52px;color:{palette['muted']};font-size:22px}}
</style></head><body><main class="canvas">{content}
<div class="footer">{safe_footer}</div><div class="page">{html.escape(page_text)}</div>
</main></body></html>"""


def render_card_set(title: str, subtitle: str, body: str, output_dir: Path,
                    platform: Platform = Platform.XHS, footer: str = "",
                    max_pages: int = 9, theme: str = "knowledge_infographic",
                    background_path: Path | None = None, brand_palette: dict | None = None,
                    font_family: str = "Noto Sans CJK SC") -> list[Path]:
    """Render a cover and body pages with Chromium; raises on missing browser/overflow."""
    from playwright.sync_api import sync_playwright

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = _pages(body, max_pages=max_pages)
    spec = get_visual_platform_spec(platform)
    width, height = spec.width, spec.height
    total = len(pages) + 1
    background_data_uri = ""
    if background_path is not None:
        raw_background = Path(background_path).read_bytes()
        suffix = Path(background_path).suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix)
        if not mime:
            raise ValueError("背景素材格式不支持")
        background_data_uri = f"data:{mime};base64,{base64.b64encode(raw_background).decode('ascii')}"
    palette = {**THEMES.get(theme, THEMES["knowledge_infographic"])}
    if brand_palette:
        for key in ("accent", "muted"):
            value = brand_palette.get(key)
            if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
                palette[key] = value
    contrast_ratio = _validate_theme_contrast(theme, bool(background_data_uri), palette)
    logger.info("Visual contrast hard-gate passed theme=%s ratio=%.2f", theme, contrast_ratio)

    documents = [
        _document(title=title, subtitle=subtitle, paragraphs=[], footer=footer,
                  page_text=f"1/{total}", theme=theme, cover=True, width=width, height=height,
                  background_data_uri=background_data_uri, palette_override=palette,
                  font_family=font_family)
    ]
    for index, paragraphs in enumerate(pages, start=2):
        documents.append(_document(
            title=title, subtitle=subtitle, paragraphs=paragraphs, footer=footer,
            page_text=f"{index}/{total}", theme=theme, cover=False, width=width, height=height,
            background_data_uri=background_data_uri, palette_override=palette,
            font_family=font_family
        ))

    paths: list[Path] = []
    expected_texts = [subtitle + title + footer] + [title + "".join(items) + footer for items in pages]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            for document in documents:
                page.set_content(document, wait_until="load")
                page.evaluate("document.fonts.ready")
                font_loaded = page.evaluate(
                    '(font) => document.fonts.check(`36px "${font}"`, "中文字体检查")',
                    font_family,
                )
                if not font_loaded:
                    raise RuntimeError(
                        f"指定字体未安装或不可用：{font_family}；请运行 scripts/install_visual_fonts.sh"
                    )
                layout = page.evaluate("""() => {
                  const root = document.querySelector('.canvas');
                  const rootRect = root.getBoundingClientRect();
                  const selectors = '.eyebrow,h1,.rule,h2,.visual-component,.footer,.page';
                  const elements = [...root.querySelectorAll(selectors)]
                    .filter(el => {
                      const style = getComputedStyle(el);
                      const rect = el.getBoundingClientRect();
                      return style.display !== 'none' && style.visibility !== 'hidden' &&
                        rect.width > 0 && rect.height > 0;
                    });
                  const boxes = elements.map(el => {
                    const r = el.getBoundingClientRect();
                    return {name: el.className || el.tagName.toLowerCase(),
                      left:r.left-rootRect.left, top:r.top-rootRect.top,
                      right:r.right-rootRect.left, bottom:r.bottom-rootRect.top};
                  });
                  const overlaps = [];
                  for (let i=0;i<boxes.length;i++) for (let j=i+1;j<boxes.length;j++) {
                    const a=boxes[i], b=boxes[j];
                    const width=Math.min(a.right,b.right)-Math.max(a.left,b.left);
                    const height=Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top);
                    if (width > 3 && height > 3) overlaps.push({a:a.name,b:b.name,area:Math.round(width*height)});
                  }
                  const outOfBounds = boxes.filter(b => b.left < 0 || b.top < 0 ||
                    b.right > rootRect.width+1 || b.bottom > rootRect.height+1);
                  const overflowNodes = [...root.querySelectorAll('*')].filter(el => {
                    const style = getComputedStyle(el);
                    const clipsX = ['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowX);
                    const clipsY = ['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowY);
                    return (clipsX && el.scrollWidth > el.clientWidth + 2) ||
                      (clipsY && el.scrollHeight > el.clientHeight + 2);
                  }).map(el => el.className || el.tagName.toLowerCase());
                  return {overlaps,outOfBounds,overflowNodes,
                    rootOverflow: root.scrollWidth > root.clientWidth + 1 ||
                      root.scrollHeight > root.clientHeight + 1};
                }""")
                if (layout["rootOverflow"] or layout["overflowNodes"] or
                        layout["outOfBounds"] or layout["overlaps"]):
                    logger.error("Visual layout hard-gate failed: %s", layout)
                    raise ValueError(
                        "视觉页面布局硬门失败：存在溢出、越界或元素重叠，已阻止输出"
                    )
                path = output_dir / f"card_{platform.value}_{uuid.uuid4().hex[:8]}.png"
                page.screenshot(path=str(path), full_page=True, type="png")
                paths.append(path)
        finally:
            browser.close()
    from .visual_ocr import validate_rendered_pages
    ocr_report_path = output_dir / f"visual_ocr_{paths[0].stem}.json"
    ocr_report = validate_rendered_pages(paths, expected_texts, ocr_report_path)
    logger.info(
        "Chromium rendered %d cards for %s; OCR status=%s",
        len(paths), platform.value, ocr_report["status"],
    )
    return paths


def render_single_page(
    *, title: str, subtitle: str, body: str, page_index: int, total_pages: int,
    output_dir: Path, platform: Platform, footer: str, theme: str,
    background_path: Path, brand_palette: dict | None = None,
    font_family: str = "Noto Sans CJK SC",
) -> tuple[Path, str]:
    """Render exactly one existing page; return image path and its expected OCR text."""
    from playwright.sync_api import sync_playwright
    from .visual_ocr import validate_rendered_pages

    if page_index < 0 or total_pages < 2 or page_index >= total_pages:
        raise ValueError("页面索引无效")
    body_pages = _pages(body, max_pages=max(9, total_pages - 1))
    if len(body_pages) + 1 != total_pages:
        raise ValueError("当前正文分页与历史版本不一致，拒绝错误覆盖")
    spec = get_visual_platform_spec(platform)
    width, height = spec.width, spec.height
    raw = Path(background_path).read_bytes()
    suffix = Path(background_path).suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix)
    if not mime:
        raise ValueError("背景素材格式不支持")
    background_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    palette = {**THEMES.get(theme, THEMES["knowledge_infographic"])}
    if brand_palette:
        for key in ("accent", "muted"):
            value = brand_palette.get(key)
            if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
                palette[key] = value
    _validate_theme_contrast(theme, True, palette)
    if page_index == 0:
        paragraphs = []
        expected = subtitle + title + footer
        cover = True
    else:
        paragraphs = body_pages[page_index - 1]
        expected = title + "".join(paragraphs) + footer
        cover = False
    document = _document(
        title=title, subtitle=subtitle, paragraphs=paragraphs, footer=footer,
        page_text=f"{page_index + 1}/{total_pages}", theme=theme, cover=cover,
        width=width, height=height, background_data_uri=background_uri,
        palette_override=palette, font_family=font_family,
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"card_{platform.value}_revise_{uuid.uuid4().hex[:10]}.png"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            page.set_content(document, wait_until="load")
            page.evaluate("document.fonts.ready")
            font_loaded = page.evaluate(
                '(font) => document.fonts.check(`36px "${font}"`, "中文字体检查")',
                font_family,
            )
            if not font_loaded:
                raise RuntimeError(
                    f"指定字体未安装或不可用：{font_family}；请运行 scripts/install_visual_fonts.sh"
                )
            layout = page.evaluate("""() => {
              const root=document.querySelector('.canvas'), rr=root.getBoundingClientRect();
              const nodes=[...root.querySelectorAll('.eyebrow,h1,.rule,h2,.visual-component,.footer,.page')];
              const boxes=nodes.map(el=>{const r=el.getBoundingClientRect();return [r.left-rr.left,r.top-rr.top,r.right-rr.left,r.bottom-rr.top]});
              let overlap=false;
              for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){
                const a=boxes[i],b=boxes[j];if(Math.min(a[2],b[2])-Math.max(a[0],b[0])>3&&Math.min(a[3],b[3])-Math.max(a[1],b[1])>3)overlap=true;
              }
              return {overflow:root.scrollWidth>root.clientWidth+1||root.scrollHeight>root.clientHeight+1,overlap};
            }""")
            if layout["overflow"] or layout["overlap"]:
                raise ValueError("单页重渲染布局硬门失败")
            page.screenshot(path=str(target), full_page=True, type="png")
        finally:
            browser.close()
    validate_rendered_pages([target], [expected], output_dir / f"visual_ocr_{target.stem}.json")
    return target, expected
