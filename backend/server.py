# -*- coding: utf-8 -*-
"""
天气数据可视化后端 —— GFS(NCEP NOMADS 0.25°) 与 EC(ECMWF open-data) 数据获取/解码/裁剪/降采样/缓存
启动: python server.py   (FastAPI, http://127.0.0.1:8080)
"""
import os, io, time, math, json
from datetime import datetime, timedelta
import urllib.request
import numpy as np
try:
    from fastapi import FastAPI, Query
    from fastapi.responses import JSONResponse, FileResponse
    import uvicorn
except Exception:
    raise SystemExit("缺少依赖: pip install fastapi uvicorn --break-system-packages")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("WX_CACHE", os.path.join(ROOT, "cache"))
os.makedirs(CACHE, exist_ok=True)

DEFAULT_REGION = {"latN": 60.0, "latS": 10.0, "lonW": 70.0, "lonE": 140.0}  # 中国及周边
MAXDIM = 220
LEVELS = ["surface", "925", "850", "700", "500", "300"]
PL_LEVELS = ["925", "850", "700", "500", "300"]

ELEM_META = {
    "temp":  {"title": "温度",  "unit": "°C"},
    "rh":    {"title": "相对湿度", "unit": "%"},
    "cloud": {"title": "云量",  "unit": "%"},
    "rain":  {"title": "降水",  "unit": "mm/h"},
    "fog":   {"title": "雾",    "unit": ""},
}

# ---------------------------------------------------------------- 通用工具
def _blockdown(a, lat, lon, maxdim):
    a = np.asarray(a, dtype=float); ny, nx = a.shape
    while (ny > maxdim or nx > maxdim) and ny > 1 and nx > 1:
        fy = 2 if ny >= 2 else 1; fx = 2 if nx >= 2 else 1
        ny2, nx2 = (ny + fy - 1)//fy, (nx + fx - 1)//fx
        na = np.empty((ny2, nx2), dtype=float)
        for j in range(ny2):
            for i in range(nx2):
                blk = a[j*fy:j*fy+fy, i*fx:i*fx+fx].ravel()
                blk = blk[np.isfinite(blk)]
                na[j, i] = blk.mean() if blk.size else np.nan
        a, ny, nx = na, ny2, nx2
        lat = lat[::fy][:ny2]; lon = lon[::fx][:nx2]
        if fy == 1 and fx == 1:
            break
    return a, lat, lon

def _round(a):
    return [[None if x is None or not np.isfinite(x) else round(float(x), 3) for x in row] for row in a]

def _save_npz(path, grids, lat, lon):
    save = {k: np.asarray(grids[k], dtype=float) for k in grids}
    save["lat"] = np.asarray(lat); save["lon"] = np.asarray(lon)
    np.savez_compressed(path, **save)

def _load_npz(path):
    z = np.load(path, allow_pickle=True)
    g = {}
    for k in z.files:
        if k in ("lat", "lon"):
            continue
        g[k] = np.asarray(z[k], dtype=float)
    return g, z["lat"].astype(float), z["lon"].astype(float)

# ---------------------------------------------------------------- GFS
NOMADS = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

def _gfs_url(date, hour, step):
    reg = DEFAULT_REGION
    u = (f"{NOMADS}?dir=/gfs.{date}/{hour:02d}/atmos&file=gfs.t{hour:02d}z.pgrb2.0p25.f{step:03d}"
         f"&subregion=&bottomlat={reg['latS']}&toplat={reg['latN']}"
         f"&leftlon={reg['lonW']}&rightlon={reg['lonE']}")
    def v(uri, name, lev): return uri + f"&var_{name}=on&lev_{lev}=on"
    for p in PL_LEVELS:
        u = v(u, "TMP", f"{p}_mb");  u = v(u, "RH", f"{p}_mb")
        u = v(u, "UGRD", f"{p}_mb"); u = v(u, "VGRD", f"{p}_mb")
    u = v(u, "TMP", "2_m_above_ground")     # 2m 温度
    u = v(u, "RH",  "2_m_above_ground")     # 2m 相对湿度
    u = v(u, "TMP", "surface")              # 地面温度(备用)
    u = v(u, "UGRD", "10_m_above_ground")   # 10m 风
    u = v(u, "VGRD", "10_m_above_ground")
    u = v(u, "TCDC", "entire_atmosphere")   # 总云量
    u = v(u, "PRATE", "surface")            # 降水率 kg/m2/s
    return u

