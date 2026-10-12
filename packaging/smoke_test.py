"""打包产物的冒烟测试:在一份临时拷贝上启动 exe,确认界面、AI 分析(librosa/numba)、重复启动检测都正常。

用法(项目根目录):python packaging/smoke_test.py dist/CS2MusicBox
只用标准库 + numpy(生成测试曲),不会改动 dist 里的文件。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PORT = 31799
BASE = f"http://127.0.0.1:{PORT}"
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("[ OK ] " if cond else "[FAIL] ") + name + ("" if cond else f"  {extra}"), flush=True)
    fails += 0 if cond else 1


def call(path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data, {"X-MusicBox": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def main(dist):
    exe_name = "CS2MusicBox.exe" if os.name == "nt" else "CS2MusicBox"
    work = tempfile.mkdtemp(prefix="mb_smoke_")
    app = os.path.join(work, "CS2MusicBox")
    shutil.copytree(dist, app)
    exe = os.path.join(app, exe_name)
    with open(os.path.join(app, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"port": PORT, "open_browser": False}, f)

    import demo_song                                   # 合成两首测试曲放进曲库
    os.makedirs(os.path.join(app, "music"))
    x, _ = demo_song.edm_track()
    demo_song.write_wav(os.path.join(app, "music", "demo_edm.wav"), x)
    demo_song.write_wav(os.path.join(app, "music", "demo_sad.wav"), demo_song.sad_track())

    env = {**os.environ, "SDL_AUDIODRIVER": "dummy"}  # 构建机没有声卡
    log = open(os.path.join(work, "app.log"), "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([exe, "--no-browser"], cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        st = None
        for _ in range(60):
            try:
                st = call("/api/state", timeout=2)
                break
            except Exception:
                time.sleep(1)
        check("程序启动,面板 API 可用", st is not None and "game" in st)
        if st is None:
            return
        check("曲库读到了 exe 旁边的 music 文件夹", sorted(f["file"] for f in st["library"]) == ["demo_edm.wav", "demo_sad.wav"],
              st["library"])
        check("进程检测可用(psutil)", st["proc"]["available"] is True, st["proc"])
        with urllib.request.urlopen(BASE + "/", timeout=5) as r:
            html = r.read().decode("utf-8")
        check("网页界面打包进去了(含队友语音、新手教程)", "CS2 音乐盒" in html and len(html) > 50000
              and 'data-page="voice"' in html and 'id="page-guide"' in html, len(html))
        check("新手教程.html 和 使用说明.txt 在 exe 旁边",
              os.path.exists(os.path.join(dist, "新手教程.html")) and os.path.exists(os.path.join(dist, "使用说明.txt")))
        v = call("/api/voice/devices", {})
        check("队友语音的音频组件可用(sounddevice + PortAudio)", v.get("available") is True, v.get("error"))

        t0 = time.time()
        r = call("/api/analyze", {"files": [], "durations": {}})
        check("开始 AI 分析", r.get("ok"), r)
        a = {}
        while time.time() - t0 < 300:
            a = call("/api/analysis")
            if a.get("status") != "running":
                break
            time.sleep(1)
        check(f"AI 分析完成(librosa + numba,{time.time() - t0:.0f} 秒)",
              a.get("status") == "done" and len(a.get("songs", [])) == 2 and not a.get("failed"),
              {k: a.get(k) for k in ("status", "error", "failed", "progress")})
        r = call("/api/song_info", {"file": "demo_edm.wav"}, timeout=60)
        check("剪辑台节拍/段落信息", r.get("ok") and len(r["info"]["bars"]) > 10, str(r)[:200])

        r = call("/api/demo", {"action": "start", "speed": 20})
        check("模拟一局可以启动", r.get("ok"), r)
        t1 = time.time()
        while time.time() - t1 < 20 and call("/api/state")["demo"]["running"]:
            time.sleep(0.5)
        st = call("/api/state")
        check("模拟一局跑完并记入教程进度", not st["demo"]["running"] and st["guide"]["demo_done"], (st["demo"], st["guide"]))

        q = subprocess.run([exe, "--no-browser"], cwd=work, env=env, capture_output=True, timeout=60)
        out = q.stdout.decode("utf-8", "replace")
        check("重复启动时提示已在运行,不会报端口占用", q.returncode == 0 and "已经在运行" in out, (q.returncode, out[-300:]))
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()
        with open(os.path.join(work, "app.log"), encoding="utf-8", errors="replace") as f:
            text = f.read()
        check("运行日志里没有报错", "Traceback" not in text, text[-1500:])
        print("\n---- 程序日志 ----\n" + text[-3000:])
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "CS2MusicBox")))
    print("\n全部通过" if not fails else f"\n{fails} 项未通过")
    sys.exit(1 if fails else 0)
