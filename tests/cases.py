"""压力测试用例目录。每个用例:
  id, group(故障-闪帧/故障-声音/陷阱/格式/字幕/真实片), video(由 recipe 生成或现成文件),
  cuts(成片时间轴剪辑点 或 None), args(额外命令行参数),
  expect: 必须报出的问题 [{cat, kinds, t, tol, min_sev}]
  forbid_high: True 表示除 expect 以外不许出现“高”级问题
"""
import os

from synth import CACHE, _h, render

LONG_ENC = None


def _fr(n, fps):
    return n / fps


def flash_recipe(P, src, key, fps, size, extra_frames=1, kind="cut", color=None, seg=5.0):
    """A 连续镜头里插 extra_frames 帧（别的镜头 / 黑白帧），插完接回 A。返回 (recipe, 插入时间)"""
    nA, nR = int(round(seg * fps)), int(round(0.8 * seg * fps))
    tA = P.stable(src, 2 * seg, key)
    tB = P.far(src, tA, key)
    ins = {"color": color, "n": extra_frames} if color else {"src": src, "t": tB, "n": extra_frames}
    v = [{"src": src, "t": tA, "n": nA}, ins, {"src": src, "t": tA + (nA + extra_frames) / fps, "n": nR}]
    total = (nA + extra_frames + nR) / fps
    return {"fps": fps, "size": size, "v": v, "a": [{"src": src, "t": tA, "d": total}]}, nA / fps


