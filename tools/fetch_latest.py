# -*- coding: utf-8 -*-
"""
GitHub Actions 专用数据抓取工具。
复用 backend/server.py 的 load_source() 下载 GRIB、解码并生成 NPZ 缓存，
把指定数据源、最近可用时次的所有时效都缓存好，供 build_static_data.py 生成 .nc。

用法:
  python3 fetch_latest.py [GFS|EC] [--hours 0 6 12 18 24 36 48 72 96 120]
"""
import os, sys, time

# 让 backend 可导入
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import server  # noqa: E402  (server.py 同目录)

GFS_STEPS = [0, 6, 12, 18, 24, 36, 48, 72, 96, 120]
EC_STEPS = [0, 3, 6, 9, 12, 18, 24, 36, 48, 72]


def fetch_source(source):
    print(f"[fetch] 探测 {source} 最近时次 ...")
    r = server._probe_latest(source)  # 会下载 step0 并写缓存
    if not r:
        print(f"[fetch] {source} 无可用数据")
        return False
    date, hour = r
    print(f"[fetch] {source} 起报 {date} {hour:02d}时")
    steps = EC_STEPS if source == "EC" else GFS_STEPS
    for s in steps:
        npz = os.path.join(server.CACHE, f"{source.lower()}_{date}_{hour:02d}_{s:03d}.npz")
        if os.path.exists(npz):
            print(f"[fetch]   +{s}h 已缓存, 跳过")
            continue
        t0 = time.time()
        try:
            server.load_source(source, date, hour, s)
            print(f"[fetch]   +{s}h 完成 ({time.time()-t0:.1f}s)")
        except Exception as e:
            print(f"[fetch]   +{s}h 失败: {e}")
    print(f"[fetch] {source} 完成")
    return True


if __name__ == "__main__":
    sources = sys.argv[1:] or ["GFS", "EC"]
    for s in sources:
        fetch_source(s.upper())
    print("[fetch] 全部完成")