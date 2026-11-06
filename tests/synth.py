"""压力测试用的测试片生成器：从真实素材里截片段，按配方拼接并埋入已知问题，同时写出标准答案。

一个配方（recipe）= dict:
  fps, size=(W,H)
  v: 画面片段列表，每段 {"src":素材, "t":起点秒, "n":帧数, "vf":额外滤镜}
     或 {"color":"black"/"white", "n":帧数}
  a: 声音片段列表 {"src", "t", "d":秒, "af":额外滤镜}；省略时 = 跟画面片段一一对应
  post_v / post_a: 拼接后整体再加的滤镜（局部闪烁、咔哒声等）
  enc: 编码参数（格式矩阵用）
"""
import hashlib
import json
import os
import random
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from cutqc.media import ffmpeg, load_audio, probe  # noqa: E402
from cutqc import flash as F  # noqa: E402

CACHE = os.path.expanduser("~/.cache/cutqc/bench")


def _h(obj):
    return hashlib.md5(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def render(recipe, out):
    """按配方渲染测试片。返回 out。"""
    if os.path.exists(out):
        return out
    fps = recipe["fps"]
    W, H = recipe.get("size", (960, 540))
    cmd_inputs = []
    vin_idx = []
    for s in recipe["v"]:
        if "color" in s:
            cmd_inputs += ["-f", "lavfi", "-i", f"color={s['color']}:s={W}x{H}:r={fps}:d={s['n'] / fps + 0.2:.3f}"]
        else:
            cmd_inputs += ["-ss", f"{s['t']:.4f}", "-t", f"{s['n'] / fps + 0.5:.4f}", "-i", s["src"]]
        vin_idx.append(len(vin_idx))
    asegs = recipe.get("a")
    if recipe.get("no_audio"):
        asegs = []
    elif asegs is None:
        asegs = [{"src": s.get("src"), "t": s.get("t", 0), "d": s["n"] / fps, "silent": "color" in s} for s in recipe["v"]]
    ain_idx = []
    base = len(vin_idx)
    for s in asegs:
        if s.get("silent") or not s.get("src"):
            cmd_inputs += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={s['d'] + 0.2:.3f}"]
        else:
            cmd_inputs += ["-ss", f"{s['t']:.4f}", "-t", f"{s['d'] + 0.5:.4f}", "-i", s["src"]]
        ain_idx.append(base + len(ain_idx))
    extra_inputs = recipe.get("extra_inputs", [])
    for ex in extra_inputs:
        cmd_inputs += ["-ss", f"{ex['t']:.4f}", "-t", "1", "-i", ex["src"]]
    parts = []
    for i, s in enumerate(recipe["v"]):
        vf = f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
        if s.get("pre"):
            vf = s["pre"] + "," + vf
        vf += f",trim=end_frame={s['n']},setpts=PTS-STARTPTS"
        if s.get("vf"):
            vf += "," + s["vf"]
        parts.append(f"[{vin_idx[i]}:v]{vf}[v{i}]")
    for i, s in enumerate(asegs):
        af = f"aresample=44100,aformat=channel_layouts=stereo,atrim=duration={s['d']:.5f},asetpts=PTS-STARTPTS"
        if s.get("af"):
            af += "," + s["af"]
        parts.append(f"[{ain_idx[i]}:a]{af}[a{i}]")
    nv, na = len(recipe["v"]), len(asegs)
    parts.append("".join(f"[v{i}]" for i in range(nv)) + f"concat=n={nv}:v=1:a=0[vc]")
    if na:
        parts.append("".join(f"[a{i}]" for i in range(na)) + f"concat=n={na}:v=0:a=1[ac]")
    pv = recipe.get("post_v")
    pa = recipe.get("post_a")
    vout, aout = "[vc]", "[ac]"
    if pv:
        parts.append(pv.replace("{IN}", "[vc]").replace("{OUT}", "[vo]"))
        vout = "[vo]"
    if pa and na:
        parts.append(pa.replace("{IN}", "[ac]").replace("{OUT}", "[ao]"))
        aout = "[ao]"
    enc = recipe.get("enc") or ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-b:a", "160k"]
    maps = ["-map", vout] + ([] if recipe.get("no_audio") else ["-map", aout])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part" + os.path.splitext(out)[1]
    cmd = [ffmpeg(), "-v", "error", "-y"] + cmd_inputs + ["-filter_complex", ";".join(parts)] + maps + enc + [tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("生成测试片失败：" + r.stderr[-800:])
    os.replace(tmp, out)
    return out


# ---------- 挑素材 ----------

class Picker:
    """在素材里随机找：一个 ≥dur 秒没有镜头切换的连续镜头；找过的记在缓存里，结果可复现。"""

    def __init__(self, sources, seed=7):
        self.sources = sources
        self.rng = random.Random(seed)
        self.info = {s: probe(s) for s in sources}
        os.makedirs(CACHE, exist_ok=True)
        self.cf = os.path.join(CACHE, "picks.json")
        self.memo = json.load(open(self.cf)) if os.path.exists(self.cf) else {}

    def _save(self):
        json.dump(self.memo, open(self.cf, "w"), ensure_ascii=False, indent=0)

    def shots(self, src):
        """短素材（<15 分钟）整片扫一遍，返回所有镜头 [(起, 止)]；长素材返回 None（随机抽查）"""
        info = self.info[src]
        if info["duration"] > 900:
            return None
        mk = f"shots2|{src}"   # v2：按新版切换检测重扫
        if mk not in self.memo:
            M = F.measure(src, info["fps"])
            c = [i / info["fps"] for i in F.detect_cuts(M)]
            b = [0.0] + c + [info["duration"]]
            self.memo[mk] = [(round(x + 0.15, 3), round(y - 0.15, 3)) for x, y in zip(b, b[1:]) if y - x > 0.5]
            self._save()
        return self.memo[mk]

    def stable(self, src, dur, key, need_loud=None, avoid=()):
        """返回起点 t：[t, t+dur] 内没有镜头切换；need_loud=x 时要求 t+x 处声音够响（-28dB 以上）"""
        mk = f"{src}|{dur}|{key}|{need_loud}"
        if mk in self.memo:
            return self.memo[mk]
        info = self.info[src]
        sh = self.shots(src)
        if sh is not None:
            cand = [s for s in sh if s[1] - s[0] >= dur]
            if not cand:
                raise RuntimeError(f"{src} 里没有 {dur}s 以上的连续镜头")
        for _ in range(40):
            if sh is not None:
                a, b = self.rng.choice(cand)
                t = round(self.rng.uniform(a, b - dur), 2)
                if any(abs(t - x) < 1 for x in avoid):
                    continue
            else:
                t = round(self.rng.uniform(30, info["duration"] - dur - 30), 2)
            if sh is None and any(abs(t - a) < 60 for a in avoid):
                continue
            if need_loud is not None:
                bad = False
                for nl in (need_loud if isinstance(need_loud, (list, tuple)) else [need_loud]):
                    w = load_audio(src, 16000, t + nl - 0.1, 0.2)
                    if len(w) == 0 or 20 * np.log10(np.sqrt(np.mean(w ** 2)) + 1e-9) < -28:
                        bad = True
                if bad:
                    continue
            M = F.measure(src, info["fps"], t, dur)
            if F.detect_cuts(M):
                continue
            if M["luma"].mean() < 25 or M["luma"].mean() > 230:
                continue
            self.memo[mk] = t
            self._save()
            return t
        # 随机抽 dur 秒没找到：改成一次解码 90 秒，在里面找够长的镜头
        for _ in range(40):
            w0 = round(self.rng.uniform(30, info["duration"] - 120), 2)
            if any(abs(w0 - a) < 60 for a in avoid):
                continue
            M = F.measure(src, info["fps"], w0, 90)
            if M["luma"].mean() < 25:
                continue
            c = [0] + F.detect_cuts(M) + [len(M["d1"])]
            for x, y in zip(c, c[1:]):
                if (y - x) / info["fps"] >= dur + 0.4:
                    t = round(w0 + x / info["fps"] + 0.2, 2)
                    if need_loud is not None:
                        ok = all(len(w) and 20 * np.log10(np.sqrt(np.mean(w ** 2)) + 1e-9) >= -28
                                 for w in [load_audio(src, 16000, t + nl - 0.1, 0.2)
                                           for nl in (need_loud if isinstance(need_loud, (list, tuple)) else [need_loud])])
                        if not ok:
                            continue
                    self.memo[mk] = t
                    self._save()
                    return t
        raise RuntimeError(f"在 {src} 里找不到 {dur}s 的连续镜头")

    def loud(self, src, dur, key, offsets):
        """声音用例：只要求这几个时间点声音够响（画面有没有切换无所谓）"""
        mk = f"loud|{src}|{dur}|{key}|{offsets}"
        if mk in self.memo:
            return self.memo[mk]
        d = self.info[src]["duration"]
        for _ in range(400):
            t = round(self.rng.uniform(min(30, d * 0.05), d - dur - min(30, d * 0.05)), 2)
            ok = True
            for nl in offsets:
                w = load_audio(src, 16000, t + nl - 0.1, 0.2)
                if len(w) == 0 or 20 * np.log10(np.sqrt(np.mean(w ** 2)) + 1e-9) < -28:
                    ok = False
                    break
            if ok:
                self.memo[mk] = t
                self._save()
                return t
        raise RuntimeError(f"{src} 里找不到声音够响的位置")

    def far(self, src, ref, key):
        """一个离 ref 很远（不同场景）的起点"""
        mk = f"far|{src}|{ref}|{key}"
        if mk not in self.memo:
            d = self.info[src]["duration"]
            sh = self.shots(src)
            if sh is not None:   # 短素材：换一个别的镜头
                mine = [s for s in sh if s[0] - 0.2 <= ref <= s[1] + 0.2]
                other = [s for s in sh if s not in mine and s[1] - s[0] > 0.3]
                a, b = self.rng.choice(other)
                t = round(self.rng.uniform(a, max(a, b - 0.3)), 2)
            else:
                gap = min(120, d * 0.3)
                for _ in range(1000):
                    t = round(self.rng.uniform(min(30, d * 0.05), d - min(40, d * 0.1)), 2)
                    if abs(t - ref) > gap:
                        break
            self.memo[mk] = t
            self._save()
        return self.memo[mk]
