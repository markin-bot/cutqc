"""声音切点检查。对每个剪辑点 t：
  - 爆音/咔哒：t 附近几毫秒内采样突跳，远大于周围水平
  - 声音被硬截断：t 前一刻还很响，t 之后 20ms 内骤降到几乎无声（尾音被切）
  - 切在字中间：有逐字识别结果时，某个字横跨 t（前后各 >40ms）
  - 音量跳变：切点两侧都有声音，但响度差 >9dB（原片声音和解说/配音接得不平）
  - 空白过长：切点处连续静音 >1.2 秒
"""
import numpy as np

SR = 16000


def _db(x):
    if len(x) == 0:
        return -120.0
    return float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9))


def _level(x):
    """一段声音的稳定响度：50ms 一格取第 70 百分位，避免一个字的起伏影响判断"""
    hop = int(0.05 * SR)
    if len(x) < hop:
        return _db(x)
    v = [_db(x[i:i + hop]) for i in range(0, len(x) - hop + 1, hop)]
    return float(np.percentile(v, 70))


_HW = np.hanning(320)


def _spec(x, k):
    seg = x[k:k + 320]
    return None if len(seg) < 320 else np.log(np.abs(np.fft.rfft(seg * _HW)) + 1e-4)


def continuity(wav, t, t0=0.0):
    """切点处频谱突变程度：≈1 说明和附近一样平滑（声音连着 / 有淡入淡出），>2 说明声音在这里硬接"""
    k = int((t - t0) * SR)
    a, b = _spec(wav, k - 320), _spec(wav, k)
    if a is None or b is None:
        return 1.0
    d = np.mean(np.abs(a - b))
    loc = []
    for o in (-0.1, -0.07, -0.05, 0.03, 0.05, 0.08):
        kk = k + int(o * SR)
        p, q = _spec(wav, kk - 320), _spec(wav, kk)
        if p is not None and q is not None:
            loc.append(np.mean(np.abs(p - q)))
    return float(d / (np.median(loc) + 1e-6)) if loc else 1.0


def _click(path, t):
    """原始采样率下看切点 ±4ms 内有没有尖峰（降到 16k 采样会把极短的咔哒声抹平）"""
    from .media import load_audio
    x = load_audio(path, 48000, max(0.0, t - 0.25), 0.5)
    if len(x) < 2000:
        return 0.0, 0.0
    hp = np.abs(np.diff(x, n=2))
    k = int(min(t, 0.25) * 48000)
    w = int(0.025 * 48000)   # 读音频按时间定位有约一个音频帧（≈23ms）的误差
    j = max(0, k - w) + int(np.argmax(hp[max(0, k - w):k + w]))
    peak = float(hp[j])
    # 尖锐度：峰值是它前后 0.5–8ms 的多少倍。咔哒声 ≈ 一两个采样的尖刺（>5 倍），爆破音 / 鼓点是一小段（<3.5 倍）
    loc = np.concatenate([hp[max(0, j - 384):max(0, j - 24)], hp[j + 24:j + 384]])
    sharp = peak / (float(np.percentile(loc, 95)) + 1e-9) if len(loc) else 0.0
    return peak, sharp


