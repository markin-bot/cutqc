"""逐字语音识别（faster-whisper），结果按 视频+区间+模型 缓存，重跑质检不用再识别。"""
import hashlib
import json
import os

SR = 16000


def _key(path, start, dur, model, lang=""):
    st = os.stat(path)
    return hashlib.md5(f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime}|{start}|{dur}|{model}|{lang}".encode()).hexdigest()[:16]


def transcribe(wav, path, start, dur, model="small", cache_dir=None, language="zh", log=print):
    cache_dir = cache_dir or os.path.expanduser("~/.cache/cutqc")
    os.makedirs(cache_dir, exist_ok=True)
    cf = os.path.join(cache_dir, f"asr_{_key(path, start, dur, model, language)}.json")
    if os.path.exists(cf):
        log(f"  用缓存的识别结果 {cf}")
        return json.load(open(cf, encoding="utf-8"))
    from faster_whisper import WhisperModel
    try:
        import ctranslate2
        dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        dev = "cpu"
    log(f"  加载识别模型 {model}（{dev}）")
    m = WhisperModel(model, device=dev, compute_type="float16" if dev == "cuda" else "int8")
    try:
        from opencc import OpenCC
        cc = OpenCC("t2s").convert
    except Exception:
        cc = lambda s: s
    lang = None if language in (None, "", "auto") else language
    segs, inf = m.transcribe(wav, language=lang, word_timestamps=True, vad_filter=True,
                           initial_prompt="以下是普通话的句子。" if lang == "zh" else None,
                           condition_on_previous_text=False)
    if lang is None:
        log(f"  自动识别语言：{inf.language}（{inf.language_probability:.0%}）")
    words = []
    total = len(wav) / SR
    last = 0
    for s in segs:
        for w in s.words or []:
            words.append({"s": round(start + w.start, 3), "e": round(start + w.end, 3), "w": cc(w.word),
                          "p": round(w.probability, 3)})
        if s.end - last > 60:
            last = s.end
            log(f"  识别进度 {s.end / max(total, 1) * 100:.0f}%")
    json.dump(words, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
    return words
