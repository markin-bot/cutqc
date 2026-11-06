"""读视频帧 / 音频 / 基本信息。只依赖 ffmpeg（优先系统 PATH，其次 imageio-ffmpeg 自带的）。"""
import os
import re
import shutil
import subprocess
import sys

import numpy as np

_FF = None


def ffmpeg():
    global _FF
    if _FF:
        return _FF
    p = os.environ.get("CUTQC_FFMPEG") or shutil.which("ffmpeg")
    if not p:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
    _FF = p
    return p


def probe(path):
    """返回 dict(duration, fps, width, height, has_audio)"""
    err = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    info = {"duration": 0.0, "fps": 25.0, "width": 0, "height": 0, "has_audio": False}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    for line in err.splitlines():
        if "Video:" in line and not info["width"]:
            m = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if m:
                info["width"], info["height"] = int(m[1]), int(m[2])
            m = re.search(r"([\d.]+) fps", line) or re.search(r"([\d.]+) tbr", line)
            if m:
                info["fps"] = float(m[1])
        if "Audio:" in line:
            info["has_audio"] = True
    return info


def _range_args(start, dur):
    a = []
    if start:
        a += ["-ss", f"{start:.3f}"]
    if dur:
        a += ["-t", f"{dur:.3f}"]
    return a


def iter_frames(path, w=64, h=36, start=0.0, dur=None, chunk=600, fps=None):
    """逐块产出 (帧数组 [n,h,w,3] uint8)。按恒定帧率解码。"""
    cmd = [ffmpeg(), "-v", "error"]
    if sys.platform == "darwin":
        cmd += ["-hwaccel", "videotoolbox"]
    cmd += _range_args(start, dur) + ["-i", path, "-an", "-sn",
                                       "-vf", (f"fps={fps}," if fps else "") + f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    fs = w * h * 3
    try:
        while True:
            buf = p.stdout.read(fs * chunk)
            if not buf:
                break
            n = len(buf) // fs
            yield np.frombuffer(buf[: n * fs], np.uint8).reshape(n, h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def load_audio(path, sr=16000, start=0.0, dur=None):
    """单声道 float32，[-1,1]"""
    cmd = [ffmpeg(), "-v", "error"] + _range_args(start, dur) + ["-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                                                                  "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def frame_strip(path, t, fps, out_png, n=7, width=200):
    """以 t 为中心截 n 帧拼成一行（中间那帧是 t），用于报告缩略图"""
    st = max(0.0, t - (n // 2) / fps)
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-ss", f"{st:.3f}", "-i", path, "-frames:v", "1",
                    "-vf", f"select='lt(n,{n})',scale={width}:-2,tile={n}x1:padding=2:color=white",
                    "-update", "1", out_png], capture_output=True)
    return os.path.exists(out_png)


def tc(t, fps=None):
    """秒 -> 时:分:秒.毫秒（给了 fps 时附带帧号）"""
    t = max(0.0, t)
    s = f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}"
    if fps:
        s += f"（第{int(round(t * fps))}帧）"
    return s


def parse_time(s, fps=25.0):
    """支持 秒数 / H:MM:SS(.mmm) / MM:SS / H:MM:SS:FF / H:MM:SS,mmm"""
    s = s.strip().replace(",", ".")
    if re.fullmatch(r"-?[\d.]+", s):
        return float(s)
    p = s.split(":")
    if len(p) == 4:
        return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2]) + int(p[3]) / fps
    if len(p) == 3:
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
    if len(p) == 2:
        return int(p[0]) * 60 + float(p[1])
    raise ValueError(s)