def _decode_grib(path):
    """用底层 FileStream 解码 GRIB, 兼容 cfgrib>=0.9.12(无 open_datasets)。
    返回 (lat, lon, raw): raw 键为 ('isob',P,shortName)|('hag',h,shortName)|('sfc',shortName)"""
    from cfgrib.messages import FileStream
    lat = lon = None
    raw = {}
    for _k, msg in FileStream(path).items():
        if isinstance(msg, Exception):
            continue
        try:
            vals = msg.get("values")
            if vals is None or getattr(vals, "ndim", 0) != 1:
                continue
            Nj = msg.get("Nj"); Ni = msg.get("Ni")
            if not Ni or not Nj:
                continue
            a = np.asarray(vals, dtype=float).reshape(Nj, Ni)
        except Exception:
            continue
        if lat is None:
            lat0 = msg.get("latitudeOfFirstGridPointInDegrees")
            lon0 = msg.get("longitudeOfFirstGridPointInDegrees")
            dj = msg.get("jDirectionIncrementInDegrees")
            di = msg.get("iDirectionIncrementInDegrees")
            if lat0 is not None and dj:
                # jScansPositively=1 时纬度沿 j 递增, 否则递减 (EC/GFS 兼容)
                if msg.get("jScansPositively") == 1:
                    lat = np.linspace(lat0, lat0 + dj*(Nj-1), Nj)
                else:
                    lat = np.linspace(lat0, lat0 - dj*(Nj-1), Nj)
                lon = np.linspace(lon0, lon0 + di*(Ni-1), Ni)
        sn, tl = msg.get("shortName"), msg.get("typeOfLevel")
        lv = msg.get("level", msg.get("heightAboveGround"))
        if tl == "isobaricInhPa":
            P = str(int(round(float(lv))))
            raw[("isob", P, sn)] = a
        elif tl == "heightAboveGround":
            raw[("hag", float(lv), sn)] = a
        else:
            raw[("sfc", sn)] = a
    # 统一为北→南方向 (la[0]=最北), 前端按此约定渲染
    if lat is not None and lat[0] < lat[-1]:
        lat = lat[::-1].copy()
        for _k in raw:
            raw[_k] = raw[_k][::-1]
    # 经度统一到 [-180,180): EC 用 0-360 表示时需调整
    if lon is not None and lon.size and lon[-1] > 180:
        lon = lon - 360.0
    return lat, lon, raw

def load_gfs(date, hour, step):
    npz = os.path.join(CACHE, f"gfs_{date}_{hour:02d}_{step:03d}.npz")
    if os.path.exists(npz):
        grids, lat, lon = _load_npz(npz)
        return {"grids": grids, "lat": lat, "lon": lon}
    rawgfs = os.path.join(CACHE, f"gfsraw_{date}_{hour:02d}_{step:03d}.grib2")
    if not os.path.exists(rawgfs) or os.path.getsize(rawgfs) < 1000:
        fb = _gfs_fetch(_gfs_url(date, hour, step))
        with open(rawgfs, "wb") as f:
            f.write(fb)
    lat, lon, raw = _decode_grib(rawgfs)
    # 裁剪到区域 (GFS subregion 下载通常已是区域, 此处兜底)
    s_lat = np.where((lat >= DEFAULT_REGION["latS"]) & (lat <= DEFAULT_REGION["latN"]))[0]
    s_lon = np.where((lon >= DEFAULT_REGION["lonW"]) & (lon <= DEFAULT_REGION["lonE"]))[0]
    if len(s_lat) != len(lat) or len(s_lon) != len(lon):
        lat, lon = lat[s_lat], lon[s_lon]
        raw = {k: a[np.ix_(s_lat, s_lon)] for k, a in raw.items()}
    grids = {}
    # 气压层: 温度(K->°C) / 湿度% / 风m/s
    for P in PL_LEVELS:
        for sn, nm in (("t", "temp"), ("r", "rh")):
            a = raw.get(("isob", P, sn))
            if a is not None:
                grids[f"{P}_{nm}"] = a - 273.15 if nm == "temp" else a
        for sn in ("u", "v"):
            a = raw.get(("isob", P, sn))
            if a is not None:
                grids[f"{P}_{sn}"] = a
    # 地面要素 (GFS shortName: 2t/2r/10u/10v/t/prate/tcc)
    t2 = raw.get(("hag", 2, "2t")); t_sfc = raw.get(("sfc", "t"))
    if t2 is not None: grids["sfc_temp"] = t2 - 273.15
    elif t_sfc is not None: grids["sfc_temp"] = t_sfc - 273.15
    r2 = raw.get(("hag", 2, "2r"))
    if r2 is not None: grids["sfc_rh"] = r2
    u10 = raw.get(("hag", 10, "10u")); v10 = raw.get(("hag", 10, "10v"))
    if u10 is not None: grids["sfc_u"] = u10
    if v10 is not None: grids["sfc_v"] = v10
    tcc = raw.get(("sfc", "tcc"))
    if tcc is not None: grids["sfc_cloud"] = tcc
    prate = raw.get(("sfc", "prate"))
    if prate is not None: grids["sfc_rain"] = prate * 3600.0  # kg/m2/s -> mm/h
    # 雾(派生): 云量*0.6 + (近地湿度-70)饱和
    if tcc is not None and r2 is not None:
        grids["sfc_fog"] = np.minimum(100.0, tcc*0.6 + np.maximum(0.0, r2 - 70.0) * 1.0)
    zlat, zlon = lat, lon
    first = True
    for k in grids:
        if first:
            grids[k], zlat, zlon = _blockdown(grids[k], lat, lon, MAXDIM)
            first = False
        else:
            grids[k], _, _ = _blockdown(grids[k], lat, lon, MAXDIM)
    _save_npz(npz, grids, zlat, zlon)
    return {"grids": grids, "lat": zlat, "lon": zlon}

