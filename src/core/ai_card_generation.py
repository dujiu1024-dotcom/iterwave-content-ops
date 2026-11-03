"""AI-native full-card generation with isolated staging and validated per-page cache."""
from __future__ import annotations

import hashlib
import logging
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from .media_generation import ImageGenerationError, generate_image_bytes, validate_image_bytes, validate_target_image_spec
from .image_generation_budget import article_budget, image_generation_run, page_budget

_PROMPT_VERSION = "ai-complete-card-v2"
_LAYOUT_VERSION = "card-layout-v1"

logger = logging.getLogger(__name__)


def _paginate_body(body: str, *, max_chars: int = 90, segments_per_page: int = 5, max_pages: int = 9) -> list[list[str]]:
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
    pages = [segments[index:index + segments_per_page] for index in range(0, len(segments), segments_per_page)]
    if len(pages) + 1 > max_pages:
        raise ValueError(f"内容分页需要 {len(pages) + 1} 页，超过单篇最大 {max_pages} 页；拒绝截断或先出图后截断")
    return pages


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


def _canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_fingerprint(*, expected: str, prompt: str, settings: dict, brand_profile: dict,
                       platform: str, page_index: int, cfg, allow_fallback: bool | None) -> str:
    from .visual_ocr import _OCR_RULES_VERSION
    media = getattr(cfg, "media_generation", None)
    quality = getattr(cfg, "media_quality", None)
    return _canonical_hash({
        "expected_text": expected,
        "prompt": prompt,
        "prompt_version": _PROMPT_VERSION,
        "model": settings.get("model"),
        "model_version": settings.get("model_version") or settings.get("model"),
        "image_provider": getattr(media, "provider", None),
        "image_base_url": getattr(media, "base_url", None),
        "configured_image_model": getattr(media, "model", None),
        "watermark": getattr(media, "watermark", None),
        "max_dimension": getattr(media, "max_dimension", None),
        "max_pixels": getattr(media, "max_pixels", None),
        "fallback_provider": getattr(media, "fallback_provider", None),
        "fallback_base_url": getattr(media, "fallback_base_url", None),
        "fallback_model": getattr(media, "fallback_model", None),
        "fallback_enabled": getattr(media, "fallback_enabled", None),
        "allow_fallback_override": allow_fallback,
        "media_quality": {
            "portrait_ratio_min": getattr(quality, "portrait_ratio_min", None),
            "portrait_ratio_max": getattr(quality, "portrait_ratio_max", None),
            "square_ratio_max": getattr(quality, "square_ratio_max", None),
            "landscape_ratio_max": getattr(quality, "landscape_ratio_max", None),
        },
        "brand_profile": brand_profile,
        "size": settings.get("size"),
        "platform": platform,
        "layout_version": _LAYOUT_VERSION,
        "layout_parameters": settings.get("layout_parameters") or {"max_chars": 60, "segments_per_page": 3},
        "ocr_rules_version": _OCR_RULES_VERSION,
        "page_index": page_index,
    })


def _safe_remove_tree(path: Path, root: Path) -> None:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_root not in resolved_path.parents:
        raise ValueError("拒绝清理媒体目录之外的路径")
    if resolved_path.exists():
        shutil.rmtree(resolved_path)


def _validate_cached_page(cache_dir: Path, fingerprint: str, expected: str, size: str, cfg, critical: bool = False) -> Path | None:
    manifest_path = cache_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        image_name = str(manifest["image"])
        if Path(image_name).name != image_name:
            return None
        image_path = cache_dir / image_name
        if (
            manifest.get("fingerprint") != fingerprint
            or not image_path.is_file()
            or cache_dir.resolve() not in image_path.resolve().parents
        ):
            return None
        raw = image_path.read_bytes()
        _mime, _suffix, width, height = validate_image_bytes(raw, cfg=cfg)
        validate_target_image_spec(width, height, size, cfg=cfg)
        from .visual_ocr import validate_rendered_pages
        report = validate_rendered_pages(
            [image_path], [expected], cache_dir / "ocr_report.json", critical_pages=[critical],
        )
        if report.get("status") == "unavailable":
            raise RuntimeError(f"OCR 执行过程中不可用（{report.get('reason') or 'unknown'}），立即终止图片生成")
        if report.get("status") == "passed" and report.get("gate") == "passed":
            return image_path
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, ImageGenerationError):
        return None
    return None


