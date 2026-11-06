"""字幕与声音对齐检查。把识别出的每个字（带时间）和字幕逐句做模糊匹配：
  - 字幕早出/晚出、提前消失/拖太久
  - 字幕文字和读音对不上（错字、漏字、配音改了字幕没改）
  - 字幕处没听到人声 / 有人声但没字幕
  - 字幕跨剪辑点（给了剪辑点时）
并输出一份按声音重新卡时间的字幕（只改匹配可靠的句子）。
"""
import re
from difflib import SequenceMatcher

try:
    from opencc import OpenCC
    _t2s = OpenCC("t2s").convert
except Exception:
    _t2s = lambda s: s

_NUM = str.maketrans({"0": "零", "1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七",
                      "8": "八", "9": "九", "两": "二", "〇": "零"})
_PUNCT = re.compile(r"[\s\W_]+", re.U)


_D = "零一二三四五六七八九"


def _cn(n):
    """整数读成中文（1000→一千，2025→二千零二十五），够字幕比对用"""
    if n == 0:
        return "零"
    out, units = "", [(100000000, "亿"), (10000, "万"), (1000, "千"), (100, "百"), (10, "十")]
    zero = False
    for v, u in units:
        q, n = divmod(n, v)
        if q:
            out += (("零" if zero and out else "") + (_cn(q) if q >= 10 else _D[q]) + u)
            zero = False
        elif out:
            zero = True
    if n:
        out += ("零" if zero and out else "") + _D[n]
    return out[1:] if out.startswith("一十") else out


def _nums(s):
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)
    s = re.sub(r"\d+\.\d+", lambda m: m[0].replace(".", "点"), s)
    return re.sub(r"\d+", lambda m: _cn(int(m[0])) if len(m[0]) <= 9 and not m[0].startswith("0") else m[0], s)


def norm(s):
    s = _t2s(_nums(s)).lower().translate(_NUM)
    return _PUNCT.sub("", s)


try:
    from pypinyin import lazy_pinyin
except Exception:
    lazy_pinyin = None
_FUZ = [("zh", "z"), ("ch", "c"), ("sh", "s"), ("ing", "in"), ("eng", "en"), ("ang", "an")]


def tok(s):
    """逐字转成去声调的拼音（再合并平翘舌、前后鼻音），让同音/近音错字也能对上"""
    if not lazy_pinyin:
        return list(s)
    out = []
    for ch in s:
        if "\u4e00" <= ch <= "\u9fff":
            py = lazy_pinyin(ch)[0]
            for a, b in _FUZ:
                py = py.replace(a, b)
            out.append(py)
        else:
            out.append(ch)
    return out


INTERJ = set("啊哎嗯哦呀呃唉哈嘿喂诶欸噢哼呵嘛呢吧")


def _ts(s):
    s = s.strip().replace(",", ".")
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(x)


def load_subs(path):
    txt = open(path, encoding="utf-8-sig", errors="replace").read()
    cues = []
    if path.lower().endswith((".ass", ".ssa")):
        fmt = None
        for line in txt.splitlines():
            if line.startswith("Format:") and fmt is None and "Start" in line:
                fmt = [x.strip() for x in line[7:].split(",")]
            if line.startswith("Dialogue:"):
                f = fmt or ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV", "Effect", "Text"]
                p = line[9:].split(",", len(f) - 1)
                d = dict(zip(f, p))
                text = re.sub(r"\{[^}]*\}", "", d.get("Text", "")).replace("\\N", " ").replace("\\n", " ")
                cues.append({"s": _ts(d["Start"]), "e": _ts(d["End"]), "text": text.strip(), "style": d.get("Style", "")})
    else:
        for b in re.split(r"\n\s*\n", txt.strip()):
            L = b.strip().splitlines()
            k = next((i for i, x in enumerate(L) if "-->" in x), None)
            if k is None:
                continue
            a, c = L[k].split("-->")
            cues.append({"s": _ts(a), "e": _ts(c.split()[0]), "text": " ".join(L[k + 1:]).strip()})
    cues.sort(key=lambda c: c["s"])
    for i, c in enumerate(cues, 1):
        c["n"] = i
    return cues


def _chars(words):
    """识别结果拆成单字流 [(字, 开始, 结束)]"""
    out = []
    for w in words:
        t = norm(w["w"])
        if not t:
            continue
        step = (w["e"] - w["s"]) / len(t)
        for k, (ch, py) in enumerate(zip(t, tok(t))):
            out.append((ch, w["s"] + k * step, w["s"] + (k + 1) * step, py))
    return out


