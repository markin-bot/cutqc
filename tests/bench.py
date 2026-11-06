"""cutqc 压力测试：生成埋好问题的测试片 + 易误报的干净片 + 格式矩阵 + 字幕用例 + 真实片标注，跑完出成绩单。

用法:
  .venv/bin/python tests/bench.py            # 快速版：闪帧 + 声音 + 格式（不做语音识别，约十几分钟）
  .venv/bin/python tests/bench.py --full     # 完整版：再加字幕用例、真实片标注、长片时间漂移
  .venv/bin/python tests/bench.py --only 陷阱  # 只跑某一组（按组名或用例 id 包含的文字筛）
素材在 tests/bench_sources.json 里配置；结果在 tests/results/<时间>/ 下，并和上一次比较。
"""
import argparse
import contextlib
import csv
import io
import json
import os
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def cfg_path(name):
    """优先用本机配置 xxx.local.json（不进仓库），没有就用示例 xxx.json"""
    loc = os.path.join(HERE, name.replace(".json", ".local.json"))
    return loc if os.path.exists(loc) else os.path.join(HERE, name)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from synth import CACHE, Picker, _h, render  # noqa: E402
import cases as CS  # noqa: E402
from cutqc.cli import main as cutqc_main  # noqa: E402
from cutqc.media import probe  # noqa: E402

SEV = {"高": 0, "中": 1, "低": 2}


def run_case(c, workdir):
    out = os.path.join(workdir, "out", c["id"])
    shutil.rmtree(out, ignore_errors=True)
    argv = [c["video"], "--out", out] + c.get("args", [])
    if c.get("cuts") is not None:
        cf = c.get("cuts_file")
        if not cf:
            cf = os.path.join(workdir, "cuts", c["id"] + ".txt")
            os.makedirs(os.path.dirname(cf), exist_ok=True)
            open(cf, "w").write("\n".join(f"{x:.4f}" for x in c["cuts"]))
        argv += ["--cuts", cf]
    if c.get("subs"):
        argv += ["--subs", c["subs"]]
    if "--only" not in argv:
        argv += ["--only", c.get("only", "flash,audio,subs")]
    buf = io.StringIO()
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(buf):
            cutqc_main(argv)
        issues = json.load(open(os.path.join(out, "issues.json"), encoding="utf-8"))
        err = None
    except SystemExit as e:
        issues, err = None, f"SystemExit {e.code}: {buf.getvalue()[-300:]}"
    except Exception:
        issues, err = None, traceback.format_exc()[-1200:]
    return issues, err, time.time() - t0


def score(c, issues):
    fps = c.get("fps") or 30.0
    res = {"found": [], "missed": [], "fp_high": [], "terr": []}
    used = set()
    for e in c["expect"]:
        best = None
        for k, it in enumerate(issues):
            if k in used or it["cat"] != e["cat"] or it["kind"] not in e["kinds"]:
                continue
            if SEV[it["sev"]] > SEV[e.get("min_sev", "中")]:
                continue
            if "n" in e and it.get("n") != e["n"]:
                continue
            d = abs(it["t"] - e["t"])
            if d <= e["tol"] and (best is None or d < best[1]):
                best = (k, d)
        if best:
            used.add(best[0])
            res["found"].append(e)
            if not e.get("loose"):
                res["terr"].append(best[1] * fps)
        else:
            res["missed"].append(e)
    for k, it in enumerate(issues):
        if k in used or it["sev"] != "高":
            continue
        # 和某个答案是同一处（同类、时间很近）的重复报告不算误报
        if any(it["cat"] == e["cat"] and abs(it["t"] - e["t"]) <= e["tol"] for e in c["expect"]):
            continue
        res["fp_high"].append(it)
    return res


