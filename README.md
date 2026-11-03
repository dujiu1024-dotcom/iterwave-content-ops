# IterWave — AI 图文内容运营工作台

[简体中文](README.md) · [English](README.en.md) · [Русский](README.ru.md) · [日本語](README.ja.md) · [Français](README.fr.md) · [Deutsch](README.de.md)

> **Early Preview** — 持续开发中的开源项目。功能状态以代码、测试和实际部署验证为准。

IterWave 是面向多平台图文创作者的内容运营工作台，将选题发现、内容规划、平台化图文生产、质量检查、人工发布确认和数据复盘组织为可追溯的工作流。

## 核心原则

- **图文优先**：聚焦选题、文案、图片卡片、质量检查和运营复盘。
- **计划驱动**：总体计划、平台分项计划、阶段依赖和执行状态共同驱动生产，而不是只提供计划管理页面。
- **人工控制发布**：自动化流程可整理并保存草稿，但不会自动点击平台的“发布”按钮。
- **可追溯**：记录计划与内容版本、任务状态、执行结果和复盘依据。
- **本地优先**：账号凭据、配置、数据库和内容资产由部署者控制。

## 工作流程

```text
选题库 / 账号定位 / 热点发现
            ↓
      总体计划与子计划
            ↓
      阶段编排与依赖执行
            ↓
   平台适配文案与图片卡片
            ↓
       质量检查与存草稿
            ↓
       用户手动确认发布
            ↓
    数据采集 → 复盘 → 策略迭代
```

## 支持平台

小红书、抖音、快手、视频号（图文场景）。平台能力受各平台接口、账号权限及页面变化影响；真实账号链路需在目标环境验证。

## 技术栈

| 层次 | 技术 |
|---|---|
| 后端 | Python 3.11+、FastAPI、Pydantic v2 |
| 数据存储 | SQLite、版本化 schema migrations |
| 调度与执行 | APScheduler、全局任务队列、隔离 Worker |
| 浏览器自动化 | Playwright（存草稿与只读采集） |
| 前端 | React 18、TypeScript、Vite |
| 图像处理 | Pillow、OpenCV、NumPy |

## 快速开始

### 后端

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn src.api.main:app --reload
```

按 `config/config.yaml` 准备配置，并将密钥放在本地环境配置中；不要提交 API Key、Cookie 或 Token。

### 前端

```bash
cd web
npm install
npm run dev
npm test
npm run build
```

## 部署与安全注意事项

- 首次部署前配置强随机的 `ITERWAVE_BOOTSTRAP_TOKEN`，通过初始化流程设置正式访问口令；初始化完成后移除引导口令。
- 公网部署必须使用 HTTPS，不要启用 `ITERWAVE_ALLOW_LOCAL_BOOTSTRAP=1`。
- 配置、数据库和登录态目录应限制为服务操作系统用户可访问；敏感文件权限应保持最小化。
- 当前 APScheduler 生命周期尚不支持跨 Uvicorn 进程选主，部署使用单个 Uvicorn worker，不要配置多个 `--workers`。
- 全局任务默认单 Worker、FIFO 串行；只有明确评估硬超时的进程级影响后，才启用跨账号组并行。
- 被硬超时或进程退出中断的任务不会自动重放。应先核实平台侧结果，再由操作者决定恢复方式。
- 自动评论、回复和关注默认关闭；启用前必须确认平台规则与账号授权。
- Playwright 自动化的发布边界是“保存草稿”，发布动作由用户手动确认。

## 项目结构

```text
src/
  core/       状态机、计划版本、任务队列、调度、恢复、分析等核心逻辑
  adapters/   平台适配器
  api/        FastAPI 路由与应用生命周期
  vendor/     按上游许可证保留的第三方代码
web/          React 前端
migrations/   数据库 schema 迁移
tests/        后端测试
config/       配置模板（不存放真实密钥）
```

## 开发与验证状态

项目仍处于持续开发阶段。仓库测试通过不等同于真实平台端到端验收。以下事项需要在具备相应账号、网络和模型凭据的目标环境验证：

- 四个平台的真实登录、存草稿与数据读取链路；
- 模型调用、文案生产、图片卡片制作及质量检查的完整链路；
- 发布后数据采集、复盘和下一轮策略驱动的完整闭环。

提交代码时请补充对应测试，并确保不将凭据、用户数据或真实账号状态写入日志和仓库。

## 第三方代码与许可证

部分平台适配代码基于 [Easel](https://github.com/kaichen/Easel)（Apache License 2.0）复制并改写。相关版权与许可说明见 `src/vendor/easel/LICENSE` 和 `src/vendor/easel/NOTICE`。

本项目采用 Apache License 2.0，详见 [LICENSE](LICENSE)。
