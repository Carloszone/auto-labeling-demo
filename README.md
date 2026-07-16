# MCAP 自动质检与标注 Demo

本项目提供 Vue 3 前端和 FastAPI 后端，用于上传一组属于同一连续任务的 MCAP 与 robot config、运行现有自动标注算法、同步播放多摄像头视频、复核 Event/异常并导出 JSON。Demo 使用一个当前工作项和最近 5 个成功工作项的进程内历史记录，文件保存在本地临时目录，不依赖数据库、S3 或任务队列。

## 环境准备

Python 开发与测试统一使用 `vla-main`：

```bash
source /home/carlos-ssd/venvs/vla-main/bin/activate
pip install -r requirements.txt
cd frontend
npm install
```

## 演示模式（推荐）

一键启动：

```bash
./start_demo.sh
```

脚本会使用 `/home/carlos-ssd/venvs/vla-main`、在缺少 `.env` 时从 `.env.example` 创建配置、安装缺失的前端依赖、构建 Vue 页面并启动 FastAPI。可通过 `VLA_MAIN_VENV=/path/to/venv ./start_demo.sh` 覆盖虚拟环境路径。按 `Ctrl+C` 停止服务。

也可以手动执行以下步骤。

先构建前端：

```bash
cd frontend
npm run build
cd ..
```

从模板创建本机配置（仓库已经忽略 `.env`）：

```bash
cp .env.example .env
```

确认 `.env` 中的 `AUTO_LABEL_VLM_ENDPOINT` 后，从项目根目录启动后端：

```bash
python -m backend.main
```

本机访问 `http://127.0.0.1:8000`；局域网其他主机访问 `http://<服务主机IP>:8000`。后端默认监听 `0.0.0.0:8000`，前端构建产物由 FastAPI 同源托管。

临时文件默认写入 `/tmp/auto-labeling-demo`。创建新工作项或服务重启时会清理旧工作项；请先下载需要保留的标注结果。

## 前端开发模式

终端一：

```bash
source /home/carlos-ssd/venvs/vla-main/bin/activate
python -m backend.main
```

终端二：

```bash
cd frontend
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 会把 `/api` 代理到 `127.0.0.1:8000`。

## 验证

```bash
source /home/carlos-ssd/venvs/vla-main/bin/activate
pytest tests/unit -q
cd frontend
npm run build
```

后端健康检查为 `GET /api/v1/health`，OpenAPI 页面为 `http://127.0.0.1:8000/docs`。

运行日志默认写入 `logs/auto-labeling-demo.log`，并同时输出到启动终端。查看实时日志：

```bash
tail -f logs/auto-labeling-demo.log
```

## 主要配置

- `AUTO_LABEL_HOST`：默认 `0.0.0.0`。
- `AUTO_LABEL_PORT`：默认 `8000`。
- `AUTO_LABEL_WORKSPACE_ROOT`：默认 `/tmp/auto-labeling-demo`。
- `AUTO_LABEL_VLM_ENDPOINT`：VLM HTTP 地址；生成 Event 后调用 VLM 时必需。
- `AUTO_LABEL_VLM_TIMEOUT_SEC`：单个 VLM 请求超时，默认 300 秒。

后端启动时自动读取项目根目录 `.env`。已经由终端、systemd 或容器注入的同名环境变量优先，不会被 `.env` 覆盖。可用 `AUTO_LABEL_ENV_FILE` 指定其他配置文件。数据库密码、S3 密钥等未来配置只写入部署主机的 `.env` 或密钥管理服务，不应提交到 Git；`.env.example` 只保存字段名和非敏感示例。

详细页面和接口规范见 [BackendDoc.md](doc/BackendDoc.md)、[FrontendDoc.md](doc/FrontendDoc.md) 与 [PageDescList.md](frontend/docs/PageDescList.md)。