def subs_cases(P, workdir):
    """字幕用例：中文解说原片 + 原字幕，截 40 秒窗口，故意改坏某几句"""
    cfg = json.load(open(cfg_path("bench_sources.json"), encoding="utf-8"))
    sc = cfg.get("subs_source")
    if not sc:
        return []
    from cutqc.subs import load_subs, write_srt
    src, srt = os.path.expanduser(sc["video"]), os.path.expanduser(sc["srt"])
    if not (os.path.exists(src) and os.path.exists(srt)):
        return []
    allc = load_subs(srt)
    C = []
    info = probe(src)
    for r in range(sc.get("windows", 3)):
        t0 = P.memo.get(f"subs{r}")
        if t0 is None:
            import random
            rng = random.Random(100 + r)
            while True:
                t0 = round(rng.uniform(120, info["duration"] - 120), 1)
                win = [c for c in allc if t0 + 1 <= c["s"] and c["e"] <= t0 + 39]
                if len(win) >= 12 and sum(len(c["text"]) for c in win) > 120:
                    break
            P.memo[f"subs{r}"] = t0
            P._save()
        rec = {"fps": 30.0, "size": (640, 360), "v": [{"src": src, "t": t0, "n": 40 * 30}]}
        v = render(rec, os.path.join(CACHE, "cases", f"subs{r}_{_h(rec)}.mp4"))
        win = [dict(c, s=round(c["s"] - t0, 3), e=round(c["e"] - t0, 3)) for c in allc if t0 + 1 <= c["s"] and c["e"] <= t0 + 39]
        long = [c for c in win if len(c["text"]) >= 7]
        d = os.path.join(workdir, "subs")
        os.makedirs(d, exist_ok=True)

        def wr(name, cues, enc="utf-8", bom=False):
            p = os.path.join(d, f"w{r}_{name}.srt")
            write_srt(cues, p)
            if enc != "utf-8" or bom:
                txt = open(p, encoding="utf-8").read()
                open(p, "w", encoding="utf-8-sig" if bom else enc).write(txt)
            return p
        clean = wr("clean", win)
        C.append(dict(id=f"字幕_w{r}_干净", group="字幕", video=v, subs=clean, fps=30.0, args=["--lang", "zh"], only="subs",
                      forbid_high=True, expect=[], count_mid=True))
        a, b, cc, dd, ee = long[1], long[3], long[5], long[7 % len(long)], long[2]
        late = [dict(c, s=c["s"] + 0.9, e=c["e"] + 0.9) if c is a else c for c in win]
        C.append(dict(id=f"字幕_w{r}_晚0.9秒", group="字幕", video=v, subs=wr("late", late), fps=30.0, args=["--lang", "zh"], only="subs",
                      expect=[dict(cat="字幕", kinds=["字幕晚出"], t=a["s"] + 0.9, tol=0.05)]))
        early = [dict(c, s=c["s"] - 1.0) if c is b else c for c in win]
        C.append(dict(id=f"字幕_w{r}_早1秒", group="字幕", video=v, subs=wr("early", early), fps=30.0, args=["--lang", "zh"], only="subs",
                      expect=[dict(cat="字幕", kinds=["字幕早出"], t=b["s"] - 1.0, tol=0.05)]))
        wrong = [dict(c, text="今天天气不错我们出去走走") if c is cc else c for c in win]
        C.append(dict(id=f"字幕_w{r}_文字全错", group="字幕", video=v, subs=wr("wrong", wrong), fps=30.0, args=["--lang", "zh"], only="subs",
                      expect=[dict(cat="字幕", kinds=["字幕和声音对不上", "字幕文字和读音不一致"], t=cc["s"], tol=0.05)]))
        miss = [c for c in win if c is not dd]
        C.append(dict(id=f"字幕_w{r}_漏一句", group="字幕", video=v, subs=wr("missing", miss), fps=30.0, args=["--lang", "zh"], only="subs",
                      expect=[dict(cat="字幕", kinds=["有人声没字幕"], t=dd["s"], tol=1.0, loose=True)]))
        short = [dict(c, e=c["s"] + 0.35) if c is ee else c for c in win]
        C.append(dict(id=f"字幕_w{r}_提前消失", group="字幕", video=v, subs=wr("endearly", short), fps=30.0, args=["--lang", "zh"], only="subs",
                      expect=[dict(cat="字幕", kinds=["字幕提前消失"], t=ee["s"], tol=0.05)]))
        if r == 0:  # 字幕文件格式：结果应和干净版一样不报“高”
            C.append(dict(id="字幕_GBK编码", group="字幕", video=v, subs=wr("gbk", win, enc="gbk"), fps=30.0, args=["--lang", "zh"], only="subs", forbid_high=True, expect=[]))
            C.append(dict(id="字幕_UTF8带BOM", group="字幕", video=v, subs=wr("bom", win, bom=True), fps=30.0, args=["--lang", "zh"], only="subs", forbid_high=True, expect=[]))
            ass = os.path.join(d, "w0.ass")

            def at(x):
                return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"
            with open(ass, "w", encoding="utf-8") as fh:
                fh.write("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Sub,Arial,40\n\n[Events]\n"
                         "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
                for c in win:
                    fh.write(f"Dialogue: 0,{at(c['s'])},{at(c['e'])},Sub,,0,0,0,,{{\\b1\\c&HFFFFFF&}}{c['text']}\n")
            C.append(dict(id="字幕_ASS格式", group="字幕", video=v, subs=ass, fps=30.0, args=["--lang", "zh"], only="subs", forbid_high=True, expect=[]))
            emp = os.path.join(d, "empty.srt")
            open(emp, "w").write("")
            C.append(dict(id="字幕_空文件", group="字幕", video=v, subs=emp, fps=30.0, args=["--lang", "zh"], only="subs", forbid_high=False, expect=[]))
    return C


def long_case(P, src, workdir, reps=40):
    """长片时间漂移：同一个“插 1 帧”片段首尾相接 reps 次，每一处都要抓到且时间码不漂"""
    rec, t = CS.flash_recipe(P, src, "mx", 30.0, (960, 540), 1)
    one = render(rec, os.path.join(CACHE, "cases", f"mx_30fps_{_h(rec)}.mp4"))
    L = sum(s["n"] for s in rec["v"]) / 30.0   # 按帧数算（ffmpeg 显示的时长四舍五入到 10ms，拼 40 段会累积）
    lst = os.path.join(CACHE, "cases", "long_list.txt")
    open(lst, "w").write("\n".join(f"file '{one}'" for _ in range(reps)))
    out = os.path.join(CACHE, "cases", f"long_{reps}_{_h(rec)}.mp4")
    from cutqc.media import ffmpeg
    import subprocess
    if not os.path.exists(out):
        subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out], check=True)
    # 拼接后画面第一帧不一定从 0 开始（音频编码延迟会把画面整体往后推一点），标准答案要加上这个起点
    import re
    o = subprocess.run([ffmpeg(), "-v", "info", "-i", out, "-t", "0.1", "-vf", "showinfo", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    v0 = float((re.findall(r"pts_time:([\d.]+)", o) or ["0"])[0])
    exp = [dict(cat="闪帧", kinds=["孤立闪帧（1帧）", "碎镜头"], t=k * L + t + v0, tol=0.25) for k in range(reps)]
    return [dict(id=f"格式_长片{reps}段拼接", group="格式", video=out, cuts=None, fps=30.0, args=["--asr", "none"], expect=exp)]


def cuts_format_cases(P, src, workdir):
    """剪辑点文件格式：txt 秒 / txt 时码 / csv 带表头 / json，结果应一致"""
    rec, t = CS.flash_recipe(P, src, "mx", 30.0, (960, 540), 1)
    v = render(rec, os.path.join(CACHE, "cases", f"mx_30fps_{_h(rec)}.mp4"))
    d = os.path.join(workdir, "cuts")
    os.makedirs(d, exist_ok=True)
    pts = [t, t + 1 / 30]
    f1 = os.path.join(d, "fmt_tc.txt")
    open(f1, "w").write("# 时:分:秒:帧\n" + "\n".join(f"0:00:{int(x):02d}:{round((x % 1) * 30):02d}" for x in pts))
    f2 = os.path.join(d, "fmt.csv")
    with open(f2, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["序号", "切点", "备注"])
        for i, x in enumerate(pts):
            w.writerow([i + 1, f"{x:.3f}", "测试"])
    f3 = os.path.join(d, "fmt.json")
    json.dump({"cuts": pts}, open(f3, "w"))
    out = []
    for name, f in (("时码txt", f1), ("带表头csv", f2), ("json", f3)):
        out.append(dict(id=f"格式_剪辑点{name}", group="格式", video=v, cuts=pts, cuts_file=f, fps=30.0, args=["--asr", "none"],
                        expect=[dict(cat="闪帧", kinds=["孤立闪帧（1帧）", "碎镜头"], t=t, tol=0.25)]))
    return out


def real_cases():
    """真实片标注：已经人工确认过的位置"""
    p = cfg_path("labels.json")
    if not os.path.exists(p):
        return []
    C = []
    for L in json.load(open(p, encoding="utf-8")):
        L["video"] = os.path.expanduser(L["video"])
        if L.get("cuts"):
            L["cuts"] = os.path.expanduser(L["cuts"])
        if not os.path.exists(L["video"]):
            continue
        args = ["--asr", L.get("asr", "none")]
        if L.get("start") is not None:
            args += ["--start", str(L["start"]), "--dur", str(L["dur"])]
        exp = [dict(cat=e["cat"], kinds=e["kinds"], t=e["t"], tol=e.get("tol", 0.15), min_sev=e.get("min_sev", "中")) for e in L.get("expect", [])]
        C.append(dict(id="真实_" + L["id"], group="真实片", video=L["video"], cuts=None, cuts_file=L.get("cuts"), fps=L.get("fps", 30.0),
                      args=args, only=L.get("only", "flash,audio"), forbid_high=L.get("forbid_high", True), expect=exp,
                      forbid_cat=L.get("forbid_cat")))
        if L.get("cuts"):
            C[-1]["cuts"] = []  # 占位：用 cuts_file
    return C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args()
    cfg = json.load(open(cfg_path("bench_sources.json"), encoding="utf-8"))
    srcs = [os.path.expanduser(s) for s in cfg["sources"] if os.path.exists(os.path.expanduser(s))]
    if not srcs:
        sys.exit("没有可用的测试素材：复制 tests/bench_sources.json 为 tests/bench_sources.local.json，填上你电脑上的素材路径")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    workdir = os.path.join(HERE, "results", stamp + ("_full" if a.full else "_quick"))
    os.makedirs(workdir, exist_ok=True)
    print(f"素材 {len(srcs)} 个；准备测试片（第一次会慢，之后有缓存）…", flush=True)
    P = Picker(srcs)
    want = lambda g: not a.only or a.only in g or g in a.only
    C = CS.build(P, srcs, quick=not a.full) if any(want(g) for g in ("故障-闪帧", "故障-声音", "陷阱", "插", "碎镜头", "咔哒", "截断", "跳变", "s0", "s1")) else []
    if want("格式"):
        C += CS.build_matrix(P, srcs[0])
        C += cuts_format_cases(P, srcs[0], workdir)
    if a.full:
        C += long_case(P, srcs[0], workdir)
        C += subs_cases(P, workdir)
        C += real_cases()
    else:
        C += [c for c in real_cases() if "asr" not in c["args"] or c["args"][c["args"].index("--asr") + 1] == "none"]
    if a.only:
        C = [c for c in C if a.only in c["group"] or a.only in c["id"]]
    print(f"共 {len(C)} 个用例", flush=True)
    rows = []
    for i, c in enumerate(C, 1):
        issues, err, sec = run_case(c, workdir)
        if err:
            r = dict(id=c["id"], group=c["group"], status="崩溃", err=err, sec=sec)
        else:
            s = score(c, issues)
            if c["group"].startswith("故障") and c["expect"] and not c.get("forbid_cat"):
                c["forbid_cat"] = [c["expect"][0]["cat"]]
            if c.get("forbid_cat"):
                s["fp_high"] = [x for x in s["fp_high"] if x["cat"] in c["forbid_cat"]]
            fp = s["fp_high"] if c.get("forbid_high", True) else []
            ok = not s["missed"] and not fp and all(x <= 1.0 for x in s["terr"])
            r = dict(id=c["id"], group=c["group"], status="通过" if ok else "失败", expect=len(c["expect"]),
                     found=len(s["found"]), missed=[(m["kinds"][0], round(m["t"], 3)) for m in s["missed"]],
                     fp_high=[(x["kind"], x["t"], x["msg"][:60]) for x in fp], terr=[round(x, 2) for x in s["terr"]],
                     mid=sum(1 for x in issues if x["sev"] == "中") if c.get("count_mid") else None, sec=round(sec, 1))
        rows.append(r)
        flag = {"通过": "✓", "失败": "✗", "崩溃": "!"}[r["status"]]
        extra = ""
        if r["status"] == "失败":
            extra = f"  漏 {r['missed']}  误报高 {r['fp_high'][:2]}  时间误差(帧) {r['terr']}"
        elif r["status"] == "崩溃":
            extra = "  " + r["err"].strip().splitlines()[-1][:120]
        print(f"[{i}/{len(C)}] {flag} {c['id']}（{sec:.0f}s）{extra}", flush=True)
    write_scorecard(rows, workdir)


def write_scorecard(rows, workdir):
    groups = {}
    for r in rows:
        g = groups.setdefault(r["group"], {"n": 0, "pass": 0, "crash": 0, "exp": 0, "found": 0, "fp": 0, "terr": []})
        g["n"] += 1
        g["pass"] += r["status"] == "通过"
        g["crash"] += r["status"] == "崩溃"
        g["exp"] += r.get("expect", 0)
        g["found"] += r.get("found", 0)
        g["fp"] += len(r.get("fp_high", []))
        g["terr"] += r.get("terr", [])
    summ = {"rows": rows, "groups": groups}
    json.dump(summ, open(os.path.join(workdir, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    prev = None
    allr = sorted(d for d in os.listdir(os.path.join(HERE, "results")) if os.path.isdir(os.path.join(HERE, "results", d)))
    for d in reversed(allr):
        p = os.path.join(HERE, "results", d, "summary.json")
        if d != os.path.basename(workdir) and os.path.exists(p) and d.split("_")[-1] == os.path.basename(workdir).split("_")[-1]:
            prev = json.load(open(p, encoding="utf-8"))
            break
    L = ["# cutqc 压力测试成绩单", "", f"结果目录：`{workdir}`", "",
         "| 组 | 用例 | 通过 | 崩溃 | 检出率 | “高”级误报 | 时间码误差（帧，最大/平均） |", "|---|---|---|---|---|---|---|"]
    for gname, g in groups.items():
        det = f"{g['found']}/{g['exp']}（{g['found'] / g['exp'] * 100:.0f}%）" if g["exp"] else "—"
        te = f"{max(g['terr']):.2f} / {sum(g['terr']) / len(g['terr']):.2f}" if g["terr"] else "—"
        L.append(f"| {gname} | {g['n']} | {g['pass']} | {g['crash']} | {det} | {g['fp']} | {te} |")
    mids = [r["mid"] for r in rows if r.get("mid") is not None]
    if mids:
        L += ["", f"干净字幕窗口里平均每个窗口报 {sum(mids) / len(mids):.1f} 条“中”级（参考误报水平）"]
    bad = [r for r in rows if r["status"] != "通过"]
    if bad:
        L += ["", "## 没通过的用例", ""]
        for r in bad:
            if r["status"] == "崩溃":
                L.append(f"- **{r['id']}** 崩溃：`{r['err'].strip().splitlines()[-1][:160]}`")
            else:
                L.append(f"- **{r['id']}**：漏报 {r['missed'] or '无'}；“高”级误报 {r['fp_high'] or '无'}；时间码误差 {r['terr']}")
    if prev:
        pv = {r["id"]: r["status"] for r in prev["rows"]}
        worse = [r["id"] for r in rows if pv.get(r["id"]) == "通过" and r["status"] != "通过"]
        better = [r["id"] for r in rows if pv.get(r["id"]) and pv[r["id"]] != "通过" and r["status"] == "通过"]
        L += ["", "## 和上一次比较", "", f"- 变差（上次通过、这次没过）：{worse or '无'}", f"- 变好：{better or '无'}"]
    total = len(rows)
    passed = sum(r["status"] == "通过" for r in rows)
    L.insert(4, f"**总计 {passed}/{total} 通过**")
    L.insert(5, "")
    md = os.path.join(workdir, "成绩单.md")
    open(md, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
