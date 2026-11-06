"""cutqc —— 成片质检：闪帧 / 声音切点 / 字幕对齐

用法:
  cutqc 成片.mp4 [--subs 字幕.srt|.ass] [--cuts 剪辑点.txt|.csv|.json|.edl] [选项]

常用选项:
  --only flash,audio,subs   只跑某几项（默认全跑；没给字幕时跳过字幕项）
  --start 秒 --dur 秒       只查一段（调参、抽查用）
  --asr small|medium|none   识别模型（默认 small；none = 不识别，声音项只做波形检查、字幕项跳过）
  --near 0.5                剪辑点前后多少秒内的碎镜头算“剪辑造成”
  --out 目录                报告输出目录（默认：视频旁边的 “<文件名>_质检”）
退出码：有“高”级问题时为 1，方便接进批处理。
"""
import argparse
import os
import sys
import time

from . import audio as A
from . import flash as F
from . import report as R
from . import subs as S
from .cuts import load_audio_cuts, load_cuts
from .media import load_audio, probe, tc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cutqc", description="成片质检：闪帧 / 声音切点 / 字幕对齐")
    ap.add_argument("video")
    ap.add_argument("--subs")
    ap.add_argument("--cuts")
    ap.add_argument("--audio-cuts", help="声音剪辑点（和画面剪辑点不同时给，比如 J/L 剪）；格式同 --cuts")
    ap.add_argument("--only", default="flash,audio,subs")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--dur", type=float)
    ap.add_argument("--asr", default="small")
    ap.add_argument("--near", type=float, default=0.5)
    ap.add_argument("--out")
    ap.add_argument("--lang", default="auto")
    a = ap.parse_args(argv)

    t_begin = time.time()
    log = lambda s: print(s, flush=True)
    if not os.path.exists(a.video):
        sys.exit(f"找不到视频：{a.video}")
    info = probe(a.video)
    fps = info["fps"]
    only = set(a.only.split(","))
    end = a.start + a.dur if a.dur else info["duration"]
    rng = (a.start, end)
    out = a.out or os.path.splitext(a.video)[0] + "_质检"
    os.makedirs(out, exist_ok=True)
    log(f"视频 {os.path.basename(a.video)}：{tc(info['duration'])}，{fps:g} fps，{info['width']}x{info['height']}")

    cuts = None
    if a.cuts:
        cuts = [t for t in load_cuts(a.cuts, fps) if rng[0] <= t <= rng[1]]
        log(f"剪辑点 {len(cuts)} 个（来自 {os.path.basename(a.cuts)}）")

    issues = []
    video_cuts = []
    if "flash" in only:
        log("① 闪帧：逐帧比对中…")
        last = [0]

        def prog(t):
            if t - last[0] > 600:
                last[0] = t
                log(f"  已看 {tc(a.start + t)}")
        M = F.measure(a.video, fps, a.start, a.dur, prog)
        fl, video_cuts = F.find_flashes(M, cuts, near=a.near)
        issues += fl
        log(f"  镜头切换 {len(video_cuts)} 处，闪帧类问题 {len(fl)} 条")

    wav, words = None, None
    need_asr = a.asr != "none" and (("subs" in only and a.subs) or "audio" in only)
    if ("audio" in only or need_asr) and info["has_audio"]:
        wav = load_audio(a.video, 16000, a.start, a.dur)
    if need_asr and wav is not None:
        log(f"② 逐字语音识别（{a.asr}）…")
        from .asr import transcribe
        words = transcribe(wav, a.video, a.start, a.dur, a.asr, language=a.lang, log=log)
        log(f"  识别到 {len(words)} 个词")

    if "audio" in only and wav is not None:
        acuts = None
        if a.audio_cuts:
            acuts = load_cuts(a.audio_cuts, fps)
        elif a.cuts:
            acuts = load_audio_cuts(a.cuts, fps)
            if acuts is not None:
                log(f"  从剪辑稿算出声音真正换来源的点 {len(acuts)} 个（台词连着说过画面切点的不算）")
        if acuts is not None:
            acuts = [t for t in acuts if rng[0] <= t <= rng[1]]
        pts = acuts if acuts is not None else (cuts if cuts is not None else video_cuts)
        pts = [p for i, p in enumerate(sorted(pts)) if i == 0 or p - sorted(pts)[i - 1] > 0.08]  # 合并挨得很近的点
        src = "剪辑点" if cuts is not None else "画面镜头切换处（没给剪辑点，按镜头切换当切点，结果仅供参考）"
        log(f"③ 声音切点：检查 {len(pts)} 个{src}")
        au = A.check_points(wav, pts, words, t0=a.start, path=a.video)
        if cuts is None:
            for it in au:  # 镜头切换不一定是声音剪辑点：降一级
                it["sev"] = {"高": "中", "中": "低"}.get(it["sev"], "低")
                it["note"] = "按画面镜头切换推测的切点；给 --cuts 剪辑点后结果更准"
        issues += au
        log(f"  声音问题 {len(au)} 条")

    if "subs" in only and a.subs:
        if words is None:
            log("④ 字幕对齐：跳过（--asr none 或视频没有声音）")
        else:
            cues = S.load_subs(a.subs)
            log(f"④ 字幕对齐：{len(cues)} 句")
            si, fixed = S.check(cues, words, cuts, rng)
            b, nb = S.check.bias
            log(f"  识别时间整体偏差校准：{b:+.2f} 秒（按 {nb} 句估计）")
            issues += si
            fp = os.path.join(out, os.path.splitext(os.path.basename(a.subs))[0] + ".按声音修正.srt")
            S.write_srt(fixed, fp)
            log(f"  字幕问题 {len(si)} 条；按声音重卡时间的字幕：{fp}")

    log("生成报告…")
    page, cnt = R.build(issues, a.video, info, out, wav, 16000, a.start,
                        meta={"range": f"{tc(rng[0])}–{tc(rng[1])}" if (a.start or a.dur) else "",
                              "n_cuts": len(cuts) if cuts is not None else None})
    hi = sum(v["高"] for v in cnt.values())
    log("")
    for c, v in cnt.items():
        log(f"  {c}：高 {v['高']} / 中 {v['中']} / 低 {v['低']}")
    log(f"报告：{page}（用时 {time.time() - t_begin:.0f} 秒）")
    return 1 if hi else 0


if __name__ == "__main__":
    sys.exit(main())