def _gfs_fetch(url, timeout=180):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) < 100:
                raise RuntimeError(f"GFS empty response {len(data)}B")
            return data
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"GFS download failed: {e}")
            time.sleep(2)

# ---------------------------------------------------------------- EC(ECMWF open-data)
def ec_path(date, hour, step):
    return os.path.join(CACHE, f"ec_{date}_{hour:02d}_{step:03d}.grib2")

def ensure_ec(date, hour, step):
    tg = ec_path(date, hour, step)
    if os.path.exists(tg) and os.path.getsize(tg) > 1000*1000:
        return tg
    from ecmwf.opendata import Client
    cl = Client()
    last = None
    for _ in range(3):
        try:
            cl.download(source="ecmwf", target=tg, date=int(date),
                        time=int(hour), step=int(step), type="fc")
            return tg
        except Exception as e:
            last = e; time.sleep(2)
    raise RuntimeError(f"EC download failed: {last}")

def _ec_rh2m(t2m, d2m):
    """由 2m 温度与 2m 露点 计算 2m 相对湿度 (%)"""
    tc = np.asarray(t2m, float) - 273.15; td = np.asarray(d2m, float) - 273.15
    e = lambda x: np.exp((17.625 * x) / (x + 243.04))
    with np.errstate(invalid="ignore", divide="ignore"):
        rh = 100.0 * e(td) / e(tc)
    return np.clip(rh, 0.0, 100.0)

def load_ec(date, hour, step):
    npz = os.path.join(CACHE, f"ec_{date}_{hour:02d}_{step:03d}.npz")
    if os.path.exists(npz):
        grids, lat, lon = _load_npz(npz)
        return {"grids": grids, "lat": lat, "lon": lon}
    tg = ensure_ec(date, hour, step)
    lat, lon, raw = _decode_grib(tg)
    s_lat = np.where((lat >= DEFAULT_REGION["latS"]) & (lat <= DEFAULT_REGION["latN"]))[0]
    s_lon = np.where((lon >= DEFAULT_REGION["lonW"]) & (lon <= DEFAULT_REGION["lonE"]))[0]
    lat, lon = lat[s_lat], lon[s_lon]
    grids = {}
    for P in PL_LEVELS:
        temp = raw.get(("isob", P, "t"))
        if temp is not None:
            grids[f"{P}_temp"] = temp[np.ix_(s_lat, s_lon)] - 273.15
        for sn, nm in (("r","rh"), ("u","u"), ("v","v")):
            a = raw.get(("isob", P, sn))
            if a is not None:
                grids[f"{P}_{nm}"] = a[np.ix_(s_lat, s_lon)]
    # EC shortName 与 GFS 一致: 2t/2d 在 heightAboveGround=2, 10u/10v 在 heightAboveGround=10
    t2 = raw.get(("hag", 2, "2t")); d2 = raw.get(("hag", 2, "2d"))
    u10 = raw.get(("hag", 10, "10u")); v10 = raw.get(("hag", 10, "10v"))
    tcc = raw.get(("sfc", "tcc"))
    tp = raw.get(("sfc", "tp"))
    if t2 is not None: grids["sfc_temp"] = t2[np.ix_(s_lat, s_lon)] - 273.15
    if u10 is not None: grids["sfc_u"] = u10[np.ix_(s_lat, s_lon)]
    if v10 is not None: grids["sfc_v"] = v10[np.ix_(s_lat, s_lon)]
    if tcc is not None: grids["sfc_cloud"] = tcc[np.ix_(s_lat, s_lon)] * 100.0
    if tp is not None:  grids["sfc_rain"] = tp[np.ix_(s_lat, s_lon)] * 1000.0  # m -> mm
    # 2m 相对湿度由 2m 温度+露点 计算; 雾由 云量+2mRH 派生
    if t2 is not None and d2 is not None:
        rh2m = _ec_rh2m(t2[np.ix_(s_lat, s_lon)], d2[np.ix_(s_lat, s_lon)])
        grids["sfc_rh"] = rh2m
        if tcc is not None:
            tcc2 = tcc[np.ix_(s_lat, s_lon)] * 100.0
            grids["sfc_fog"] = np.minimum(100.0, tcc2*0.6 + np.maximum(0.0, rh2m - 70.0))
    zlat, zlon = lat, lon
    first = True
    for k in grids:
        if first:
            grids[k], zlat, zlon = _blockdown(grids[k], lat, lon, MAXDIM)
            first = False
        else:
            grids[k], _, _ = _blockdown(grids[k], lat, lon, MAXDIM)
    _save_npz(npz, grids, zlat, zlon)
    return {"grids": grids, "lat": zlat, "lon": zlon}

