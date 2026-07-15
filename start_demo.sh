#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"
VENV_DIR="${VLA_MAIN_VENV:-/home/carlos-ssd/venvs/vla-main}"
PYTHON="${VENV_DIR}/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "错误：找不到 vla-main Python：${PYTHON}" >&2
  echo "可通过 VLA_MAIN_VENV 指定其他虚拟环境目录。" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "错误：未找到 npm，请先安装 Node.js 和 npm。" >&2
  exit 1
fi

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  if [[ ! -f "${ROOT_DIR}/.env.example" ]]; then
    echo "错误：缺少 .env 和 .env.example。" >&2
    exit 1
  fi
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  echo "已根据 .env.example 创建 .env，请按部署环境检查其中配置。"
fi

cd "${ROOT_DIR}"

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "首次启动：正在安装前端依赖……"
  npm ci --prefix "${FRONTEND_DIR}"
fi

echo "正在构建 Vue 前端……"
npm run build --prefix "${FRONTEND_DIR}"

echo "正在启动自动标注 Demo……"
SERVICE_PORT="$("${PYTHON}" -c 'from backend.settings import settings; print(settings.port)')"
LOG_PATH="$("${PYTHON}" -c 'from backend.settings import settings; print(settings.log_path)')"
echo "本机地址：http://127.0.0.1:${SERVICE_PORT}"
echo "局域网地址：http://<服务主机IP>:${SERVICE_PORT}"
echo "运行日志：${LOG_PATH}"
echo "按 Ctrl+C 停止服务。"

exec "${PYTHON}" -m backend.main
