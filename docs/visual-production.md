# 视觉生产运行规范

## 平台输出规格

`src/core/visual_specs.py` 是唯一平台画布规格入口。当前四个平台统一使用 1080×1440 PNG（3:4）作为保守通用基线，最多 9 页；平台实测或官方规格确认后，只更新该配置和对应测试，不在渲染器内散落尺寸常量。该基线不是对平台上传限制的保证。

## 字体与授权

- 生产渲染默认使用 Noto Sans CJK SC。
- CI 安装 Debian/Ubuntu `fonts-noto-cjk` 包；生产 Linux 主机可运行 `bash scripts/install_visual_fonts.sh`。
- Noto CJK 字体按 SIL Open Font License 1.1 发布；商业使用需保留字体包自带的 OFL 与版权声明。不得把字体文件从不明来源复制进仓库。
- 可选字体仅允许 `Noto Sans CJK SC`、`Source Han Sans SC`、`Microsoft YaHei`、`Arial`。其中后两者依赖部署操作系统合法提供，不随项目分发。
- 当前仓库不重新分发字体二进制；容器镜像或主机必须安装 Noto CJK 字体包。缺少字体时 Chromium 可能回退到系统字体，部署验收应检查 `fc-match "Noto Sans CJK SC"`。

## 单页重做

- 仅支持保留了原始 AI 背景素材的视觉版本。
- 必须启用 `ITERWAVE_ENABLE_AI_VISUALS=1`，并正确配置图像模型 API Key。
- 单页重做只替换指定页，其他页沿用原文件；创建新视觉版本并保留历史版本。
- 新版本状态为 `review_required`，仍需人工审核，不会自动发布。
