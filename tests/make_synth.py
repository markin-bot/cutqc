"""造一段带已知问题的测试视频，验证 cutqc 能不能抓到。
用法: python make_synth.py 任意素材.mp4 输出目录
埋的问题（成片时间）：
  5.000  插入 1 帧别的镜头（孤立闪帧，前后是同一个连续镜头）
  10.000 剪辑点处 3 帧碎镜头（10.000–10.100）
  10.100 声音硬切（没有淡入淡出）
字幕：第 2 句故意晚 0.8 秒
"""
import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cutqc.media import ffmpeg
src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
FF = ffmpeg()
segs = [(100.0, 5.0), (2000.0, 1 / 30), (105.0 + 1 / 30, 5.0 - 1 / 30), (3000.0, 0.1), (400.0, 9.9)]
parts = []
for k, (a, d) in enumerate(segs):
    p = f"{out}/p{k}.ts"
    subprocess.run([FF, "-v", "error", "-y", "-ss", f"{a:.4f}", "-i", src, "-frames:v", str(max(1, round(d * 30))), "-t", f"{d:.4f}",
                    "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-f", "mpegts", p], check=True)
    parts.append(p)
open(f"{out}/list.txt", "w").write("\n".join(f"file '{p}'" for p in parts))
subprocess.run([FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{out}/list.txt", "-c", "copy", f"{out}/synth.mp4"], check=True)
open(f"{out}/cuts.txt", "w").write("5.0\n5.033\n10.0\n10.1\n")
print("ok", out)