def build(P, sources, quick=True):
    C = []
    out = lambda rid, rec, ext=".mp4": render(rec, os.path.join(CACHE, "cases", f"{rid}_{_h(rec)}{ext}"))

    for si, src in enumerate(sources):
        fps0 = P.info[src]["fps"]
        fps0 = 30.0 if abs(fps0 - 30) < 0.5 else (24.0 if abs(fps0 - 24) < 0.5 else 25.0)
        W, H = (960, 540) if P.info[src]["width"] >= P.info[src]["height"] else (540, 960)
        tag = f"s{si}"
        # 镜头长度：长镜头素材按 5 秒一段；镜头普遍 ≤6 秒的素材（如 AI 生成的 5 秒片段拼成的短剧）按 2.4 秒一段
        seg = 5.0 if P.shots(src) is None or max(b - a for a, b in P.shots(src)) >= 10.5 else 2.4
        reps = 2 if quick else 4
        for r in range(reps):
            # ---------- 故障：闪帧 ----------
            for nfr in (1, 2):
                rec, t = flash_recipe(P, src, f"{tag}f{nfr}r{r}", fps0, (W, H), nfr, seg=seg)
                cuts = None if r % 2 else [t, t + nfr / fps0]
                C.append(dict(id=f"{tag}_插{nfr}帧_r{r}{'_无剪辑点' if cuts is None else ''}", group="故障-闪帧", video=out(f"{tag}ins{nfr}r{r}", rec),
                              cuts=cuts, fps=fps0,
                              expect=[dict(cat="闪帧", kinds=["孤立闪帧（1帧）", "孤立闪帧（2帧）", "碎镜头"], t=t, tol=0.25)]))
            for k in ((1, 3, 5) if quick else (1, 2, 3, 4, 5)):
                nA = int(round(seg * fps0))
                tA = P.stable(src, seg + 1, f"{tag}sa{k}r{r}")
                tB = P.far(src, tA, f"{tag}sb{k}r{r}")
                tC = P.far(src, tB + 300, f"{tag}sc{k}r{r}")
                rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": nA}, {"src": src, "t": tB, "n": k},
                                                         {"src": src, "t": tC, "n": int(0.8 * seg * fps0)}]}
                t = nA / fps0
                C.append(dict(id=f"{tag}_剪辑点碎镜头{k}帧_r{r}", group="故障-闪帧", video=out(f"{tag}short{k}r{r}", rec),
                              cuts=[t, t + k / fps0], fps=fps0,
                              expect=[dict(cat="闪帧", kinds=["碎镜头", "孤立闪帧（1帧）", "孤立闪帧（2帧）"], t=t, tol=0.25)]))
            for col in ("black", "white"):
                rec, t = flash_recipe(P, src, f"{tag}{col}r{r}", fps0, (W, H), 1, color=col, seg=seg)
                C.append(dict(id=f"{tag}_插1帧{'黑' if col == 'black' else '白'}帧_r{r}", group="故障-闪帧", video=out(f"{tag}{col}r{r}", rec),
                              cuts=None, fps=fps0,
                              expect=[dict(cat="闪帧", kinds=["黑帧", "白帧", "孤立闪帧（1帧）"], t=t, tol=0.25,
                                           min_sev="低" if col == "white" else "中")]))
            # 局部闪烁：只有画面左上 1/4 闪 1 帧
            tA = P.stable(src, 2 * seg, f"{tag}loc{r}")
            tB = P.far(src, tA, f"{tag}locb{r}")
            N = int(round(seg * fps0))
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": int(2 * seg * fps0)}],
                   "extra_inputs": [{"src": src, "t": tB}],
                   "post_v": f"[2:v]fps={fps0},scale={W // 2}:{H // 2}:force_original_aspect_ratio=increase,crop={W // 2}:{H // 2},setsar=1,trim=end_frame=1,loop=loop={int(12 * fps0)}:size=1:start=0,setpts=N/({fps0}*TB)[ov];"
                             f"{{IN}}[ov]overlay=0:0:enable='eq(n\\,{N})':eof_action=pass{{OUT}}"}
            C.append(dict(id=f"{tag}_局部闪1帧_r{r}", group="故障-闪帧", video=out(f"{tag}local{r}", rec), cuts=None, fps=fps0,
                          expect=[dict(cat="闪帧", kinds=["孤立闪帧（1帧）"], t=N / fps0, tol=0.25)]))

            # ---------- 故障：声音 ----------
            tA = P.loud(src, 10, f"{tag}clk{r}", [5.0])
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": int(10 * fps0)}],
                   "post_a": "aevalsrc='if(between(t\\,5.0\\,5.0003)\\,0.9\\,0)':s=44100:c=stereo:d=10[imp];{IN}[imp]amix=inputs=2:normalize=0:duration=first{OUT}"}
            C.append(dict(id=f"{tag}_咔哒声_r{r}", group="故障-声音", video=out(f"{tag}click{r}", rec), cuts=[5.0], fps=fps0,
                          args=["--asr", "none"], expect=[dict(cat="声音", kinds=["爆音/咔哒"], t=5.0, tol=0.03)]))
            tA = P.loud(src, 10, f"{tag}trc{r}", [4.95])
            n5 = int(round(5 * fps0))
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": n5}, {"src": src, "t": tA + 5, "n": n5}],
                   "a": [{"src": src, "t": tA, "d": n5 / fps0}, {"src": None, "d": n5 / fps0}]}
            C.append(dict(id=f"{tag}_尾音硬截断_r{r}", group="故障-声音", video=out(f"{tag}trunc{r}", rec), cuts=[n5 / fps0], fps=fps0,
                          args=["--asr", "none"], expect=[dict(cat="声音", kinds=["声音被硬截断"], t=n5 / fps0, tol=0.03)]))
            tA = P.loud(src, 10, f"{tag}lvl{r}", [4.5, 5.0, 5.5])
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": n5}, {"src": src, "t": tA + 5, "n": n5}],
                   "a": [{"src": src, "t": tA, "d": n5 / fps0, "af": "volume=-20dB"}, {"src": src, "t": tA + 5, "d": n5 / fps0}]}
            C.append(dict(id=f"{tag}_音量跳变20dB_r{r}", group="故障-声音", video=out(f"{tag}level{r}", rec), cuts=[n5 / fps0], fps=fps0,
                          args=["--asr", "none"], expect=[dict(cat="声音", kinds=["音量跳变"], t=n5 / fps0, tol=0.03, min_sev="低")]))

            # ---------- 陷阱：正常剪辑，不许报“高” ----------
            tA = P.stable(src, 0.8 * seg, f"{tag}mont{r}")
            vs, cuts, t = [], [], 0.0
            for j in range(10):
                n = 3 + (j * 7 + r) % 4
                vs.append({"src": src, "t": P.far(src, tA + j * 13, f"{tag}m{r}{j}"), "n": n})
                t += n / fps0
                cuts.append(round(t, 4))
            vs.append({"src": src, "t": tA, "n": int(0.6 * seg * fps0)})
            tot = sum(x["n"] for x in vs) / fps0
            C.append(dict(id=f"{tag}_陷阱_快切蒙太奇_r{r}", group="陷阱", video=out(f"{tag}montage{r}", {"fps": fps0, "size": (W, H), "v": vs,
                                                                                                    "a": [{"src": src, "t": tA, "d": tot}]}),
                          cuts=cuts, fps=fps0, args=["--asr", "none"], forbid_high=True, expect=[]))
            tA = P.stable(src, 2 * seg, f"{tag}fl{r}")
            N = int(seg * fps0)
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": int(2 * seg * fps0)}],
                   "post_v": f"{{IN}}eq=brightness=0.45:enable='between(n\\,{N}\\,{N + 1})'{{OUT}}"}
            C.append(dict(id=f"{tag}_陷阱_闪光灯2帧_r{r}", group="陷阱", video=out(f"{tag}flashlight{r}", rec), cuts=None, fps=fps0,
                          args=["--asr", "none"], forbid_high=True, expect=[]))
            ns = int(round(seg * fps0))
            tA = P.stable(src, seg + 1, f"{tag}fw{r}")
            tB = P.stable(src, seg + 1, f"{tag}fwb{r}", avoid=(tA,))
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": ns},
                                                     {"src": src, "t": tB, "n": ns, "vf": "fade=t=in:s=0:n=4:color=white"}],
                   "a": [{"src": src, "t": tA, "d": 2 * ns / fps0}]}
            C.append(dict(id=f"{tag}_陷阱_闪白转场_r{r}", group="陷阱", video=out(f"{tag}fadewhite{r}", rec), cuts=[ns / fps0], fps=fps0,
                          args=["--asr", "none"], forbid_high=True, expect=[]))
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": ns, "vf": f"fade=t=out:s={ns - 12}:n=12"},
                                                     {"src": src, "t": tB, "n": ns, "vf": "fade=t=in:s=0:n=12"}],
                   "a": [{"src": src, "t": tA, "d": 2 * ns / fps0}]}
            C.append(dict(id=f"{tag}_陷阱_黑场过渡_r{r}", group="陷阱", video=out(f"{tag}dip{r}", rec), cuts=[ns / fps0], fps=fps0,
                          args=["--asr", "none"], forbid_high=True, expect=[]))
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": int(min(1.6 * seg, seg + 0.9) * fps0),
                                                      "pre": "crop=iw*0.55:ih*0.55:x='(iw-ow)*(0.5+0.5*sin(t*4.5))':y='(ih-oh)/2'"}]}
            C.append(dict(id=f"{tag}_陷阱_快速摇镜头_r{r}", group="陷阱", video=out(f"{tag}pan{r}", rec), cuts=None, fps=fps0,
                          args=["--asr", "none"], forbid_high=True, expect=[]))
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": tA, "n": ns}, {"src": src, "t": tB, "n": ns}],
                   "a": [{"src": src, "t": tA, "d": 2 * ns / fps0}]}
            C.append(dict(id=f"{tag}_陷阱_JL剪_r{r}", group="陷阱", video=out(f"{tag}jcut{r}", rec), cuts=[ns / fps0], fps=fps0,
                          args=["--asr", "none"], forbid_high=True, expect=[]))
            n4 = int(0.8 * seg * fps0)
            tC = P.stable(src, seg, f"{tag}clc{r}", avoid=(tA, tB))
            rec = {"fps": fps0, "size": (W, H), "v": [{"src": src, "t": x, "n": n4} for x in (tA, tB, tC)],
                   "a": [{"src": src, "t": x, "d": n4 / fps0, "af": f"afade=t=in:d=0.02,afade=t=out:st={n4 / fps0 - 0.02:.3f}:d=0.02"} for x in (tA, tB, tC)]}
            C.append(dict(id=f"{tag}_陷阱_干净硬切_r{r}", group="陷阱", video=out(f"{tag}clean{r}", rec), cuts=[n4 / fps0, 2 * n4 / fps0], fps=fps0,
                          args=["--asr", "none"], forbid_high=True, expect=[]))
    return C