def check_points(wav, points, words=None, t0=0.0, level_jump=10.0, gap=1.2, path=None):
    issues = []
    n = len(wav)
    dx = np.abs(np.diff(wav, prepend=wav[:1]))
    # 时间不可靠的词不参与判断：太长（>0.8 秒一个词）或置信度低
    W = sorted([w for w in (words or []) if w["e"] - w["s"] <= 0.8 and w.get("p", 1) >= 0.35], key=lambda w: w["s"])
    starts = np.array([w["s"] for w in W]) if W else np.array([])

    def seg(a, b):
        a, b = int(max(0, (a - t0) * SR)), int(min(n, (b - t0) * SR))
        return wav[a:b] if b > a else wav[:0]

    for t in points:
        if t - t0 < 0.05 or (t - t0) * SR > n - 800:
            continue
        k = int((t - t0) * SR)
        # 爆音
        w6 = int(0.006 * SR)
        if path:
            peak, sharp = _click(path, t)
            if peak > 0.04 and sharp >= 5.0:
                issues.append(dict(cat="声音", kind="爆音/咔哒", t=t, sev="高",
                                   msg=f"切点附近有一个尖刺（是周围的 {sharp:.0f} 倍），会听到“咔”一声"))
            peak = 0.0
            base, thr = 1.0, 1.0
        else:
            peak = float(dx[max(0, k - w6):k + w6].max())
            ctx = np.concatenate([dx[max(0, k - 8000):max(0, k - 160)], dx[k + 160:k + 8000]])
            base = float(np.percentile(ctx, 99.5)) if len(ctx) else 0.0
            thr = 3.0
        if peak > 0.04 and peak > thr * base:
            issues.append(dict(cat="声音", kind="爆音/咔哒", t=t, sev="高",
                               msg=f"切点处采样突跳 {peak:.3f}，是周围的 {peak / max(base, 1e-6):.1f} 倍，会听到“咔”一声"))
        pre20, post20 = _db(seg(t - 0.025, t - 0.003)), _db(seg(t + 0.003, t + 0.025))
        pre, post = _level(seg(t - 0.8, t - 0.03)), _level(seg(t + 0.03, t + 0.8))
        # 尾音硬截断
        pre60 = max(_db(seg(t - 0.065, t - 0.045)), _db(seg(t - 0.045, t - 0.025)), pre20)
        post_b = _db(seg(t + 0.025, t + 0.05))   # 编码会把切点后 ~20ms 抹开，看 25–50ms
        if pre60 > -34 and post_b < max(pre20, pre60) - 25 and post_b < -45:
            issues.append(dict(cat="声音", kind="声音被硬截断", t=t, sev="中",
                               msg=f"切点前还有 {max(pre20, pre60):.0f}dB 的声音，切点后骤降到 {post_b:.0f}dB，尾音可能被切掉"))
        # 切在字中间
        if len(W):
            j = int(np.searchsorted(starts, t))
            for w in W[max(0, j - 2):j + 1]:
                # 识别的字时间有 ±0.1 秒误差：再用波形确认切点两侧 15ms 都还有人声
                if w["s"] < t - 0.06 and w["e"] > t + 0.06 and pre20 > -32 and post20 > -32 \
                        and _db(seg(t - 0.015, t)) > -32 and _db(seg(t, t + 0.015)) > -32:
                    strong = w["s"] < t - 0.1 and w["e"] > t + 0.1
                    cont = continuity(wav, t, t0)
                    it = dict(cat="声音", kind="切在字中间", t=t, sev="高" if strong and cont >= 1.3 else "中",
                              msg=f"“{w['w'].strip()}”（{w['s']:.2f}–{w['e']:.2f}秒）被切点劈开，会吞字或半个字")
                    if cont < 1.3:
                        it["note"] = "切点处声音频谱是连续的：可能是 J/L 剪（声音没断）或做了淡入淡出，请听一下确认"
                    issues.append(it)
                    break
        # 音量跳变
        talk_both = len(W) and any(t - 0.6 < w["e"] <= t + 0.05 for w in W) and any(t - 0.05 <= w["s"] < t + 0.6 for w in W)
        if max(pre, post) > -35 and min(pre, post) > -58 and abs(pre - post) > level_jump:
            issues.append(dict(cat="声音", kind="音量跳变", t=t, sev="中" if talk_both else "低",
                               note="" if talk_both else "切点两边不都在说话，可能是正常的场景声音变化",
                               msg=f"切点前 {pre:.0f}dB、切点后 {post:.0f}dB，相差 {abs(pre - post):.0f}dB，衔接会忽大忽小"))
        # 空白过长
        if pre < -45 and post < -45:
            a = b = t
            while a - t0 > 0.1 and _db(seg(a - 0.1, a)) < -45 and t - a < 5:
                a -= 0.1
            while _db(seg(b, b + 0.1)) < -45 and b - t < 5 and (b - t0) * SR < n:
                b += 0.1
            if b - a > gap:
                issues.append(dict(cat="声音", kind="空白过长", t=t, sev="低",
                                   msg=f"切点处连续 {b - a:.1f} 秒几乎没声音"))
    for it in issues:
        it["t"] = round(it["t"], 3)
    return issues