def _generate_ai_card_set(
    *, title: str, subtitle: str, body: str, output_dir: Path, platform,
    cfg, settings: dict, allow_fallback: bool | None = None,
    brand_profile: dict | None = None, task_id: str | None = None,
    account_group: str | None = None,
) -> tuple[list[Path], list[str]]:
    """Generate validated AI pages with durable task checkpoints and atomic delivery."""
    from .visual_ocr import preflight_ocr, validate_rendered_pages
    from .visual_task_store import VisualTaskStore

    policy = getattr(cfg, "media_generation", None)
    preflight = preflight_ocr(languages=getattr(policy, "ocr_languages", "chi_sim+eng"), timeout=getattr(policy, "ocr_timeout_seconds", 45))
    if preflight.get("status") != "available":
        raise RuntimeError(f"OCR 前置预检失败（{preflight.get('reason')}），已阻止图片 API 调用")
    body_pages = _paginate_body(body, max_chars=60, segments_per_page=3, max_pages=getattr(policy, "max_pages_per_article", 9))
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
            f"品牌名称（仅作为视觉识别，不得擅自添加额外文案）：{brand_profile.get('brand_name')}" if brand_profile.get("brand_name") else "",
            f"主色：{brand_profile.get('primary_color')}" if brand_profile.get("primary_color") else "",
            f"辅助色：{brand_profile.get('secondary_color')}" if brand_profile.get("secondary_color") else "",
            f"背景色：{brand_profile.get('background_color')}" if brand_profile.get("background_color") else "",
            f"文字色：{brand_profile.get('text_color')}" if brand_profile.get("text_color") else "",
            f"视觉风格：{brand_profile.get('style_key')}" if brand_profile.get("style_key") else "",
            f"组件风格：{brand_profile.get('component_style')}" if brand_profile.get("component_style") else "",
            f"避免风格：{'、'.join(brand_profile.get('forbidden_styles') or [])}" if brand_profile.get("forbidden_styles") else "",
        ]
        brand_prefix = "账号品牌视觉规范（只约束画面风格，不得更改正文）：\n" + "\n".join(
            item for item in brand_rules if item
        )
        prefix = "\n\n".join(item for item in (prefix, brand_prefix) if item)
    if prefix:
        prompts = [f"{prefix}\n\n{prompt}" for prompt in prompts]

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    media_root = output_dir / "media"
    staging_root = media_root / "staging"
    validated_root = media_root / "validated"
    validated_root.mkdir(parents=True, exist_ok=True)
    task_id = str(task_id or uuid.uuid4().hex)
    db_path = getattr(getattr(cfg, "paths", None), "db_file", "data/easel.db")
    if callable(getattr(cfg, "resolve_path", None)):
        db_path = cfg.resolve_path(db_path)
    account_group = str(account_group or Path(db_path).resolve())
    store = VisualTaskStore(db_path)
    owner = store.acquire(task_id, account_group, lease_seconds=900)
    policy = getattr(cfg, "media_generation", None)
    page_timeout = int(getattr(policy, "page_timeout_seconds", 600))
    group_timeout = int(getattr(policy, "group_timeout_seconds", 1800))
    task_timeout = int(getattr(policy, "task_timeout_seconds", 1800))
    task_deadline = time.monotonic() + min(group_timeout, task_timeout)
    staging_dir = staging_root / f"{task_id}-{uuid.uuid4().hex}"
    accepted_cache: list[Path] = []
    published_dir: Path | None = None

    def check_deadline(deadline: float, label: str) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{label}超时，已停止后续图片生成")

    try:
        staging_dir.mkdir(parents=True, exist_ok=False)
        for index, prompt in enumerate(prompts):
            check_deadline(task_deadline, "图片整组/任务")
            page_deadline = min(task_deadline, time.monotonic() + page_timeout)
            fingerprint = _cache_fingerprint(
                expected=expected[index], prompt=prompt, settings=settings,
                brand_profile=brand_profile, platform=platform.value, page_index=index,
                cfg=cfg, allow_fallback=allow_fallback,
            )
            cache_dir = validated_root / fingerprint
            critical = index <= 1 or bool(re.search(r"\d|禁止|不|没|无|非|未", expected[index]))

            # A durable checkpoint is reusable only when the matching fingerprinted
            # cache artifact still exists and passes the current OCR/image policy.
            checkpoint = store.completed_page(task_id, index)
            cached = _validate_cached_page(
                cache_dir, fingerprint, expected[index], settings["size"], cfg, critical=critical,
            )
            check_deadline(page_deadline, "图片缓存校验")
            if cached is not None:
                if checkpoint and Path(checkpoint).resolve() != cached.resolve():
                    logger.info("视觉任务 checkpoint 指向旧缓存，采用当前指纹验证后的缓存 task=%s page=%s", task_id, index)
                if not store.heartbeat(task_id, owner, lease_seconds=900):
                    raise RuntimeError("图片生成任务租约已失效，停止继续图片处理")
                store.start_page(task_id, index)
                store.complete_page(task_id, index, str(cached))
                accepted_cache.append(cached)
                continue

            if cache_dir.exists():
                _safe_remove_tree(cache_dir, validated_root)

            last_error = "OCR 未通过"
            accepted_stage = None
            store.start_page(task_id, index)
            with page_budget():
                for attempt in range(3):
                    check_deadline(page_deadline, "图片单页")
                    if not store.heartbeat(task_id, owner, lease_seconds=900):
                        raise RuntimeError("图片生成任务租约已失效，停止继续调用图片 API")
                    retry_prompt = prompt if attempt == 0 else (
                        prompt + "\n上一版文字识别未通过。请优先保证所有指定中文逐字准确、清晰、完整，"
                        "减少装饰和文字数量，增大字号，重新设计整页成品。"
                    )
                    raw, _mime, suffix, width, height = generate_image_bytes(
                        retry_prompt, model=settings["model"], size=settings["size"], cfg=cfg,
                        allow_fallback=allow_fallback,
                    )
                    check_deadline(page_deadline, "图片单页")
                    validate_target_image_spec(width, height, settings["size"], cfg=cfg)
                    staged_image = staging_dir / f"page_{index:03d}_{attempt}{suffix}"
                    staged_image.write_bytes(raw)
                    try:
                        staged_image.chmod(0o600)
                    except OSError:
                        pass
                    report_path = staging_dir / f"page_{index:03d}_{attempt}.json"
                    try:
                        report = validate_rendered_pages(
                            [staged_image], [expected[index]], report_path,
                            critical_pages=[critical],
                        )
                        check_deadline(page_deadline, "图片单页 OCR")
                    except ValueError as exc:
                        last_error = str(exc)
                        staged_image.unlink(missing_ok=True)
                        report_path.unlink(missing_ok=True)
                        store.fail_page(task_id, index, last_error)
                        continue
                    if report.get("status") == "unavailable":
                        last_error = str(report.get("reason") or "OCR unavailable")
                        staged_image.unlink(missing_ok=True)
                        report_path.unlink(missing_ok=True)
                        store.fail_page(task_id, index, last_error)
                        raise RuntimeError(
                            f"OCR 执行过程中不可用（{last_error}），立即终止，不再进行付费重试"
                        )
                    if report.get("status") == "passed" and report.get("gate") == "passed":
                        accepted_stage = staged_image
                        break
                    last_error = str(report.get("reason") or report.get("status") or "OCR 未通过")
                    staged_image.unlink(missing_ok=True)
                    report_path.unlink(missing_ok=True)
                    store.fail_page(task_id, index, last_error)

            if accepted_stage is None:
                raise ValueError(
                    f"第 {index + 1} 张 AI 完整卡片连续 3 次未通过 OCR，拒绝模板降级：{last_error}"
                )

            cache_temp = staging_dir / f"cache_{fingerprint}"
            cache_temp.mkdir(parents=True, exist_ok=False)
            staged_cache_image = cache_temp / f"image{accepted_stage.suffix}"
            shutil.copy2(accepted_stage, staged_cache_image)
            staged_ocr_report = staging_dir / f"page_{index:03d}_{attempt}.json"
            if staged_ocr_report.is_file():
                shutil.copy2(staged_ocr_report, cache_temp / "ocr_report.json")
            (cache_temp / "manifest.json").write_text(
                json.dumps({
                    "fingerprint": fingerprint, "image": staged_cache_image.name,
                    "ocr_rules_version": "ocr-semantic-v2",
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                cache_temp.replace(cache_dir)
            except OSError:
                _safe_remove_tree(cache_temp, staging_dir)
                concurrent_cache = _validate_cached_page(
                    cache_dir, fingerprint, expected[index], settings["size"], cfg, critical=critical,
                )
                if concurrent_cache is None:
                    raise
                cached = concurrent_cache
            else:
                cached = cache_dir / staged_cache_image.name
            store.complete_page(task_id, index, str(cached))
            accepted_cache.append(cached)

        check_deadline(task_deadline, "图片整组/任务")
        complete_report_path = staging_dir / "complete_ocr_report.json"
        critical_pages = [
            index <= 1 or bool(re.search(r"\d|禁止|不|没|无|非|未", expected[index]))
            for index in range(len(expected))
        ]
        report = validate_rendered_pages(
            accepted_cache, expected, complete_report_path, critical_pages=critical_pages,
        )
        if report.get("status") != "passed" or report.get("gate") != "passed":
            raise ValueError("AI 完整卡片整组 OCR 粗检未通过；拒绝交付")
        if not complete_report_path.is_file():
            complete_report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
            )

        delivery_stage = staging_dir / "delivery"
        delivery_stage.mkdir()
        staged_paths: list[Path] = []
        for source in accepted_cache:
            target = delivery_stage / f"ai_card_{platform.value}_{uuid.uuid4().hex[:12]}{source.suffix}"
            shutil.copy2(source, target)
            staged_paths.append(target)
        final_report = delivery_stage / f"visual_ocr_{staged_paths[0].stem}.json"
        report_pages = report.get("pages") if isinstance(report.get("pages"), list) else []
        mapped_pages = [
            {
                **page,
                "image": staged_paths[index].name if index < len(staged_paths) else page.get("image"),
            }
            for index, page in enumerate(report_pages)
        ]
        delivery_report = {**report, "pages": mapped_pages}
        final_report.write_text(
            json.dumps(delivery_report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        sets_root = media_root / "sets"
        sets_root.mkdir(parents=True, exist_ok=True)
        target_dir = sets_root / f"{task_id}-{uuid.uuid4().hex[:8]}"
        delivery_stage.replace(target_dir)
        published_dir = target_dir
        delivered = [published_dir / path.name for path in staged_paths]
        store.finish(task_id, owner, "completed")
        return delivered, page_texts
    except Exception as exc:
        if published_dir is not None and published_dir.exists():
            _safe_remove_tree(published_dir, media_root / "sets")
        try:
            store.finish(task_id, owner, "failed", str(exc))
        except Exception:
            pass
        raise
    finally:
        _safe_remove_tree(staging_dir, staging_root)


def generate_ai_card_set(
    *, title: str, subtitle: str, body: str, output_dir: Path, platform,
    cfg, settings: dict, allow_fallback: bool | None = None,
    brand_profile: dict | None = None, task_id: str | None = None,
    account_group: str | None = None,
) -> tuple[list[Path], list[str]]:
    """Run durable article generation under the configured shared API budget."""
    policy = getattr(cfg, "media_generation", None)
    with image_generation_run(
        max_calls=int(getattr(policy, "max_api_calls_per_run", 30)),
        timeout_seconds=int(getattr(policy, "hard_timeout_seconds", 300)),
        max_concurrency=int(getattr(policy, "max_concurrency", 2)),
    ):
        with article_budget(
            max_calls=int(getattr(policy, "max_api_calls_per_article", 27)),
            page_max_calls=int(getattr(policy, "max_api_calls_per_page", 3)),
        ):
            return _generate_ai_card_set(
                title=title, subtitle=subtitle, body=body, output_dir=output_dir,
                platform=platform, cfg=cfg, settings=settings,
                allow_fallback=allow_fallback, brand_profile=brand_profile,
                task_id=task_id, account_group=account_group,
            )
