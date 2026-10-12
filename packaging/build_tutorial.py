"""把 docs/新手教程.md 转成一个单文件网页(图片内嵌),打包时放在 exe 旁边,双击就能用浏览器看。

用法:python packaging/build_tutorial.py [输出路径]      (默认 dist/新手教程.html)
打包配置 cs2musicbox.spec 会自动调用;需要 markdown 库(requirements-exe.txt 里有)。
"""
import base64
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "新手教程.md")

STYLE = """
:root{--bg:#1a1e24;--panel:#232932;--inset:#151a20;--line:#38404c;--paper:#ece8df;--dim:#9aa3ae;--signal:#f2a541;--ok:#6cc59a;--fuse:#e0604f}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--paper)}
body{font:15px/1.75 "Bahnschrift","Microsoft YaHei UI","PingFang SC","Noto Sans SC",system-ui,sans-serif;padding:32px 20px 80px}
main{max-width:880px;margin:0 auto}
h1{font-size:28px;margin:0 0 12px}
h1::after{content:"";display:block;width:56px;height:3px;background:var(--signal);margin-top:12px;border-radius:2px}
h2{font-size:21px;margin:44px 0 10px;padding-top:8px}
h3{font-size:17px;margin:28px 0 8px}
p,li{color:#d9d5cc}
a{color:var(--signal)}
strong{color:var(--paper)}
hr{border:0;border-top:1px solid var(--line);margin:40px 0}
img{display:block;max-width:100%;height:auto;margin:14px 0 6px;border:1px solid var(--line);border-radius:8px}
code{background:var(--inset);border:1px solid var(--line);border-radius:4px;padding:1px 6px;font-family:Consolas,"Cascadia Mono",monospace;font-size:.92em}
pre{background:var(--inset);border:1px solid var(--line);border-radius:8px;padding:12px 16px;overflow:auto}
pre code{border:0;padding:0;font-size:15px;color:var(--signal)}
blockquote{margin:14px 0;padding:8px 16px;border-left:3px solid var(--signal);background:rgba(242,165,65,.08);border-radius:0 8px 8px 0}
blockquote p{margin:6px 0}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
th{background:var(--panel)}
ul,ol{padding-left:22px}
"""


def _embed_images(html, base):
    def repl(m):
        path = os.path.join(base, m.group(1))
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            return m.group(0)
        return f'src="data:image/png;base64,{data}"'
    return re.sub(r'src="([^":]+\.png)"', repl, html)


def build(out_path, src=SRC):
    import markdown
    with open(src, encoding="utf-8") as f:
        text = f.read()
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    body = _embed_images(body, os.path.dirname(src))
    body = re.sub(r'<a href="(https?://[^"]+)"', r'<a href="\1" target="_blank" rel="noopener"', body)
    html = (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>CS2 音乐盒 · 新手教程</title>'
            f'<style>{STYLE}</style></head><body><main>{body}</main></body></html>')
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "新手教程.html")
    print("已生成", build(out))
