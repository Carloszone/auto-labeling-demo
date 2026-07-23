# MCAP 自动质检与标注 Demo

本项目将一组连续的机器人 MCAP 数据解析为多摄像头视频和对齐时序，执行数据/图像质检、Trigger 检测、Event 生成，并调用 VLM 生成可人工复核的事件标注。项目包含 Vue 3 前端、FastAPI 后端和可独立调用的 Python 核心流水线。

当前 Demo 面向单机协作开发：同一时间处理一个当前任务，任务状态保存在进程内，产物保存在本地工作目录；服务重启不会恢复任务，请及时导出需要保留的结果。

## 1. 环境要求

- Ubuntu 22.04 或兼容 Linux
- Python 3.11+
- Node.js 18+ 与 npm
- FFmpeg/PyAV 可用的 `libx264` 编码器
- 可访问的 VLM HTTP 服务（执行事件标注时必需）
- 建议至少 32 GiB RAM，并避免同时运行多个大模型或大任务

克隆仓库后，在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
npm ci --prefix frontend
```

团队如果使用已有虚拟环境，也可以直接激活该环境。不要把开发机的绝对虚拟环境路径提交到仓库。

## 2. 配置

默认参数的唯一来源是 [`config/defaults.toml`](config/defaults.toml)。该文件包含服务、MCAP 拼接、Parser、DataCheck、EventGeneration 和 EventLabeling 的所有可选默认值及行内说明。

部署差异通过环境变量覆盖。首次运行：

```bash
cp .env.example .env
```

至少设置 VLM 地址：

```dotenv
AUTO_LABEL_VLM_ENDPOINT=http://127.0.0.1:1234/api/v1/chat
```

`.env` 不提交 Git。系统环境变量优先于 `.env`；可通过 `AUTO_LABEL_ENV_FILE` 指向其他环境文件。

以下值没有默认值，必须在运行时从外部提供：

- Web：一个或多个 MCAP 文件、robot config JSON。
- CLI：`--mcap-path`、`--robot-config-path`。
- 需要 VLM 标注时：`AUTO_LABEL_VLM_ENDPOINT` 或 CLI `--vlm-endpoint`。
- RobotConfig 内：主时间 topic、camera/state/action topic、字段路径和机器人结构。

## 3. 启动完整 Web 服务

推荐使用一键脚本。它会创建 `.env`（若不存在）、安装缺失的前端依赖、构建 Vue 页面并启动 FastAPI：

```bash
VLA_MAIN_VENV="$PWD/.venv" ./start_demo.sh
```

如果已经使用项目约定的其他虚拟环境，把 `VLA_MAIN_VENV` 指向其目录。启动后访问：

- 页面：<http://127.0.0.1:8000>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- OpenAPI：<http://127.0.0.1:8000/docs>

手动启动：

```bash
npm run build --prefix frontend
python -m backend.main
```

按 `Ctrl+C` 停止服务。

## 4. 前后端开发模式

终端一启动后端：

```bash
source .venv/bin/activate
python -m backend.main
```

终端二启动 Vite：

```bash
npm run dev --prefix frontend
```

访问 <http://127.0.0.1:5173>。Vite 将 `/api` 代理到 `127.0.0.1:8000`。

## 5. 运行核心 CLI

不启动前端时，可以直接执行串行流水线：

```bash
python -m app.main \
  --mcap-path /data/example.mcap \
  --robot-config-path /data/robot.json \
  --vlm-endpoint http://127.0.0.1:1234/api/v1/chat \
  --output-path /data/example.annotations.json
```

使用 JSON 覆盖部分算法参数：

```bash
python -m app.main \
  --mcap-path /data/example.mcap \
  --robot-config-path /data/robot.json \
  --pipeline-config-path /data/pipeline-overrides.json
```

覆盖文件只需提供需要变更的字段，未提供字段从 `config/defaults.toml` 递归补齐。

## 6. 验证改动

```bash
source .venv/bin/activate
pytest tests/unit -q
npm run build --prefix frontend
```

提交前还应执行：

```bash
git diff --check
```

不要把大型 MCAP、`.env`、工作目录、日志或前端依赖提交到 Git。

## 7. 日志与故障定位

默认日志：

```bash
tail -f logs/auto-labeling-demo.log
```

默认任务目录为 `/tmp/auto-labeling-demo`。如遇任务失败，优先记录：

- `job_id`、失败 stage 和发生时间；
- MCAP 数量、合计大小和主时间 topic；
- 日志中的 `module_started`、`module_completed`、`pipeline_failed`；
- `journalctl -k` 中是否存在 OOM。

大型任务可能同时消耗大量内存和磁盘。生产化之前不要并发运行多个任务。

## 8. 项目结构

```text
app/                 核心算法、配置加载和 CLI
backend/             FastAPI、任务状态和产物接口
frontend/            Vue 3 页面
config/defaults.toml 唯一默认参数源
doc/                 技术与模块文档
tests/               单元和集成测试
```

总体架构、配置优先级、中间产物和开发约定见 [`doc/TechnicalGuide.md`](doc/TechnicalGuide.md)。模块细节见 `doc/ParserModuleDoc.md`、`doc/DataCheckModuleDoc.md`、`doc/EventGenerationModuleDoc.md` 和 `doc/EventLabelingModuleDoc.md`。
