#!/usr/bin/env bash
# 本地预览纯静态站点 (运行期零后端)。部署时直接把 site/ 目录丢到任意静态托管/CDN 即可。
cd "$(dirname "$0")/site"
PORT="${WX_PORT:-8080}"
echo "纯静态天气站点 -> http://127.0.0.1:$PORT"
python3 -m http.server "$PORT" --bind 0.0.0.0