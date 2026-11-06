"""闪帧检测。逐帧（按视频真实帧率）比较缩小后的画面：
  - 孤立闪帧：第 i 帧和前后两帧都差很多，而前后两帧彼此很像（A-B-A）；2 帧版同理（A-B-B-A）
  - 碎镜头：两次镜头切换之间只有 ≤N 帧
  - 黑帧 / 白帧：单独 1–2 帧突然全黑或全白
连续一串短镜头（≥3 个）当作原片快切 / 蒙太奇，降为“低”。
给了剪辑点时，剪辑点附近的问题升为“高”，离剪辑点远的碎镜头降级（多半是原片自带）。
"""
import numpy as np

from .media import iter_frames, tc

GRID = 4  # 分块比较：抓局部闪烁（整帧平均会把小面积闪烁平均掉）


def _diff(a, b):
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).astype(np.float32)
    h, w = d.shape[:2]
    blk = d[: h // GRID * GRID, : w // GRID * GRID].reshape(GRID, h // GRID, GRID, w // GRID, 3).mean(axis=(1, 3, 4))
    return float(d.mean()), float(blk.max())


def _hist(f):
    q = (f // 64).astype(np.int32)
    h = np.bincount((q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]).ravel(), minlength=64).astype(np.float32)
    return h / h.sum()


def _gmean(f):
    h, w = f.shape[:2]
    return f[: h // GRID * GRID, : w // GRID * GRID].astype(np.float32).reshape(GRID, h // GRID, GRID, w // GRID, 3).mean(axis=(1, 3))


def _gd(a, b):
    """4×4 块平均色的最大变化：摇镜头时各块平均色基本不变，换镜头 / 闪帧时会有块大变"""
    return float(np.abs(a - b).mean(axis=2).max())


def measure(path, fps, start=0.0, dur=None, progress=None):
    """返回逐帧指标数组 dict：d1(与上一帧)、b1(分块最大)、s1(i+1 与 i-1)、s2(i+2 与 i-1)、luma"""
    d1, b1, s1, s2, lu, hd = [], [], [], [], [], []
    g1, gs1, gs2 = [], [], []
    ph = None
    gw = []
    win = []  # 最近 3 帧
    n = 0
    for chunk in iter_frames(path, start=start, dur=dur, fps=fps):
        for f in chunk:
            lu.append(float(f.mean()))
            g = _gmean(f)
            g1.append(_gd(g, gw[-1]) if gw else 0.0)
            gs1.append(_gd(g, gw[-2]) if len(gw) >= 2 else 0.0)
            gs2.append(_gd(g, gw[-3]) if len(gw) >= 3 else 0.0)
            gw = (gw + [g])[-3:]
            h = _hist(f)
            hd.append(float(np.abs(h - ph).sum() / 2) if ph is not None else 0.0)
            ph = h
            if win:
                a, b = _diff(f, win[-1])
                d1.append(a)
                b1.append(b)
            else:
                d1.append(0.0)
                b1.append(0.0)
            # s1[i-1] = diff(f_i, f_{i-2})；s2[i-2] = diff(f_i, f_{i-3})
            s1.append(_diff(f, win[-2])[0] if len(win) >= 2 else 0.0)
            s2.append(_diff(f, win[-3])[0] if len(win) >= 3 else 0.0)
            win = (win + [f])[-3:]
            n += 1
        if progress:
            progress(n / fps)
    N = len(d1)
    S1 = np.zeros(N, np.float32)
    S2 = np.zeros(N, np.float32)
    s1 = np.array(s1, np.float32)
    s2 = np.array(s2, np.float32)
    S1[: N - 1] = s1[1:]          # S1[i] = diff(f_{i+1}, f_{i-1})
    S2[: max(0, N - 2)] = s2[2:]  # S2[i] = diff(f_{i+2}, f_{i-1})
    G1 = np.array(g1, np.float32)
    GS1 = np.zeros(N, np.float32)
    GS2 = np.zeros(N, np.float32)
    gs1 = np.array(gs1, np.float32)
    gs2 = np.array(gs2, np.float32)
    GS1[: N - 1] = gs1[1:]
    GS2[: max(0, N - 2)] = gs2[2:]
    return {"g1": G1, "GS1": GS1, "GS2": GS2, "d1": np.array(d1, np.float32), "b1": np.array(b1, np.float32), "S1": S1, "S2": S2,
            "luma": np.array(lu, np.float32), "hd": np.array(hd, np.float32), "fps": fps, "start": start}


def detect_cuts(M, abs_thr=20.0, rel=3.0, hist_thr=0.3):
    """g1：4×4 块平均色最大变化 >28 也算（同一场戏换镜头，整体颜色分布可能很接近）"""
    """镜头切换帧号（第 i 帧是新镜头的第一帧）。三个条件同时满足：
    画面差异大、比附近帧的变化大很多（附近的重复帧不算，避免转帧率素材的 1-2-3 节奏误判）、颜色分布明显变了
    （快速摇镜头时画面差异大但颜色分布基本不变）。"""
    d, b, hd = M["d1"], M["b1"], M["hd"]
    N = len(d)
    cuts = []
    M["cand"] = cand = []   # 不排除周期性运动的候选切点，算快切密度用
    for i in range(1, N):
        lo, hi = max(1, i - 15), min(N, i + 16)
        local = np.concatenate([d[lo:i], d[i + 1:hi]])
        local = local[local > 0.8]
        med = float(np.median(local)) if len(local) else 0.0
        G = M["g1"]
        gl = np.concatenate([G[lo:i], G[i + 1:hi]])
        gl = gl[gl > 0.8]
        gmed = float(np.median(gl)) if len(gl) else 0.0
        strong = d[i] > max(abs_thr, rel * med + 6) or (G[i] > max(45, rel * gmed + 10) and d[i] > abs_thr)
        # 周期性运动（转帧率素材快速运动时每隔几帧跳一次）：附近反复出现同样大的变化、颜色分布又没大变 → 不是切换
        recurring = hd[i] < hist_thr and int((local > 0.6 * d[i]).sum()) >= 3
        if strong and b[i] > 30 and (hd[i] > hist_thr or G[i] > 28):
            cand.append(i)
        if strong and not recurring and b[i] > 30 and (hd[i] > hist_thr or G[i] > 28):
            cuts.append(i)
    return cuts


def find_flashes(M, edit_points=None, near=0.5, max_short=None):
    fps, t0 = M["fps"], M["start"]
    d, S1, S2, lu, b1 = M["d1"], M["S1"], M["S2"], M["luma"], M["b1"]
    N = len(d)
    max_short = max_short or max(2, int(round(fps * 0.21)))  # 约 6 帧（30fps）/ 5 帧（24fps）
    T = lambda i: t0 + i / fps
    E = sorted(edit_points or [])

    def near_edit(t):
        if not E:
            return None
        k = np.searchsorted(E, t)
        best = min((abs(E[j] - t) for j in (k - 1, k) if 0 <= j < len(E)), default=9e9)
        return best <= near

    issues = []
    flagged = set()
    # 1) 孤立闪帧 A-B-A / A-B-B-A
    G, GS1, GS2 = M["g1"], M["GS1"], M["GS2"]
    for i in range(1, N - 2):
        a, b = d[i], d[i + 1]
        ga, gb = G[i], G[i + 1]
        # 整帧版：前后都差很多、前后彼此很像；而且块平均色也要跳（排除摇镜头往返时“前后帧凑巧很像”）
        one = (a > 14 and b > 14 and S1[i] < 0.45 * min(a, b) and ga > 18 and gb > 18 and GS1[i] < 0.4 * min(ga, gb))
        # 局部版：只有一块区域闪
        one = one or (ga > 30 and gb > 30 and GS1[i] < 0.3 * min(ga, gb) and b1[i] > 40)
        if one:
            flagged.update([i])
            issues.append(dict(cat="闪帧", kind="孤立闪帧（1帧）", i=i, frames=1))
            continue
        gc = G[i + 2] if i + 2 < N else 0
        two = (a > 14 and d[i + 2] > 14 and d[i + 1] < 0.4 * a and S2[i] < 0.45 * min(a, d[i + 2])
               and ga > 18 and gc > 18 and G[i + 1] < 0.4 * ga and GS2[i] < 0.4 * min(ga, gc))
        if two:
            flagged.update([i, i + 1])
            issues.append(dict(cat="闪帧", kind="孤立闪帧（2帧）", i=i, frames=2))
    # 2) 黑帧 / 白帧（1–2 帧突然全黑全白）
    for i in range(1, N - 2):
        for L in (1, 2):
            if i + L >= N:
                continue
            seg = lu[i:i + L]
            nb = (lu[i - 1] + lu[i + L]) / 2
            if (seg.max() < 14 and nb > 40) or (seg.min() > 235 and nb < 200):
                if i not in flagged:
                    issues.append(dict(cat="闪帧", kind="黑帧" if seg.max() < 14 else "白帧", i=i, frames=L))
                    flagged.update(range(i, i + L))
                break
    # 3) 碎镜头
    cuts = detect_cuts(M)
    shots = list(zip(cuts, cuts[1:]))
    short = [(a, b) for a, b in shots if b - a <= max_short]
    short_set = {a for a, b in short}
    for a, b in short:
        if a in flagged:
            continue
        # 前后相邻也是短镜头 → 快切段落
        k = cuts.index(a)
        run = sum(1 for j in (k - 2, k - 1, k + 1, k + 2) if 0 <= j < len(cuts) and cuts[j] in short_set)
        dense = sum(1 for x in M.get("cand", cuts) if abs(x - a) <= fps * 0.6) >= 4   # 1.2 秒内切了 4 次以上 = 快切段落
        run = 2 if dense else run
        # 闪白 / 闪黑转场：短镜头后面几帧亮度单调回落 / 回升到新镜头（原片的转场效果，不是残帧）
        seq = lu[a:min(N, b + 3)]
        fade = len(seq) >= 3 and abs(float(seq[0]) - float(seq[-1])) > 35 and (
            all(x >= y - 2 for x, y in zip(seq, seq[1:])) or all(x <= y + 2 for x, y in zip(seq, seq[1:]))) \
            and b + 1 < N and d[b + 1] > 8 and d[b] < 0.6 * d[a]   # 进新镜头后还在继续渐变，而不是一刀硬切

        issues.append(dict(cat="闪帧", kind="闪白/闪黑转场" if fade else "碎镜头", i=a, frames=b - a,
                           montage=run >= 2, fade=bool(fade)))
    # 定级
    for it in issues:
        t = T(it["i"])
        it["t"] = round(t, 3)
        it["dur"] = round(it["frames"] / fps, 3)
        ne = near_edit(t)
        it["near_edit"] = ne
        if it["kind"].startswith("孤立闪帧") or it["kind"] in ("黑帧", "白帧"):
            it["sev"] = "高" if ne in (True, None) else "中"
            if it["kind"] == "白帧" or (lu[it["i"]] > lu[max(0, it["i"] - 1)] + 60):
                it["note"] = "画面突然变亮，可能是原片的闪光/闪白特效"
                it["sev"] = "中" if it["sev"] == "高" else "低"
        elif it.get("fade"):
            it["sev"] = "低"
            it["note"] = "亮度一帧帧渐变到新镜头，是原片的闪白/闪黑转场效果"
        else:
            if it.get("montage"):
                it["sev"] = "低"
                it["note"] = "前后都是短镜头，像原片快切/蒙太奇"
            elif ne is True:
                it["sev"] = "高"
            elif ne is False:
                it["sev"] = "低"
                it["note"] = "离剪辑点较远，多半是原片自带的快切"
            else:
                it["sev"] = "中"
        it["msg"] = f"{it['kind']}：{it['frames']} 帧（{it['dur']:.2f}秒）" + ("，在剪辑点附近" if ne else "")
    cut_times = [round(T(i), 3) for i in cuts]
    return issues, cut_times
