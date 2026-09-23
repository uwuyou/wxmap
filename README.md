# 🌦 EC / GFS 天气可视化 —— NetCDF 纯前端解析

**跑在浏览器里的天气地图**：所有栅格数据以 NetCDF 文件提供，前端用 netcdfjs **直接解析渲染**，**运行期零后端、零 Python**，适合移动设备上任何静态托管 / CDN 直接部署。

## 架构（两段式）

```
[构建期]  tools/build_static_data.py          [运行期]  site/  (纯静态)
 GFS/EC 数据(缓存 NPZ)  ──裁剪+编码──►  site/data/*.nc       │  前端 netcdfjs 直接解析,
                                        site/data/manifest.json│  触发零后端请求
```

- **构建期(Python)**：仅开发时运行一次，把已下载的 GFS/EC 网格裁剪为中国周边小区域，编码成 netCDF3，生成多个时效的 `.nc` 文件 + 索引 `manifest.json`。
- **运行期(零后端)**：`site/` 是纯静态目录，浏览器加载 `manifest.json` 按需 `fetch` 对应 `.nc`，用内嵌 netcdfjs 解析渲染。可部署到 GitHub Pages、S3、任意 CDN。

## 功能

- 多要素：温度 / 相对湿度 / 云量 / 降水 / 雾
- 多气压层：地面 / 925 / 850 / 700 / 500 / 300 hPa（一个文件内全部含）
- 多时效：GFS 0/6/12h…双时次对比，切换零请求
- 风矢量箭头、叠加不透明度、腾讯矢量地图瓦片
- 移动端优先 UI

## 目录结构

```
wxmap/
├─ site/                        # 🔥 运行期纯静态站点(部署此目录即可)
│   ├─ index.html               #   页面(NC 前端解析入口)
│   ├─ netcdfjs.js              #   浏览器 NetCDF 解析库(免外网)
│   ├─ leaflet.*                #   Leaflet 本地资源
│   └─ data/                    #   预生成的 *.nc + manifest.json
├─ tools/
│   └─ build_static_data.py     # 🔧 构建期: 生成 site/data 下的 nc/manifest
├─ cache/                       # GFS/EC 原始数据缓存(构建期读取)
├─ backend/server.py            # (可选) 实时服务: 下载 GRIB 并生 NPZ 缓存
└─ README.md
```

## 快速使用

```bash
# 1) (可选) 若要拉最新数据: 先跑后端生成 NPZ 缓存
cd backend && python3 server.py     # 访问界面下载数据, 再次访问生成缓存后 Ctrl-C

# 2) 构建静态数据(读 cache 生成 site/data)
cd tools && python3 build_static_data.py

# 3) 本地预览 (任何静态 http 服务均可)
cd ../site && python3 -m http.server 8080
# 浏览器打开 http://127.0.0.1:8080/
```

> NetCDF 解析需经由 http(s) 提供(manifest 用 fetch)，直接双击 `index.html`(file://) 会失败，请用上方的静态服务器或部署到托管。

## 部署到静态托管 / CDN（移动端）

把 `site/` 整个目录上传到任意静态托管（GitHub Pages、Netlify、Cloudflare Pages、OSS/S3 等）即可，无需任何后端。移动浏览器直接打开链接使用。

## 数据更新

- 重新运行 `backend/server.py` 拉取最新 GFS/EC → `tools/build_static_data.py` 重新生成 `site/data` → 重新部署 `site/`。
- 也可自行放入更多时效数据后重跑构建工具。

## 依赖

- 构建期：`scipy`（NetCDF 编码）；后端需 `fastapi/uvicorn/ecmwf-opendata`（拉数据用）。
- 运行期：**零依赖**（资源已内嵌，无需联网加载库）。