"""本地服务：接收油猴脚本发来的图片链接，下载 + 去水印，存到 output/商品名/。

启动：  python server.py
接口：  POST /process   { "name": "商品名", "urls": ["...", ...], "source_url": "..." }
        GET  /health
"""
import os
import re
import json
import time
import traceback
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import downloader
import dewatermark

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config():
    with open(os.path.join(HERE, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def safe_name(name):
    name = (name or "").strip() or "untitled"
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    return name[:80]


def host_key_of(url):
    try:
        return urlparse(url).hostname or None
    except Exception:  # noqa: BLE001
        return None


def process(payload, cfg):
    urls = [u for u in payload.get("urls", []) if isinstance(u, str) and u.startswith("http")]
    urls = list(dict.fromkeys(urls))  # 去重且保序
    if not urls:
        return {"ok": False, "error": "没有收到有效图片链接"}

    name = safe_name(payload.get("name"))
    ts = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.abspath(os.path.join(HERE, cfg["output_dir"], f"{name}_{ts}"))
    original_dir = os.path.join(base, "original")
    clean_dir = os.path.join(base, "clean")

    print(f"    [下载] 开始下载 {len(urls)} 张 ...", flush=True)
    dl = downloader.download_all(urls, original_dir, cfg["download"])
    saved = dl["saved"]
    print(f"    [下载] 完成 {len(saved)}/{len(urls)}，失败 {len(dl['errors'])}", flush=True)
    if not saved:
        return {"ok": False, "error": "全部图片下载失败", "detail": dl["errors"], "out_dir": base}

    dw = cfg.get("dewatermark", {})
    mask_info = ""
    if dw.get("enabled", True):
        host = host_key_of(payload.get("source_url", ""))
        ok, info, _ = dewatermark.run(saved, original_dir, clean_dir, dw, host_key=host)
        mask_info = info
    else:
        clean_dir = None
        mask_info = "去水印已关闭"

    return {
        "ok": True,
        "name": name,
        "total": len(urls),
        "downloaded": len(saved),
        "failed": len(dl["errors"]),
        "errors": dl["errors"][:10],
        "out_dir": base,
        "original_dir": original_dir,
        "clean_dir": clean_dir,
        "dewatermark": mask_info,
    }


class Handler(BaseHTTPRequestHandler):
    cfg = None

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        self._send(200, {"ok": True})

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/health"):
            self._send(200, {"ok": True, "service": "1688-tool", "time": time.time()})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/process"):
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self._send(400, {"ok": False, "error": f"请求解析失败: {e}"})
            return
        try:
            names = payload.get("urls", [])
            print(f"[{time.strftime('%H:%M:%S')}] 收到任务 '{payload.get('name')}' "
                  f"共 {len(names)} 链接，开始处理...")
            result = process(payload, self.cfg)
            print(f"    -> 下载 {result.get('downloaded')}/{result.get('total')}，"
                  f"{result.get('dewatermark')}")
            self._send(200, result)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, *args):  # 静音默认访问日志
        pass


def main():
    cfg = load_config()
    Handler.cfg = cfg
    host = cfg["server"]["host"]
    port = int(cfg["server"]["port"])
    srv = ThreadingHTTPServer((host, port), Handler)
    print("=" * 56)
    print("  1688 图片下载 + 自动去水印  本地服务已启动")
    print(f"  监听: http://{host}:{port}")
    print(f"  输出目录: {os.path.abspath(os.path.join(HERE, cfg['output_dir']))}")
    print("  用的时候保持本窗口开启。Ctrl+C 退出。")
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
