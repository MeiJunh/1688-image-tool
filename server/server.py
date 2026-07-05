"""本地服务：接收油猴脚本发来的图片链接，下载 + 去水印，存到 output/商品名/。

启动：  python server.py
接口：  POST /process   { "name": "商品名", "urls": ["...", ...], "source_url": "..." }
              -> 立即返回 { ok, task_id }，处理在后台进行（避免浏览器长时间等待/断连）
        GET  /status?id=<task_id>   查询任务进度
        GET  /health
"""
import os
import re
import json
import time
import uuid
import threading
import traceback
from urllib.parse import urlparse, urlparse as _up, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import downloader
import dewatermark

HERE = os.path.dirname(os.path.abspath(__file__))

# 后台任务表：task_id -> 进度/结果字典
TASKS = {}
TASKS_LOCK = threading.Lock()


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


def process(payload, cfg, task=None):
    urls = [u for u in payload.get("urls", []) if isinstance(u, str) and u.startswith("http")]
    urls = list(dict.fromkeys(urls))  # 去重且保序
    if not urls:
        return {"ok": False, "error": "没有收到有效图片链接"}

    name = safe_name(payload.get("name"))
    ts = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.abspath(os.path.join(HERE, cfg["output_dir"], f"{name}_{ts}"))
    original_dir = os.path.join(base, "original")
    clean_dir = os.path.join(base, "clean")

    if task is not None:
        _update(task, stage="下载中", total=len(urls), out_dir=base)
    print(f"    [下载] 开始下载 {len(urls)} 张 ...", flush=True)
    dl = downloader.download_all(urls, original_dir, cfg["download"])
    saved = dl["saved"]
    print(f"    [下载] 完成 {len(saved)}/{len(urls)}，失败 {len(dl['errors'])}", flush=True)
    if task is not None:
        _update(task, downloaded=len(saved), failed=len(dl["errors"]))
    if not saved:
        return {"ok": False, "error": "全部图片下载失败", "detail": dl["errors"], "out_dir": base}

    dw = cfg.get("dewatermark", {})
    mask_info = ""
    if dw.get("enabled", True):
        if task is not None:
            _update(task, stage="去水印中")
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


def _update(task, **kw):
    """线程安全地更新任务进度。"""
    with TASKS_LOCK:
        task.update(kw)


def _run_task(tid, payload, cfg):
    """后台线程：执行 process 并把结果写回任务表。"""
    task = TASKS[tid]
    try:
        result = process(payload, cfg, task=task)
        result["done"] = True
        result["stage"] = "完成" if result.get("ok") else "失败"
        _update(task, **result)
        print(f"    -> 任务 {tid} 完成：下载 {result.get('downloaded')}/{result.get('total')}，"
              f"{result.get('dewatermark')}", flush=True)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _update(task, ok=False, done=True, stage="失败", error=str(e))


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
        elif self.path.startswith("/status"):
            qs = parse_qs(_up(self.path).query)
            tid = (qs.get("id") or [""])[0]
            with TASKS_LOCK:
                task = dict(TASKS.get(tid, {})) if tid else {}
            if not task:
                self._send(404, {"ok": False, "error": "任务不存在或已过期", "task_id": tid})
            else:
                self._send(200, task)
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
            tid = uuid.uuid4().hex[:12]
            with TASKS_LOCK:
                TASKS[tid] = {"ok": None, "done": False, "stage": "排队中",
                              "total": len(names), "downloaded": 0, "task_id": tid}
            print(f"[{time.strftime('%H:%M:%S')}] 收到任务 {tid} '{payload.get('name')}' "
                  f"共 {len(names)} 链接，后台处理中...")
            # 立即返回，处理放到后台线程，避免浏览器长时间等待/断连
            threading.Thread(target=_run_task, args=(tid, payload, self.cfg),
                             daemon=True).start()
            self._send(200, {"ok": True, "task_id": tid, "started": True,
                             "message": "已开始后台处理"})
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
