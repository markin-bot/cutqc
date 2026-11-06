"""输出：网页报告（点时间码直接跳到视频那一刻）+ issues.json + 修正清单.csv"""
import csv
import html
import json
import os
from urllib.parse import quote

import numpy as np

from .media import frame_strip, tc

SEV_ORDER = {"高": 0, "中": 1, "低": 2}


def wave_png(wav, sr, t0, t, out, span=0.6, w=420, h=70):
    from PIL import Image, ImageDraw
    a, b = int((t - span - t0) * sr), int((t + span - t0) * sr)
    seg = wav[max(0, a):max(0, b)]
    img = Image.new("RGB", (w, h), (250, 250, 250))
    dr = ImageDraw.Draw(img)
    if len(seg):
        cols = np.array_split(np.abs(seg), w)
        for x, c in enumerate(cols):
            v = float(c.max()) if len(c) else 0
            y = int(v * (h / 2 - 2))
            dr.line([(x, h // 2 - y), (x, h // 2 + y)], fill=(70, 110, 180))
    dr.line([(w // 2, 0), (w // 2, h)], fill=(220, 40, 40), width=2)
    img.save(out)


def build(issues, video, info, outdir, wav=None, sr=16000, t0=0.0, meta=None, max_img=250):
    os.makedirs(os.path.join(outdir, "img"), exist_ok=True)
    issues.sort(key=lambda x: (SEV_ORDER.get(x["sev"], 3), x["t"]))
    fps = info["fps"]
    made = 0
    for k, it in enumerate(issues):
        if made >= max_img or it["sev"] == "低":
            continue
        if it["cat"] == "闪帧":
            p = os.path.join(outdir, "img", f"f{k}.jpg")
            if frame_strip(video, it["t"], fps, p):
                it["img"] = "img/" + os.path.basename(p)
                made += 1
        elif it["cat"] == "声音" and wav is not None:
            p = os.path.join(outdir, "img", f"a{k}.png")
            wave_png(wav, sr, t0, it["t"], p)
            it["img"] = "img/" + os.path.basename(p)
            made += 1
    json.dump(issues, open(os.path.join(outdir, "issues.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "修正清单.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["严重程度", "类别", "问题", "时间码", "秒", "说明", "备注"])
        for it in issues:
            w.writerow([it["sev"], it["cat"], it["kind"], tc(it["t"]), it["t"], it["msg"], it.get("note", "")])
    # 网页
    cnt = {}
    for it in issues:
        cnt.setdefault(it["cat"], {"高": 0, "中": 0, "低": 0})[it["sev"]] += 1
    vsrc = "file://" + quote(os.path.abspath(video))
    rows = []
    for it in issues:
        img = f'<img src="{it["img"]}" loading="lazy">' if it.get("img") else ""
        note = f'<div class="note">{html.escape(it["note"])}</div>' if it.get("note") else ""
        rows.append(
            f'<tr class="r" data-cat="{it["cat"]}" data-sev="{it["sev"]}"><td><span class="sev s{it["sev"]}">{it["sev"]}</span></td>'
            f'<td>{it["cat"]}</td><td><a href="#" onclick="go({it["t"]});return false">{tc(it["t"])}</a></td>'
            f'<td><b>{html.escape(it["kind"])}</b><div>{html.escape(it["msg"])}</div>{note}</td><td>{img}</td></tr>')
    summ = " ".join(f'<span class="chip">{c}：高 {v["高"]} · 中 {v["中"]} · 低 {v["低"]}</span>' for c, v in cnt.items()) or "没有发现问题"
    m = meta or {}
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>成片质检报告</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--hi:#d70015;--mid:#c25e00;--lo:#8e8e93}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1c1e;--fg:#f2f2f7;--mut:#a1a1a6;--line:#3a3a3c}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"PingFang SC",sans-serif}}
header{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;z-index:2;display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}}
video{{width:min(480px,100%);background:#000;border-radius:6px}}
h1{{font-size:18px;margin:0 0 4px}} .mut{{color:var(--mut);font-size:12px}} .chip{{display:inline-block;margin:2px 6px 2px 0;padding:2px 8px;border:1px solid var(--line);border-radius:10px;font-size:12px}}
table{{border-collapse:collapse;width:100%}} td{{border-bottom:1px solid var(--line);padding:8px;vertical-align:top}}
td img{{max-width:min(720px,60vw);border-radius:4px}} .sev{{font-weight:600}} .s高{{color:var(--hi)}} .s中{{color:var(--mid)}} .s低{{color:var(--lo)}}
.note{{color:var(--mut);font-size:12px}} label{{margin-right:10px;font-size:13px}} main{{padding:0 16px 40px}}
</style></head><body><header><video id="v" src="{vsrc}" controls preload="metadata"></video><div>
<h1>成片质检报告</h1><div class="mut">{html.escape(os.path.basename(video))} · {tc(info['duration'])} · {fps:g} fps
{(' · 检查区间 ' + html.escape(m['range'])) if m.get('range') else ''}{(' · 剪辑点 ' + str(m['n_cuts']) + ' 个') if m.get('n_cuts') is not None else ''}</div>
<div>{summ}</div><div style="margin-top:6px">
<label><input type="checkbox" class="fs" value="高" checked>高</label><label><input type="checkbox" class="fs" value="中" checked>中</label><label><input type="checkbox" class="fs" value="低">低</label>
<label><input type="checkbox" class="fc" value="闪帧" checked>闪帧</label><label><input type="checkbox" class="fc" value="声音" checked>声音</label><label><input type="checkbox" class="fc" value="字幕" checked>字幕</label></div>
<div class="mut">点时间码跳到问题前 1 秒播放。视频放不出来时，用浏览器直接打开这个网页文件（视频按本地路径加载）。</div></div></header>
<main><table>{''.join(rows)}</table></main><script>
const v=document.getElementById('v');function go(t){{try{{v.currentTime=Math.max(0,t-1);v.play()}}catch(e){{}}}}
function f(){{const s=[...document.querySelectorAll('.fs:checked')].map(x=>x.value),c=[...document.querySelectorAll('.fc:checked')].map(x=>x.value);
document.querySelectorAll('tr.r').forEach(r=>r.style.display=(s.includes(r.dataset.sev)&&c.includes(r.dataset.cat))?'':'none')}}
document.querySelectorAll('input').forEach(i=>i.onchange=f);f();</script></body></html>"""
    out = os.path.join(outdir, "质检报告.html")
    open(out, "w", encoding="utf-8").write(page)
    return out, cnt
