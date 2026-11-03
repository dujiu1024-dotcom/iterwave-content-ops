"""AI-native full-card generation: the image API returns each final page.

No HTML/CSS/Pillow text overlay is used. Each output image is the complete card,
including Chinese typography, illustration, layout and decoration. OCR is a hard gate.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from .media_generation import generate_image_bytes


def _paginate_body(body: str, *, max_chars: int = 90, segments_per_page: int = 5) -> list[list[str]]:
    """Split all source text on sentence boundaries without truncating long sentences."""
    text = str(body or "").strip()
    if not text:
        raise ValueError("正文为空，拒绝生成不完整卡片")
    segments: list[str] = []
    for raw in re.split(r"(?<=[。！？!?；;\n])", text):
        part = raw
        while len(part) > max_chars:
            cut = max(part.rfind(mark, 0, max_chars) for mark in "，、, ")
            if cut < max_chars // 2:
                cut = max_chars
            else:
                cut += 1
            segments.append(part[:cut])
            part = part[cut:]
        if part:
            segments.append(part)
    if not segments:
        raise ValueError("正文分页为空，拒绝生成不完整卡片")
    return [
        segments[index:index + segments_per_page]
        for index in range(0, len(segments), segments_per_page)
    ]


def _page_prompt(*, title: str, subtitle: str, page_text: str, page_index: int,
                 page_count: int, cover: bool = False) -> str:
    if cover:
        role = "这是整组图文的封面页"
        content = f"必须准确呈现的封面标题：\n{title}\n副标题：\n{subtitle}"
        layout = "封面要有明确主视觉、醒目的标题排版和克制的辅助信息，形成完整的编辑设计。"
    else:
        role = f"这是第 {page_index} 页正文，共 {page_count} 页正文"
        content = f"必须逐字、完整呈现以下中文正文，不得改写、删减、增补或杜撰：\n{page_text}"
        layout = (
            "将内容设计成真正的信息图页面：按语义选择流程、步骤、对比、分组、"
            "时间线、重点数字或图解构图。避免整页大段连续文字，避免统一的矩形文字框堆叠。"
        )
    return (
        "请直接生成一张完整、可发布的中文社交媒体图文卡片成品图片，而不是背景图、素材图或模板。"
        "图片本身必须包含所有文字、插画、图形、信息层级、色彩和装饰；不得依赖后期程序叠字。"
        "高品质中文编辑设计，专业信息图与品牌杂志视觉，构图有重点、留白充足、字体清晰可读，"
        "插画和图形必须服务于本页具体内容。严格适配请求的输出尺寸与纵横比，高清。"
        f"\n{role}。主题：{title}。栏目：{subtitle}。\n{layout}\n"
        f"{content}\n"
        "文字准确性优先于装饰：中文必须使用简体中文，严格保留给定文字及数字，不得出现乱码、"
        "伪文字、无关英文、水印、虚构品牌标识。不得把正文缩成难以阅读的小字。"
    )


def generate_ai_card_set(
    *, title: str, subtitle: str, body: str, output_dir: Path, platform,
    cfg, settings: dict, allow_fallback: bool | None = None,
    brand_profile: dict | None = None,
) -> tuple[list[Path], list[str]]:
    """Generate cover + all body pages as complete AI images, then require OCR pass."""
    from .visual_ocr import validate_rendered_pages

    body_pages = _paginate_body(body, max_chars=60, segments_per_page=3)
    page_texts = ["".join(page) for page in body_pages]
    expected = [f"{title}\n{subtitle}", *page_texts]
    prompts = [
        _page_prompt(title=title, subtitle=subtitle, page_text="", page_index=0,
                     page_count=len(body_pages), cover=True)
    ]
    prompts.extend(
        _page_prompt(title=title, subtitle=subtitle, page_text=text,
                     page_index=index, page_count=len(body_pages))
        for index, text in enumerate(page_texts, start=1)
    )
    prefix = str(settings.get("prefix") or "").strip()
    brand_profile = brand_profile if isinstance(brand_profile, dict) else {}
    if brand_profile:
        from .visual_catalog import validate_brand_profile
        validate_brand_profile(brand_profile)
        brand_rules = [
            f"品牌名称（仅作为视觉识别，不得擅自添加额外文案）：{brand_profile.get('brand_name')}"
            if brand_profile.get("brand_name") else "",
            f"主色：{brand_profile.get('primary_color')}" if brand_profile.get("primary_color") else "",
            f"辅助色：{brand_profile.get('secondary_color')}" if brand_profile.get("secondary_color") else "",
            f"背景色：{brand_profile.get('background_color')}" if brand_profile.get("background_color") else "",
            f"文字色：{brand_profile.get('text_color')}" if brand_profile.get("text_color") else "",
            f"视觉风格：{brand_profile.get('style_key')}" if brand_profile.get("style_key") else "",
            f"组件风格：{brand_profile.get('component_style')}" if brand_profile.get("component_style") else "",
            f"避免风格：{'、'.join(brand_profile.get('forbidden_styles') or [])}"
            if brand_profile.get("forbidden_styles") else "",
        ]
        brand_prefix = "账号品牌视觉规范（只约束画面风格，不得更改正文）：\n" + "\n".join(
            item for item in brand_rules if item
        )
        prefix = "\n\n".join(item for item in (prefix, brand_prefix) if item)
    if prefix:
        prompts = [f"{prefix}\n\n{prompt}" for prompt in prompts]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, prompt in enumerate(prompts):
        accepted_path = None
        last_error = "OCR 未通过"
        for attempt in range(3):
            retry_prompt = prompt if attempt == 0 else (
                prompt + "\n上一版文字识别未通过。请优先保证所有指定中文逐字准确、清晰、完整，"
                "减少装饰和文字数量，增大字号，重新设计整页成品。"
            )
            raw, _mime, suffix, _width, _height = generate_image_bytes(
                retry_prompt, model=settings["model"], size=settings["size"], cfg=cfg,
                allow_fallback=allow_fallback,
            )
            path = output_dir / f"ai_card_{platform.value}_{uuid.uuid4().hex[:12]}{suffix}"
            path.write_bytes(raw)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            page_report = output_dir / f"visual_ocr_{path.stem}.json"
            try:
                result = validate_rendered_pages([path], [expected[index]], page_report)
            except ValueError as exc:
                last_error = str(exc)
                path.unlink(missing_ok=True)
                continue
            if result.get("status") == "passed" and result.get("gate") == "passed":
                accepted_path = path
                break
            last_error = str(result.get("reason") or result.get("status") or "OCR 未通过")
            path.unlink(missing_ok=True)
        if accepted_path is None:
            raise ValueError(
                f"第 {index + 1} 张 AI 完整卡片连续 3 次未通过 OCR，拒绝模板降级：{last_error}"
            )
        paths.append(accepted_path)

    report_path = output_dir / f"visual_ocr_{paths[0].stem}.json"
    report = validate_rendered_pages(paths, expected, report_path)
    if report.get("status") != "passed" or report.get("gate") != "passed":
        raise ValueError(
            f"AI 完整卡片 OCR 硬门未通过（{report.get('status')}）；"
            "图片已生成但不得作为合格成品交付"
        )
    return paths, page_texts