# ---------------------------------------------------------------- 统一
def load_source(source, date, hour, step):
    if source == "GFS":
        return load_gfs(date, hour, step)
    return load_ec(date, hour, step)

def build_payload(source, date, hour, step, level):
    d = load_source(source, date, hour, step)
    grids, lat, lon = d["grids"], d["lat"], d["lon"]
    def g(P, name):
        return grids.get(f"{P}_{name}")
    pay = {"source": source, "date": f"{date}{hour:02d}", "step": int(step),
           "level": level, "valid": _valid(date, hour, step),
           "lat": np.round(lat, 4).tolist(), "lon": np.round(lon, 4).tolist(),
           "elems": {}, "wind": {}}
    # 地面要素
    for name in ("temp", "rh", "cloud", "rain", "fog"):
        a = g("sfc", name)
        if a is not None:
            pay["elems"][name] = {"v": _round(a), "meta": ELEM_META[name]}
    # 气压层温度/湿度
    if level != "surface":
        for name in ("temp", "rh"):
            a = g(level, name)
            if a is not None:
                pay["elems"][name] = {"v": _round(a), "meta": ELEM_META[name]}
    # 风 (地面→10m, 气压层→该层)
    wp = "sfc" if level == "surface" else level
    u, v = g(wp, "u"), g(wp, "v")
    if u is not None and v is not None:
        mag = np.hypot(u, v)
        pay["wind"] = {"u": _round(u), "v": _round(v),
                       "mag": round(float(np.nanmax(mag)), 1)}
    return pay

def _valid(date, hour, step):
    t = datetime.strptime(date, "%Y%m%d") + timedelta(hours=int(hour) + int(step))
    return t.strftime("%Y-%m-%d %H:%M")

def _probe_latest(source):
    now = datetime.now()
    tried = []
    for bi in range(5):
        day = now - timedelta(days=bi)
        date = day.strftime("%Y%m%d")
        for hour in (0, 6, 12, 18):
            if bi == 0 and hour >= now.hour:
                continue
            key = (date, hour)
            if key in tried:
                continue
            tried.append(key)
            # 优先本地缓存 (秒回), 无缓存才下载探测
            npz = os.path.join(CACHE, f"{source.lower()}_{date}_{hour:02d}_000.npz")
            if os.path.exists(npz):
                return date, hour
            try:
                load_source(source, date, hour, 0)
                return date, hour
            except Exception:
                continue
    return None

# ---------------------------------------------------------------- FastAPI
app = FastAPI(title="EC/GFS 天气可视化")

from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response as FResponse
try:
    from scipy.io import netcdf_file
    HAVE_SCIPY_NC = True
except Exception:
    HAVE_SCIPY_NC = False
FRONT = os.path.join(ROOT, "frontend")
app.mount("/static", StaticFiles(directory=FRONT), name="static")

@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "frontend", "index.html"))