def _match(cue_txt, C, lo, hi):
    idx = [k for k, c in enumerate(C) if lo <= c[1] <= hi]
    if not idx or not cue_txt:
        return 0.0, [], idx
    win = [C[k][3] for k in idx]
    sm = SequenceMatcher(None, tok(cue_txt), win, autojunk=False)
    blocks = []
    for a, b, size in sm.get_matching_blocks():
        # 一个匹配块内部，相邻两个字在时间上隔太久（>0.8 秒）就拆开：识别字流里挨着的两个字可能分属前后两句
        st = 0
        for j in range(1, size):
            if C[idx[b + j]][1] - C[idx[b + j - 1]][2] > 0.8:
                blocks.append((a + st, b + st, j - st))
                st = j
        if size - st > 0:
            blocks.append((a + st, b + st, size - st))
    if not blocks:
        return 0.0, [], idx
    # 只保留以最长块为中心、前后错位一致的块（去掉窗口里别处凑巧相同的字）
    k = max(range(len(blocks)), key=lambda i: blocks[i][2])
    keep = [blocks[k]]
    for direction in (-1, 1):
        ref = blocks[k]
        i = k + direction
        while 0 <= i < len(blocks):
            a, b, size = blocks[i]
            ra, rb, rs = ref
            if direction < 0:
                ga, gb = ra - (a + size), rb - (b + size)
            else:
                ga, gb = a - (ra + rs), b - (rb + rs)
            # 时间上也要接得上：两段匹配之间隔的时间不能远超中间差的字数（防止和上一句末尾的同音字拼到一起）
            if direction < 0:
                tgap = C[idx[ref[1]]][1] - C[idx[b + size - 1]][2]
            else:
                tgap = C[idx[b]][1] - C[idx[rb + rs - 1]][2]
            if abs(ga - gb) <= 3 and gb <= ga + 3 and tgap <= 0.6 + 0.35 * max(ga, 0):
                keep.append(blocks[i])
                ref = blocks[i]
            i += direction
    keep.sort()
    pairs = []
    for a, b, size in keep:
        for j in range(size):
            pairs.append((a + j, idx[b + j]))
    return len(pairs) / len(cue_txt), pairs, idx


def calibrate(cues, C, t_range=None):
    """识别出的字时间整体会比真实发声晚/早一点（faster-whisper 一般晚 0.1–0.2 秒）。
    用匹配很好的长句估一个整体偏差，判断早出晚出时先扣掉，避免整片误报。"""
    import statistics
    ds = []
    for c in cues:
        if t_range and not (t_range[0] <= c["s"] <= t_range[1]):
            continue
        t = norm(c["text"])
        if len(t) < 5:
            continue
        r, p, _ = _match(t, C, c["s"] - 2.5, c["e"] + 2.5)
        if r >= 0.8 and p[0][0] == 0:
            ds.append(c["s"] - C[p[0][1]][1])
    if len(ds) < 8:
        return 0.0, len(ds)
    b = statistics.median(ds)
    return (b if abs(b) <= 0.4 else 0.0), len(ds)


