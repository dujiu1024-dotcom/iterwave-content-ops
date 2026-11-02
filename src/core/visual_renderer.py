"""HTML/CSS + Chromium card renderer.

This module owns deterministic typography/layout and the geometry hard gate:

* pagination is measured in the real browser (greedy fill by rendered height), never
  estimated from paragraph counts, and exceeding the page limit raises instead of
  truncating;
* the requested font must really be installed (fontconfig + in-browser probe);
* every network request from the rendering page is blocked (content is self-contained);
* text regions are reported from the DOM so the OCR gate can read each one separately.

Styles, fonts and component styles come from ``visual_catalog`` (single source).
"""
from __future__ import annotations

import base64
import html
import logging
import re
import shutil  # noqa: F401  (kept for test monkeypatching of font detection)
import subprocess  # noqa: F401
import uuid
from pathlib import Path

from ..adapters.base import Platform
from . import visual_catalog
from .visual_specs import NUMBER_TOKEN, build_infographic_components, get_visual_platform_spec

logger = logging.getLogger(__name__)

CARD_SIZE = (1080, 1440)
# Read-only view of the catalog palettes (kept for callers/tests); edit visual_catalog.json.
THEMES = {key: visual_catalog.style_palette(key) for key in visual_catalog.style_keys()}
DEFAULT_STYLE = visual_catalog.default_style()
DEFAULT_FONT = visual_catalog.default_font()
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


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


def _light_text(palette: dict) -> bool:
    return _relative_luminance(palette["fg"]) > 0.5


def _resolve_palette(theme: str, brand_palette: dict | None) -> dict:
    """Catalog palette for the style plus validated tenant overrides (colors + component style)."""
    palette: dict = {**THEMES.get(theme, THEMES[DEFAULT_STYLE])}
    for key, value in (brand_palette or {}).items():
        if key in {"bg", "fg", "accent", "muted"}:
            if not (isinstance(value, str) and visual_catalog.HEX_COLOR.fullmatch(value)):
                raise ValueError(f"品牌配色 {key} 必须为 #RRGGBB")
            palette[key] = value
        elif key == "component_style":
            palette[key] = value
    return palette


def _validate_theme_contrast(theme: str, has_background_image: bool, palette_override: dict | None = None) -> float:
    palette = {**THEMES.get(theme, THEMES[DEFAULT_STYLE]), **(palette_override or {})}
    if has_background_image and _light_text(palette):
        # The 60% minimum dark overlay bounds the brightest possible image pixel.
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


def _rich(text: str, highlight: str = "") -> str:
    """Escape text and wrap every numeric token in a nowrap ``.num`` span (the first token
    equal to ``highlight`` becomes the emphasised ``<strong>``). The OCR gate verifies each
    ``.num`` box on its own, so numbers are checked exactly and independently of prose."""
    out: list[str] = []
    cursor = 0
    highlighted = False
    for match in NUMBER_TOKEN.finditer(text or ""):
        out.append(html.escape(text[cursor:match.start()]))
        token = html.escape(match.group(1))
        if highlight and not highlighted and match.group(1) == highlight:
            out.append(f'<strong class="num">{token}</strong>')
            highlighted = True
        else:
            out.append(f'<span class="num">{token}</span>')
        cursor = match.end()
    out.append(html.escape((text or "")[cursor:]))
    return "".join(out)


