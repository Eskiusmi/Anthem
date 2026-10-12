"""模拟器:不开 CS2,向正在运行的音乐盒发送一整局假数据,用来试听效果。

用法:先运行 python main.py,再开一个终端运行 python simulate.py
(面板「新手教程」里的「模拟一局」按钮做的是同一件事,不用开终端)
可选参数:--speed 2   (加快节奏)
"""
import json
import sys
import time
import urllib.request

from config import CONFIG_PATH
from gsi_fake import demo_script

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)
URL = f"http://127.0.0.1:{CFG['port']}/gsi"
SPEED = float(sys.argv[sys.argv.index("--speed") + 1]) if "--speed" in sys.argv else 1.0


def send(label, data, wait):
    data["auth"] = {"token": CFG["token"]}
    req = urllib.request.Request(URL, json.dumps(data).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=3).read()
    print(f"→ {label}")
    time.sleep(wait / SPEED)


SCRIPT = demo_script()

if __name__ == "__main__":
    print("提示:回合/炸弹“剩余10秒”需要真实等待回合时长,模拟器里不演示。\n")
    try:
        for label, data, wait in SCRIPT:
            send(label, data, wait)
    except OSError as e:
        print(f"连接失败:{e}\n请先运行 python main.py")