def check(cues, words, cuts=None, t_range=None, start_tol=0.35, end_tol=0.5):
    C = _chars(words)
    issues, fixed = [], []
    bias, nb = calibrate(cues, C, t_range)
    check.bias = (bias, nb)
    covered = [False] * len(C)
    for c in cues:
        if t_range and not (t_range[0] <= c["s"] <= t_range[1]):
            continue
        txt = norm(c["text"])
        if not txt:
            continue
        if all(ch in INTERJ for ch in txt):   # 纯语气词：识别常漏，不判
            fixed.append(dict(c))
            continue
        short = len(txt) <= 3
        pad = 1.0 if short else 2.5
        ratio, pairs, idx = _match(txt, C, c["s"] - pad, c["e"] + pad)
        if ratio < 0.5 and len(txt) >= 5:
            r2, p2, i2 = _match(txt, C, c["s"] - 8, c["e"] + 8)
            if r2 >= 0.7 and r2 > ratio + 0.2:
                ratio, pairs, idx = r2, p2, i2
        base = dict(cat="字幕", t=round(c["s"], 3), n=c["n"], text=c["text"])
        if not pairs or ratio < 0.35:
            heard = "".join(C[k][0] for k in idx if c["s"] - 0.2 <= C[k][1] <= c["e"] + 0.2)
            if not heard:
                issues.append(dict(base, kind="字幕处没听到人声", sev="低" if short else "中",
                                   msg=f"第{c['n']}句“{c['text']}”出现时没识别到人声（可能是音乐盖住、或这句没配音）"))
            else:
                issues.append(dict(base, kind="字幕和声音对不上", sev="高" if len(txt) >= 4 and len(heard) >= 3 else "中",
                                   msg=f"第{c['n']}句字幕“{c['text']}”，听到的是“{heard}”"))
            fixed.append(dict(c))
            continue
        for _, k in pairs:
            covered[k] = True
        # 用匹配到的首尾字推算这句真正的起止
        (ci0, k0), (ci1, k1) = pairs[0], pairs[-1]
        span_chars = max(1, ci1 - ci0)
        cps = (C[k1][2] - C[k0][1]) / span_chars if ci1 > ci0 else 0.2
        cps = min(max(cps, 0.08), 0.5)
        real_s = C[k0][1] - ci0 * cps
        real_e = C[k1][2] + (len(txt) - 1 - ci1) * cps
        ds, de = c["s"] - real_s - bias, c["e"] - real_e - bias
        if ratio < 0.7 and not short:
            heard = "".join(C[k][0] for k in range(min(k0, idx[0]) if idx else k0, k1 + 1)
                            if real_s - 0.1 <= C[k][1] <= real_e + 0.1)
            issues.append(dict(base, kind="字幕文字和读音不一致", sev="中",
                               msg=f"第{c['n']}句字幕“{c['text']}”，听到的像“{heard}”（匹配 {ratio * 100:.0f}%）"))
        judge_s = ci0 <= 1
        judge_e = ci1 >= len(txt) - 2
        if not judge_s:
            ds = 0.0
        if not judge_e:
            de = 0.0
        if ds > start_tol:
            issues.append(dict(base, kind="字幕晚出", sev="高" if ds > 0.8 and not short else "中",
                               msg=f"第{c['n']}句“{c['text']}”比声音晚 {ds:.2f} 秒出现"))
        elif ds < -start_tol:
            issues.append(dict(base, kind="字幕早出", sev="高" if ds < -0.8 and not short else "中",
                               msg=f"第{c['n']}句“{c['text']}”比声音早 {-ds:.2f} 秒出现"))
        if de < -end_tol:
            issues.append(dict(base, kind="字幕提前消失", sev="中",
                               msg=f"第{c['n']}句“{c['text']}”在话还没说完前 {-de:.2f} 秒就消失了"))
        elif de > end_tol + 0.5:
            issues.append(dict(base, kind="字幕拖太久", sev="低",
                               msg=f"第{c['n']}句“{c['text']}”话说完后还挂了 {de:.2f} 秒"))
        if cuts:
            for x in cuts:
                if c["s"] + 0.25 < x < c["e"] - 0.25 and not (real_s < x < real_e):
                    issues.append(dict(base, kind="字幕跨剪辑点", sev="低",
                                       msg=f"第{c['n']}句“{c['text']}”跨过了 {x:.2f} 秒的剪辑点，声音却不跨"))
                    break
        f = dict(c)
        if ratio >= 0.6:
            f["s"], f["e"] = max(0.0, real_s + bias), real_e + bias + 0.1
        fixed.append(f)
    # 有人声没字幕：连续 ≥4 个字没被任何字幕匹配到
    run = []
    for k, ch in enumerate(C):
        if t_range and not (t_range[0] <= ch[1] <= t_range[1]):
            continue
        if not covered[k]:
            run.append(k)
            continue
        if len(run) >= 4:
            _report_uncovered(run, C, cues, issues)
        run = []
    if len(run) >= 4:
        _report_uncovered(run, C, cues, issues)
    # 修正版：保证不重叠、最短 0.4 秒
    fixed.sort(key=lambda c: c["s"])
    for a, b in zip(fixed, fixed[1:]):
        if a["e"] > b["s"] - 0.02:
            a["e"] = max(a["s"] + 0.4, b["s"] - 0.02)
    return issues, fixed


def _report_uncovered(run, C, cues, issues):
    s, e = C[run[0]][1], C[run[-1]][2]
    # 这段时间里本来就有字幕 → 算“文字对不上”，已在上面报过
    if any(c["s"] - 0.2 <= s and e <= c["e"] + 0.2 for c in cues):
        return
    txt = "".join(C[k][0] for k in run)
    issues.append(dict(cat="字幕", kind="有人声没字幕", t=round(s, 3), sev="中",
                       msg=f"{s:.2f}–{e:.2f} 秒听到“{txt}”，但没有字幕"))


def write_srt(cues, path):
    def f(v):
        v = max(0.0, v)
        return f"{int(v // 3600):02d}:{int(v % 3600 // 60):02d}:{int(v % 60):02d},{int(round(v * 1000)) % 1000:03d}"
    with open(path, "w", encoding="utf-8") as fh:
        for i, c in enumerate(cues, 1):
            fh.write(f"{i}\n{f(c['s'])} --> {f(c['e'])}\n{c['text']}\n\n")