def _document(*, title: str, subtitle: str, paragraphs: list[str], footer: str,
              page_text: str, theme: str, cover: bool, width: int, height: int,
              background_data_uri: str = "", palette_override: dict | None = None,
              font_family: str = DEFAULT_FONT) -> str:
    overrides = palette_override or {}
    for color_key in ("bg", "fg", "accent", "muted"):
        if color_key in overrides and not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(overrides[color_key])):
            raise ValueError(f"品牌配色 {color_key} 必须为 #RRGGBB")
    component_style = str(overrides.get("component_style") or visual_catalog.default_component_style())
    if component_style not in visual_catalog.component_style_keys():
        raise ValueError("不支持的组件样式")
    palette = {
        **THEMES.get(theme, THEMES[DEFAULT_STYLE]),
        **{key: value for key, value in overrides.items() if key in {"bg", "fg", "accent", "muted"}},
    }
    if font_family not in visual_catalog.font_families():
        raise ValueError("不支持的字体族")
    light_text = _light_text(palette)
    panel_bg = "rgba(255,255,255,.08)" if light_text else "rgba(255,255,255,.56)"
    panel_border = "rgba(255,255,255,.18)" if light_text else "rgba(23,33,43,.10)"
    text_bg = "rgba(255,255,255,.05)" if light_text else "rgba(255,255,255,.32)"
    component_style_css = {
        "card": "",
        "minimal": ".visual-component{padding:14px 0;border:0;border-radius:0;background:transparent}.visual-stat{border-left:6px solid " + palette["accent"] + ";padding-left:22px}",
        "editorial": ".visual-component{padding:18px 22px;border:0;border-bottom:2px solid " + palette["muted"] + ";border-radius:0;background:transparent}.visual-quote{border-left:6px solid " + palette["accent"] + "}",
    }.get(component_style, "")
    safe_title = _rich(title or "")
    safe_subtitle = html.escape(subtitle or "")
    safe_footer = html.escape(footer or "")
    component_markup = []
    for component in build_infographic_components(paragraphs):
        if component.kind == "step":
            rest = component.text[len(component.label):].lstrip(" ：:、-.．)）")
            component_markup.append(
                f'<div class="visual-component visual-step"><span class="step-label{" num" if component.label.isdigit() else ""}">{html.escape(component.label)}</span>'
                f'<p>{_rich(rest or component.text, component.value)}</p></div>'
            )
        elif component.kind == "stat":
            component_markup.append(
                f'<div class="visual-component visual-stat"><p>{_rich(component.text, component.value)}</p></div>'
            )
        elif component.kind == "quote":
            component_markup.append(
                f'<blockquote class="visual-component visual-quote"><p>{_rich(component.text)}</p></blockquote>'
            )
        else:
            component_markup.append(
                f'<div class="visual-component visual-text"><p>{_rich(component.text)}</p></div>'
            )
    body = "".join(component_markup)
    overlay = (
        "rgba(8,14,24,.60),rgba(8,14,24,.68)"
        if light_text
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
body{{font-family:"{font_family}",sans-serif;
background:{palette['bg']};color:{palette['fg']};{background_css}}}
.canvas{{position:relative;width:{width}px;height:{height}px;padding:112px 92px 120px;
overflow:hidden;display:flex;flex-direction:column;justify-content:center}}
.eyebrow{{display:inline-flex;align-self:flex-start;max-width:100%;padding:15px 24px;
border-radius:999px;background:{palette['accent']};color:{_contrast_text(palette['accent'])};font-size:30px;
font-weight:700;overflow-wrap:anywhere;margin-bottom:58px}}
h1{{font-size:82px;line-height:1.22;letter-spacing:-2px;margin:0;font-weight:800;
overflow-wrap:anywhere;word-break:break-word}}
h2{{font-size:54px;line-height:1.25;margin:0 0 42px;font-weight:800;overflow-wrap:anywhere;word-break:break-word}}
.rule{{width:150px;height:8px;background:{palette['accent']};margin-top:58px;border-radius:8px}}
.body{{display:flex;flex-direction:column;gap:20px;overflow:hidden}}
.visual-component{{padding:22px 26px;border-radius:22px;background:{panel_bg};
border:1px solid {panel_border};overflow-wrap:anywhere;word-break:break-word}}
.visual-component p{{font-size:34px;line-height:1.48;margin:0;overflow-wrap:anywhere;word-break:break-word}}
.visual-stat{{border-left:10px solid {palette['accent']}}}
.visual-stat strong,.visual-step strong{{display:inline-block;color:{palette['accent']};font-size:48px;line-height:1.1;font-weight:900;margin:0 5px}}
.num{{white-space:nowrap}}
.visual-step{{display:flex;align-items:baseline;gap:18px}}
.step-label{{flex:0 0 auto;padding:9px 16px;border-radius:999px;background:{palette['accent']};
color:{_contrast_text(palette['accent'])};font-size:25px;font-weight:800;min-width:52px;text-align:center}}
.visual-quote{{margin:0;border-left:10px solid {palette['accent']};font-style:italic}}
.visual-text{{background:{text_bg}}}
{component_style_css}
.footer{{position:absolute;left:92px;bottom:52px;max-width:760px;color:{palette['muted']};
font-size:22px;line-height:1.35;overflow-wrap:anywhere}}
.page{{position:absolute;right:92px;bottom:52px;color:{palette['muted']};font-size:22px}}
</style></head><body><main class="canvas">{content}
<div class="footer">{safe_footer}</div><div class="page">{html.escape(page_text)}</div>
</main></body></html>"""


def _assert_font_available(font_family: str) -> None:
    """Fail closed when the requested CJK font is absent; never silently render tofu."""
    if font_family not in visual_catalog.font_families():
        raise ValueError("不支持的字体族")
    import shutil as _shutil
    import subprocess as _subprocess

    executable = _shutil.which("fc-match")
    if not executable:
        raise RuntimeError("无法验证视觉字体：未安装 fontconfig（fc-match），禁止静默回退")
    try:
        result = _subprocess.run(
            [executable, "-f", "%{family}", font_family],
            check=True, capture_output=True, text=True, timeout=5,
        )
    except (OSError, _subprocess.SubprocessError) as exc:
        raise RuntimeError("无法验证视觉字体，请安装 fontconfig 和指定字体") from exc
    matched = {name.strip().casefold() for name in result.stdout.split(",") if name.strip()}
    if not matched.intersection(visual_catalog.accepted_font_matches(font_family)):
        raise RuntimeError(
            f"视觉字体不可用：请求 {font_family}，系统实际匹配 {result.stdout.strip() or 'unknown'}；"
            "请先安装对应字体（scripts/install_visual_fonts.sh），禁止静默回退"
        )


def _ocr_expected_texts(title: str, pages: list[list[str]]) -> list[str]:
    """Gate primary content only; subtitle/footer/page labels are decorative metadata."""
    return [title] + ["\n".join([title, *items]) for items in pages]


# Primary-content text regions read separately by the OCR gate.
_OCR_REGION_SELECTOR = "h1,h2,.step-label,.visual-component p"
_LAYOUT_JS = """() => {
  const root = document.querySelector('.canvas');
  const rootRect = root.getBoundingClientRect();
  const selectors = '.eyebrow,h1,.rule,h2,.visual-component,.footer,.page';
  const elements = [...root.querySelectorAll(selectors)].filter(el => {
    const style = getComputedStyle(el); const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  });
  const boxes = elements.map(el => { const r = el.getBoundingClientRect();
    return {name: el.className || el.tagName.toLowerCase(), left:r.left-rootRect.left, top:r.top-rootRect.top,
            right:r.right-rootRect.left, bottom:r.bottom-rootRect.top}; });
  const overlaps = [];
  for (let i=0;i<boxes.length;i++) for (let j=i+1;j<boxes.length;j++) {
    const a=boxes[i], b=boxes[j];
    const w=Math.min(a.right,b.right)-Math.max(a.left,b.left);
    const h=Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top);
    if (w > 3 && h > 3) overlaps.push({a:a.name,b:b.name,area:Math.round(w*h)});
  }
  const outOfBounds = boxes.filter(b => b.left < 0 || b.top < 0 || b.right > rootRect.width+1 || b.bottom > rootRect.height+1);
  const overflowNodes = [...root.querySelectorAll('*')].filter(el => {
    const style = getComputedStyle(el);
    const clipsX = ['hidden','clip','auto','scroll'].includes(style.overflowX);
    const clipsY = ['hidden','clip','auto','scroll'].includes(style.overflowY);
    return (clipsX && el.scrollWidth > el.clientWidth + 2) || (clipsY && el.scrollHeight > el.clientHeight + 2);
  }).map(el => el.className || el.tagName.toLowerCase());
  const regions = [...root.querySelectorAll('""" + _OCR_REGION_SELECTOR + """')]
    .filter(el => el.innerText.trim()).map(el => {
      const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
      let bg = 'rgba(0,0,0,0)'; let node = el;
      while (node && node.nodeType === 1) {
        const c = getComputedStyle(node).backgroundColor;
        if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') { bg = c; break; }
        node = node.parentElement;
      }
      const pl = parseFloat(cs.paddingLeft)||0, pr = parseFloat(cs.paddingRight)||0;
      const pt = parseFloat(cs.paddingTop)||0, pb = parseFloat(cs.paddingBottom)||0;
      return {text: el.innerText, x: r.left + pl, y: r.top + pt, w: r.width - pl - pr, h: r.height - pt - pb,
              bx: r.left, by: r.top, bw: r.width, bh: r.height,
              font_size: parseFloat(cs.fontSize),
              line_height: parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.3,
              color: cs.color, background: bg};
    });
  const numbers = [...root.querySelectorAll('.num')].map(el => {
      const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
      const pl = parseFloat(cs.paddingLeft)||0, pr = parseFloat(cs.paddingRight)||0;
      const pt = parseFloat(cs.paddingTop)||0, pb = parseFloat(cs.paddingBottom)||0;
      return {text: el.innerText, x: r.left + pl, y: r.top + pt, w: r.width - pl - pr,
              h: r.height - pt - pb, font_size: parseFloat(cs.fontSize), color: cs.color};
    });
  return {overlaps, outOfBounds, overflowNodes, regions, numbers,
          rootOverflow: root.scrollWidth > root.clientWidth + 1 || root.scrollHeight > root.clientHeight + 1};
}"""


def _data_uri(path: Path | str | None) -> str:
    if not path:
        return ""
    path = Path(path)
    mime = _MIME.get(path.suffix.lower())
    if not mime:
        raise ValueError("背景素材格式不支持")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class _IsolatedPage:
    """Fresh non-persistent Chromium context with every network request blocked."""

    def __init__(self, playwright, width: int, height: int):
        self.blocked: list[str] = []
        self.browser = playwright.chromium.launch(headless=True)
        context = self.browser.new_context(
            viewport={"width": width, "height": height}, device_scale_factor=1,
            accept_downloads=False, service_workers="block",
        )

        def _route(route):
            url = route.request.url
            if url.startswith(("data:", "about:")):
                route.continue_()
            else:
                self.blocked.append(url[:200])
                route.abort()

        context.route("**/*", _route)
        self.page = context.new_page()

    def load(self, document: str) -> dict:
        self.page.set_content(document, wait_until="load")
        self.page.evaluate("document.fonts.ready")
        layout = self.page.evaluate(_LAYOUT_JS)
        if self.blocked:
            raise ValueError(f"渲染页面尝试访问外部网络，已拦截并阻止输出：{self.blocked[:3]}")
        return layout

    def close(self) -> None:
        self.browser.close()


def _layout_failed(layout: dict) -> bool:
    return bool(layout["rootOverflow"] or layout["overflowNodes"] or layout["outOfBounds"] or layout["overlaps"])


def _assert_font_in_browser(page, font_family: str) -> None:
    """In-browser check that the font really renders (metrics differ from a generic fallback)."""
    page.set_content("<html><body></body></html>")
    ok = page.evaluate("f => document.fonts.check(`48px \"${f}\"`, '中文')", font_family)
    probe = page.evaluate("""f => { const c=document.createElement('canvas').getContext('2d');
        c.font = `48px "${f}", monospace`; const a=c.measureText('中文测试Ag').width;
        c.font = '48px monospace'; const b=c.measureText('中文测试Ag').width; return a !== b; }""", font_family)
    if not (ok and probe):
        raise RuntimeError(
            f"指定字体未安装或不可用：{font_family}；请运行 scripts/install_visual_fonts.sh，禁止静默回退"
        )


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _measure_pages(iso: _IsolatedPage, *, title: str, subtitle: str, body: str, footer: str,
                   theme: str, width: int, height: int, palette: dict, font_family: str,
                   max_pages: int) -> list[list[str]]:
    """Greedy pagination by rendered height in the real browser; raises past the page limit."""
    if max_pages < 2:
        raise ValueError("max_pages must allow a cover and at least one body page")
    blocks = [b for b in _split_paragraphs(body) if b]
    if not blocks:
        return [[""]]

    def fits(items: list[str]) -> bool:
        doc = _document(title=title, subtitle=subtitle, paragraphs=items, footer=footer,
                        page_text=f"{max_pages}/{max_pages}", theme=theme, cover=False,
                        width=width, height=height, palette_override=palette,
                        font_family=font_family)
        return not _layout_failed(iso.load(doc))

    def atoms(block: str) -> list[str]:
        """Split a block until every piece fits on an otherwise empty page."""
        if fits([block]):
            return [block]
        if len(block) < 24:
            raise ValueError("单段文字超出单页可排版区域，已阻止输出")
        pieces = _split_paragraphs(block, limit=max(12, len(block) // 2))
        if len(pieces) <= 1:
            raise ValueError("单段文字超出单页可排版区域，已阻止输出")
        return [atom for piece in pieces for atom in atoms(piece)]

    pages: list[list[str]] = []
    current: list[str] = []

    def new_page() -> None:
        nonlocal current
        pages.append(current)
        current = []
        if len(pages) + 2 > max_pages:  # cover + full pages + the page being started
            raise ValueError(
                f"正文需要超过 {max_pages} 页，超过配置上限 {max_pages} 页；"
                "请先由视觉策划提炼内容，不允许静默截断"
            )

    for block in blocks:
        if fits(current + [block]):
            current.append(block)
            continue
        if current and fits([block]):
            new_page()
            current.append(block)
            continue
        for atom in atoms(block):
            if current and not fits(current + [atom]):
                new_page()
            current.append(atom)
    if current:
        pages.append(current)
    # Deterministic completeness check: pagination must carry every source character.
    if _compact("".join(item for page in pages for item in page)) != _compact("".join(blocks)):
        raise ValueError("分页结果与正文不一致，已阻止输出")
    if len(pages) + 1 > max_pages:
        raise ValueError(
            f"正文需要 {len(pages) + 1} 页，超过配置上限 {max_pages} 页；"
            "请先由视觉策划提炼内容，不允许静默截断"
        )
    return pages


def plan_card_pages(title: str, subtitle: str, body: str, platform: Platform = Platform.XHS,
                    footer: str = "", max_pages: int = 9, theme: str = DEFAULT_STYLE,
                    brand_palette: dict | None = None, font_family: str = DEFAULT_FONT) -> list[list[str]]:
    """Return the measured body pagination (used to plan one AI background per page)."""
    from playwright.sync_api import sync_playwright

    _assert_font_available(font_family)
    spec = get_visual_platform_spec(platform)
    palette = _resolve_palette(theme, brand_palette)
    with sync_playwright() as playwright:
        iso = _IsolatedPage(playwright, spec.width, spec.height)
        try:
            _assert_font_in_browser(iso.page, font_family)
            return _measure_pages(iso, title=title, subtitle=subtitle, body=body, footer=footer,
                                  theme=theme, width=spec.width, height=spec.height, palette=palette,
                                  font_family=font_family, max_pages=min(max_pages, spec.max_pages))
        finally:
            iso.close()


def render_card_set(title: str, subtitle: str, body: str, output_dir: Path,
                    platform: Platform = Platform.XHS, footer: str = "",
                    max_pages: int = 9, theme: str = DEFAULT_STYLE,
                    background_path: Path | None = None, brand_palette: dict | None = None,
                    font_family: str = DEFAULT_FONT,
                    page_backgrounds: "list[Path | None] | None" = None,
                    pages: "list[list[str]] | None" = None) -> list[Path]:
    """Render a cover and body pages with Chromium; raises on missing browser/font/overflow.

    ``background_path`` is the cover background; ``page_backgrounds[i]`` is body page i's
    background (None = plain style background). ``pages`` may carry a pagination from
    :func:`plan_card_pages`; it is re-validated by the geometry gate and completeness check.
    """
    from playwright.sync_api import sync_playwright
    from .visual_ocr import validate_rendered_pages

    _assert_font_available(font_family)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = get_visual_platform_spec(platform)
    width, height = spec.width, spec.height
    max_pages = min(max_pages, spec.max_pages)
    palette = _resolve_palette(theme, brand_palette)
    cover_uri = _data_uri(background_path)
    paths: list[Path] = []
    page_regions: list[list[dict]] = []
    page_numbers: list[list[dict]] = []
    with sync_playwright() as playwright:
        iso = _IsolatedPage(playwright, width, height)
        try:
            _assert_font_in_browser(iso.page, font_family)
            if pages is None:
                pages = _measure_pages(iso, title=title, subtitle=subtitle, body=body, footer=footer,
                                       theme=theme, width=width, height=height, palette=palette,
                                       font_family=font_family, max_pages=max_pages)
            else:
                pages = [list(items) for items in pages] or [[""]]
                if len(pages) + 1 > max_pages:
                    raise ValueError(f"正文需要 {len(pages) + 1} 页，超过配置上限 {max_pages} 页；不允许静默截断")
                if _compact("".join(i for p in pages for i in p)) != _compact("".join(_split_paragraphs(body))):
                    raise ValueError("传入分页与正文不一致，已阻止输出")
            total = len(pages) + 1
            body_uris = []
            for index in range(len(pages)):
                override = page_backgrounds[index] if page_backgrounds and index < len(page_backgrounds) else None
                body_uris.append(_data_uri(override))
            ratio = _validate_theme_contrast(theme, bool(cover_uri) or any(body_uris), palette)
            logger.info("Visual contrast hard-gate passed theme=%s ratio=%.2f", theme, ratio)
            specs = [(True, [], cover_uri)] + [(False, items, body_uris[i]) for i, items in enumerate(pages)]
            for index, (cover, items, uri) in enumerate(specs, start=1):
                document = _document(title=title, subtitle=subtitle, paragraphs=items, footer=footer,
                                     page_text=f"{index}/{total}", theme=theme, cover=cover,
                                     width=width, height=height, background_data_uri=uri,
                                     palette_override=palette, font_family=font_family)
                layout = iso.load(document)
                if _layout_failed(layout):
                    logger.error("Visual layout hard-gate failed: %s",
                                 {k: v for k, v in layout.items() if k != "regions"})
                    raise ValueError("视觉页面布局硬门失败：存在溢出、越界或元素重叠，已阻止输出")
                path = output_dir / f"card_{platform.value}_{uuid.uuid4().hex[:8]}.png"
                iso.page.screenshot(path=str(path), full_page=False, type="png")
                paths.append(path)
                page_regions.append(layout["regions"])
                page_numbers.append(layout["numbers"])
        finally:
            iso.close()
    ocr_report_path = output_dir / f"visual_ocr_{paths[0].stem}.json"
    ocr_report = validate_rendered_pages(
        paths, _ocr_expected_texts(title, pages), ocr_report_path, regions=page_regions,
        numbers=page_numbers,
    )
    logger.info("Chromium rendered %d cards for %s; OCR status=%s",
                len(paths), platform.value, ocr_report["status"])
    return paths


def render_single_page(
    *, title: str, subtitle: str, body: str, page_index: int, total_pages: int,
    output_dir: Path, platform: Platform, footer: str, theme: str,
    background_path: Path | None, brand_palette: dict | None = None,
    font_family: str = DEFAULT_FONT,
) -> tuple[Path, str]:
    """Render exactly one existing page; return image path and its expected OCR text."""
    from playwright.sync_api import sync_playwright
    from .visual_ocr import validate_rendered_pages

    if page_index < 0 or total_pages < 2 or page_index >= total_pages:
        raise ValueError("页面索引无效")
    _assert_font_available(font_family)
    spec = get_visual_platform_spec(platform)
    width, height = spec.width, spec.height
    background_uri = _data_uri(background_path)
    palette = _resolve_palette(theme, brand_palette)
    _validate_theme_contrast(theme, bool(background_uri), palette)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"card_{platform.value}_revise_{uuid.uuid4().hex[:10]}.png"
    with sync_playwright() as playwright:
        iso = _IsolatedPage(playwright, width, height)
        try:
            _assert_font_in_browser(iso.page, font_family)
            body_pages = _measure_pages(iso, title=title, subtitle=subtitle, body=body, footer=footer,
                                        theme=theme, width=width, height=height, palette=palette,
                                        font_family=font_family, max_pages=max(spec.max_pages, total_pages))
            if len(body_pages) + 1 != total_pages:
                raise ValueError("当前正文分页与历史版本不一致，拒绝错误覆盖")
            if page_index == 0:
                paragraphs, cover = [], True
            else:
                paragraphs, cover = body_pages[page_index - 1], False
            expected = _ocr_expected_texts(title, [paragraphs])[0 if cover else 1]
            document = _document(
                title=title, subtitle=subtitle, paragraphs=paragraphs, footer=footer,
                page_text=f"{page_index + 1}/{total_pages}", theme=theme, cover=cover,
                width=width, height=height, background_data_uri=background_uri,
                palette_override=palette, font_family=font_family,
            )
            layout = iso.load(document)
            if _layout_failed(layout):
                raise ValueError("单页重渲染布局硬门失败")
            iso.page.screenshot(path=str(target), full_page=False, type="png")
        finally:
            iso.close()
    validate_rendered_pages([target], [expected], output_dir / f"visual_ocr_{target.stem}.json",
                            regions=[layout["regions"]], numbers=[layout["numbers"]])
    return target, expected
