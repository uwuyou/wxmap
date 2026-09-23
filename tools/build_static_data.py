# -*- coding: utf-8 -*-
"""
构建期静态数据生成工具 (仅开发时运行, 网站运行期不需要 Python)
读取已下载的 GFS/EC NPZ 网格缓存, 裁剪为中国周边区域, 编码为 netCDF3 文件,
并在 static/data 下生成 manifest.json。前端用 netcdfjs 直接解析这些 .nc 文件。

用法:  python3 build_static_data.py
说明:  需存在 /workspace/wxmap/cache 下 gfs_*/ec_*.npz (由之前服务生成)。
"""
import os, json, glob
import numpy as np
from scipy.io import netcdf_file

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("WX_CACHE", os.path.join(os.path.dirname(ROOT), "cache"))
OUT = os.environ.get("WX_OUT", os.path.join(os.path.dirname(ROOT), "site", "data"))
os.makedirs(OUT, exist_ok=True)

REGION = {"latN": 60.0, "latS": 10.0, "lonW": 70.0, "lonE": 140.0}
LEVELS = ["surface", "925", "850", "700", "500", "300"]
GFS_STEPS = [0, 6, 12, 18, 24, 36, 48, 72, 96, 120]
EC_STEPS = [0, 3, 6, 9, 12, 18, 24, 36, 48, 72]

def load_npz(path):
    z = np.load(path, allow_pickle=True)
    g = {}
    for k in z.files:
        if k in ("lat", "lon"):
            continue
        g[k] = np.asarray(z[k], dtype="f4")
    return g, z["lat"].astype("f4"), z["lon"].astype("f4")

def list_runs(source):
    pat = os.path.join(CACHE, f"{source.lower()}_*_*.npz")
    runs = {}
    for p in glob.glob(pat):
        base = os.path.basename(p).replace(".npz", "")
        # gfs_DATE_HH_STEP
        parts = base.split("_")
        date, hour, step = parts[1], int(parts[2]), int(parts[3])
        runs.setdefault((date, hour), set()).add(step)
    # 选最晚时次
    if not runs:
        return []
    best = max(runs.keys())
    steps = sorted(runs[best])
    return [{"date": best[0], "hour": best[1], "steps": steps}]

def emit_nc(source, date, hour, step, outpath):
    npz = os.path.join(CACHE, f"{source.lower()}_{date}_{hour:02d}_{step:03d}.npz")
    grids, lat, lon = load_npz(npz)
    ny, nx = lat.size, lon.size
    if os.path.exists(outpath):
        os.remove(outpath)
    nc = netcdf_file(outpath, "w", version=1)
    nc.createDimension("lat", ny); nc.createDimension("lon", nx)
    v_lat = nc.createVariable("lat", "f4", ("lat",)); v_lat[:] = lat
    v_lon = nc.createVariable("lon", "f4", ("lon",)); v_lon[:] = lon
    for k, a in grids.items():
        aa = np.asarray(a, dtype="f4")
        if aa.ndim == 2:
            v = nc.createVariable(k, "f4", ("lat", "lon"))
            v[:] = aa
    nc.__setattr__("source", source); nc.__setattr__("date", f"{date}{hour:02d}")
    nc.__setattr__("step", int(step))
    nc.close()

def main():
    manifest = {"region": REGION, "levels": LEVELS, "sources": {}}
    for source in ("GFS", "EC"):
        runs = list_runs(source)
        if not runs:
            print(f"{source}: 无缓存, 跳过")
            continue
        r = runs[0]
        date, hour = r["date"], r["hour"]
        steps_avail = r["steps"]
        # 从该 source 定义里取它应有的步骤
        want = GFS_STEPS if source == "GFS" else EC_STEPS
        steps = [s for s in want if s in steps_avail] or steps_avail
        entry = {"date": date, "hour": hour, "steps": steps, "files": {}}
        for s in steps:
            fname = f"{source.lower()}_{date}_{hour:02d}_{s:03d}.nc"
            outpath = os.path.join(OUT, fname)
            emit_nc(source, date, hour, s, outpath)
            entry["files"][s] = fname
            print(f"  {source} +{s}h -> {fname} ({os.path.getsize(outpath)} B)")
        manifest["sources"][source] = entry
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("manifest.json 已生成")

if __name__ == "__main__":
    main()