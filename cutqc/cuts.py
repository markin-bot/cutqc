"""读剪辑点列表（成片时间轴上的切点，秒）。支持：
  - .txt：每行一个时间（秒 / H:MM:SS.mmm / H:MM:SS:FF），# 开头为注释
  - .csv：自动找第一列能解析成时间的；或列名含“切点/cut/时间/time/出点”的列
  - .json：短剧工具包的剪辑稿（clips 里有 vin/vout/speed），或 {"cuts":[...]}，或纯列表
  - .edl：CMX3600，取每条事件的 REC IN
"""
import csv
import json
import re

from .media import parse_time


def load_cuts(path, fps=25.0):
    p = path.lower()
    if p.endswith(".json"):
        d = json.load(open(path, encoding="utf-8"))
        if isinstance(d, list):
            return sorted(float(x) for x in d)
        if "cuts" in d:
            return sorted(float(x) for x in d["cuts"])
        if "clips" in d:  # 短剧工具包剪辑稿：按渲染规则累加镜头时长
            f = d.get("fps", fps)
            t, out = 0.0, []
            for c in d["clips"]:
                t += max(1, round((c["vout"] - c["vin"]) / c.get("speed", 1.0) * f)) / f
                out.append(round(t, 4))
            return out[:-1]
        raise ValueError("认不出的 json 剪辑点格式")
    if p.endswith(".edl"):
        out = []
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*\d+\s+\S+\s+\S+\s+\S+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", line)
            if m:
                out.append(parse_time(m[3], fps))
        if not out:
            return []
        base = min(out)
        return sorted(set(round(x - base, 4) for x in out if x > base))
    if p.endswith(".csv"):
        rows = list(csv.reader(open(path, encoding="utf-8-sig")))
        head = rows[0] if rows else []
        col = next((i for i, h in enumerate(head) if re.search(r"切点|cut|出点|time|时间", h, re.I)), None)
        out = []
        for r in rows[1:] if col is not None else rows:
            cells = [r[col]] if col is not None and col < len(r) else r
            for c in cells:
                try:
                    out.append(parse_time(c, fps))
                    break
                except Exception:
                    continue
        return sorted(out)
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line:
            out.append(parse_time(line.split()[0], fps))
    return sorted(out)


def load_audio_cuts(path, fps=25.0):
    """声音真正换来源的点（只对短剧工具包剪辑稿有效，其余格式返回 None = 跟画面剪辑点一样）。
    - 相邻两刀都是原声、且源素材首尾相连（台词连着说过切点）→ 声音是连续的，不算
    - 静音刀不算
    - 自带声音块 [ain, aout, off] 的，块的起止就是声音剪辑点"""
    if not path.lower().endswith(".json"):
        return None
    d = json.load(open(path, encoding="utf-8"))
    if not isinstance(d, dict) or "clips" not in d:
        return None
    f = d.get("fps", fps)
    t, out, CL = 0.0, [], d["clips"]
    for k, c in enumerate(CL):
        dur = max(1, round((c["vout"] - c["vin"]) / c.get("speed", 1.0) * f)) / f
        au = c.get("audio", "orig")
        if isinstance(au, list):
            for ain, aout, off in au:
                out += [t + off, t + off + (aout - ain)]
        elif au == "orig":
            pv = CL[k - 1] if k else None
            if not (pv and pv.get("audio", "orig") == "orig" and abs(c["vin"] - pv["vout"]) < 0.03 and not c.get("file")):
                out.append(t)
            nx = CL[k + 1] if k + 1 < len(CL) else None
            if not (nx and nx.get("audio", "orig") == "orig" and abs(nx["vin"] - c["vout"]) < 0.03 and not nx.get("file")):
                out.append(t + dur)
        t += dur
    return sorted(set(round(x, 3) for x in out if 0 < x < t))