def build_matrix(P, src):
    """格式矩阵：同一个“插 1 帧”故障换各种格式 / 边界条件，要求不崩溃、照样抓到、时间码误差 ≤1 帧"""
    C = []
    base_rec, _ = flash_recipe(P, src, "mx", 30.0, (960, 540), 1)
    tA = base_rec["v"][0]["t"]

    def case(cid, fps=30.0, size=(960, 540), enc=None, ext=".mp4", no_audio=False, args=None, cuts=None, post_v=None,
             dur=None, expect_t=None, path_dir=None):
        rec, t = flash_recipe(P, src, "mx", fps, size, 1)
        if dur:  # 极短片：插入点 0.7 秒
            n1 = int(round(0.7 * fps))
            rec["v"][0]["n"] = n1
            rec["v"][2]["t"] = tA + (n1 + 1) / fps
            rec["v"][2]["n"] = int(round((dur - 0.7) * fps)) - 1
            rec["a"][0]["d"] = dur
            t = n1 / fps
        if enc:
            rec["enc"] = enc
        if no_audio:
            rec["no_audio"] = True
        if post_v:
            rec["post_v"] = post_v
        d = os.path.join(CACHE, "cases", path_dir) if path_dir else os.path.join(CACHE, "cases")
        v = render(rec, os.path.join(d, f"mx_{cid}_{_h(rec)}{ext}"))
        C.append(dict(id=f"格式_{cid}", group="格式", video=v, cuts=cuts, fps=fps, args=(args or []) + ["--asr", "none"],
                      expect=[dict(cat="闪帧", kinds=["孤立闪帧（1帧）", "碎镜头"], t=expect_t if expect_t is not None else t, tol=0.25)]))

    for f in (23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 60.0):
        case(f"{f:g}fps", fps=f)
    case("变帧率VFR", enc=["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-fps_mode", "vfr", "-c:a", "aac"],
         post_v="{IN}setpts='N/(30*TB)+if(eq(mod(N\\,4)\\,1)\\,0.008/TB\\,0)'{OUT}")
    case("竖屏720x1280", size=(720, 1280))
    case("4K", size=(3840, 2160), enc=["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac"])
    case("HEVC", enc=["-c:v", "libx265", "-preset", "ultrafast", "-crf", "24", "-tag:v", "hvc1", "-c:a", "aac", "-x265-params", "log-level=error"])
    case("ProRes", enc=["-c:v", "prores_ks", "-profile:v", "0", "-c:a", "pcm_s16le"], ext=".mov")
    case("MKV", ext=".mkv")
    case("无音轨", no_audio=True)
    case("单声道", enc=["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1"])
    case("5.1声道", enc=["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "6"])
    case("极短1.6秒", dur=1.6)
    case("中文空格路径", path_dir="测试 目录 （中文）")
    case("只查一段", args=["--start", "4.5", "--dur", "1.5"])
    case("剪辑点超出时长", cuts=[5.0, 5.033, 99999.0, -3.0])
    return C