@app.get("/api/runs")
def runs(source: str = Query("GFS")):
    src = source.upper()
    if src not in ("GFS", "EC"):
        return JSONResponse({"error": "bad source"}, status_code=400)
    r = _probe_latest(src)
    if not r:
        return JSONResponse({"error": "no data available"}, status_code=503)
    date, hour = r
    steps = [0, 3, 6, 9, 12, 18, 24, 36, 48, 72] if src == "EC" else \
            [0, 6, 12, 18, 24, 36, 48, 72, 96, 120]
    return {"source": src, "date": date, "hour": hour, "steps": steps,
            "levels": LEVELS, "region": DEFAULT_REGION,
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M")}

@app.get("/api/grid")
def grid_api(source: str = Query("GFS"), date: str = Query(""),
             hour: int = Query(0), step: int = Query(0),
             level: str = Query("surface")):
    src = source.upper()
    if src not in ("GFS", "EC"):
        return JSONResponse({"error": "bad source"}, status_code=400)
    if not date:
        r = _probe_latest(src)
        if not r:
            return JSONResponse({"error": "no data"}, status_code=503)
        date, hour = r
    if level not in LEVELS:
        level = "surface"
    try:
        t0 = time.time()
        p = build_payload(src, date, hour, step, level)
        p["ms"] = round((time.time() - t0) * 1000)
        return p
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ------------------------------------------------------ NetCDF 下发(前端直接解析)
def grids_of(source, date, hour, step, level):
    """取当前要下发的 2D 网格变量: 返回 {var: np.ndarray2D}, 以及 lat/lon。"""
    d = load_source(source, date, hour, step)
    grids, lat, lon = d["grids"], d["lat"], d["lon"]
    def g(P, name):
        return grids.get(f"{P}_{name}")
    out = {}
    for name in ("temp", "rh", "cloud", "rain", "fog"):
        a = g("sfc", name)
        if a is not None:
            out["sfc_"+name] = a
    if level != "surface":
        for name in ("temp", "rh"):
            a = g(level, name)
            if a is not None:
                out[level+"_"+name] = a
    wp = "sfc" if level == "surface" else level
    u, v = g(wp, "u"), g(wp, "v")
    if u is not None and v is not None:
        out["wind_u"], out["wind_v"] = u, v
    return out, lat, lon

def encode_netcdf(source, date, hour, step, level):
    """把网格数据编码为 netCDF3(经典格式) 二进制, 可由浏览器 netcdfjs 直接解析。
    变量命名约定: 维度 lat,lon; 要素 sfc_temp/sfc_cloud/xxx_temp... ; wind_u/wind_v。
    """
    out, lat, lon = grids_of(source, date, hour, step, level)
    ny, nx = lat.size, lon.size
    if not HAVE_SCIPY_NC:
        raise RuntimeError("缺少 scipy (pip install scipy)")
    fname = os.path.join(CACHE, f"_tmp_{source}_{date}_{hour:02d}_{int(step):03d}_{level}.nc")
    try:
        if os.path.exists(fname):
            os.remove(fname)
        nc = netcdf_file(fname, "w", version=1)  # NETCDF3 经典格式, netcdfjs 广泛支持
        nc.createDimension("lat", ny); nc.createDimension("lon", nx)
        v_lat = nc.createVariable("lat", "f4", ("lat",)); v_lat[:] = lat
        v_lon = nc.createVariable("lon", "f4", ("lon",)); v_lon[:] = lon
        for k, a in out.items():
            aa = np.asarray(a, dtype="f4")
            if aa.ndim == 2:
                v = nc.createVariable(k, "f4", ("lat", "lon"))
                v[:] = aa
        nc.__setattr__("source", source); nc.__setattr__("date", f"{date}{hour:02d}")
        nc.__setattr__("step", int(step)); nc.__setattr__("level", level)
        nc.close()
        with open(fname, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(fname):
            try: os.remove(fname)
            except Exception: pass

@app.get("/api/nc")
def nc_api(source: str = Query("GFS"), date: str = Query(""),
           hour: int = Query(0), step: int = Query(0),
           level: str = Query("surface")):
    src = source.upper()
    if src not in ("GFS", "EC"):
        return JSONResponse({"error": "bad source"}, status_code=400)
    if not date:
        r = _probe_latest(src)
        if not r:
            return JSONResponse({"error": "no data"}, status_code=503)
        date, hour = r
    if level not in LEVELS:
        level = "surface"
    try:
        t0 = time.time()
        blob = encode_netcdf(src, date, hour, step, level)
        ms = round((time.time() - t0) * 1000)
        # 追加 encoding 便于前端解码
        meta = {"source": src, "date": f"{date}{hour:02d}", "step": int(step),
                "level": level, "ms": ms}
        return FResponse(content=blob,
                         media_type="application/x-netcdf",
                         headers={"X-Grid-Meta": json.dumps(meta)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.environ.get("WX_PORT", "8080")), log_level="warning")