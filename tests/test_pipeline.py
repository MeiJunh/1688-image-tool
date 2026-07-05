"""端到端测试，输出结构化结果供生成测试报告。

覆盖：
  A. 自动水印检测（跨图一致性）——召回率
  B. 去水印效果 —— 水印区误差下降
  C. 下载模块 —— 带 Referer 从本地 HTTP 抓图并落盘
  D. 服务全链路 —— 启动 server.py，POST /process，产物落地
"""
import os
import sys
import json
import time
import threading
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(HERE, "..", "server")
sys.path.insert(0, os.path.abspath(SERVER_DIR))

import automask  # noqa: E402
import dewatermark  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
RESULTS = {"cases": []}


def record(name, passed, detail):
    RESULTS["cases"].append({"name": name, "pass": bool(passed), "detail": detail})
    flag = "PASS" if passed else "FAIL"
    print(f"[{flag}] {name} :: {detail}")


# ---------------- A + B：检测与去水印 ----------------
def test_detect_and_remove():
    wm_dir = os.path.join(FIX, "wm")
    gt_dir = os.path.join(FIX, "gt")
    truth_dir = os.path.join(FIX, "truth")
    paths = sorted(os.path.join(wm_dir, f) for f in os.listdir(wm_dir))

    dw_cfg = {
        "engine": "opencv",
        "mask_strategy": "consistency",
        "consistency": {"edge_percentile": 85, "dilate": 6, "min_group": 3},
        "heuristic": {"white_thresh": 205, "sat_max": 40, "dilate": 4},
        "regions": {},
    }
    masks = automask.build_masks(paths, dw_cfg)

    # A. 召回率：预测蒙版覆盖了多少真实水印像素
    recalls = []
    for p in paths:
        base = os.path.basename(p)
        truth = cv2.imread(os.path.join(truth_dir, base), cv2.IMREAD_GRAYSCALE)
        pred = masks[p]
        if pred is None:
            recalls.append(0.0)
            continue
        if pred.shape != truth.shape:
            pred = cv2.resize(pred, (truth.shape[1], truth.shape[0]))
        tp = np.logical_and(truth > 0, pred > 0).sum()
        recalls.append(tp / max(1, (truth > 0).sum()))
    avg_recall = float(np.mean(recalls))
    record("A.自动检测水印-召回率", avg_recall >= 0.75,
           f"平均召回 {avg_recall:.1%}（阈值75%）")

    # B. 去水印效果：水印区误差下降
    out_dir = os.path.join(HERE, "fixtures", "_clean")
    os.makedirs(out_dir, exist_ok=True)
    ok, info, _ = dewatermark.run(paths, wm_dir, out_dir, dw_cfg)
    before_errs, after_errs = [], []
    for p in paths:
        base = os.path.basename(p)
        gt = cv2.imread(os.path.join(gt_dir, base)).astype(np.float32)
        wm = cv2.imread(p).astype(np.float32)
        clean_path = os.path.join(out_dir, base)
        clean = cv2.imread(clean_path)
        if clean is None:
            continue
        clean = clean.astype(np.float32)
        truth = cv2.imread(os.path.join(truth_dir, base), cv2.IMREAD_GRAYSCALE) > 0
        m = truth[:, :, None]
        before_errs.append(float((np.abs(wm - gt) * m).sum() / max(1, m.sum() * 3)))
        after_errs.append(float((np.abs(clean - gt) * m).sum() / max(1, m.sum() * 3)))
    be, ae = float(np.mean(before_errs)), float(np.mean(after_errs))
    drop = (be - ae) / be if be else 0
    record("B.去水印-水印区误差下降", drop >= 0.4,
           f"去水印前误差 {be:.1f} → 后 {ae:.1f}，下降 {drop:.0%}（阈值40%）；{info}")
    return {"avg_recall": avg_recall, "err_before": be, "err_after": ae, "drop": drop}


# ---------------- C + D：下载 + 服务全链路 ----------------
def _free_static_server(directory):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_download_and_service():
    # 起一个静态服务器充当"图片站"
    static, sport = _free_static_server(os.path.join(FIX, "wm"))
    files = sorted(os.listdir(os.path.join(FIX, "wm")))
    urls = [f"http://127.0.0.1:{sport}/{f}" for f in files]

    # 起 app 服务
    import server as appserver  # noqa: E402
    cfg = appserver.load_config()
    cfg["dewatermark"]["engine"] = "opencv"  # 测试固定用 opencv，快且不依赖 iopaint 安装
    appserver.Handler.cfg = cfg
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), appserver.Handler)
    aport = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)

    # 健康检查
    h = requests.get(f"http://127.0.0.1:{aport}/health", timeout=5).json()
    record("D1.服务健康检查", h.get("ok") is True, json.dumps(h, ensure_ascii=False))

    # 全链路 POST（异步：立即返回 task_id，再轮询 /status 直到完成）
    payload = {"name": "测试商品/带非法字符*", "urls": urls,
               "source_url": "https://detail.1688.com/offer/123.html"}
    post = requests.post(f"http://127.0.0.1:{aport}/process", json=payload, timeout=10).json()
    tid = post.get("task_id")
    record("D3.任务立即受理(异步)", post.get("ok") and tid,
           f"POST 立即返回 task_id={tid}")
    r = {}
    for _ in range(120):
        r = requests.get(f"http://127.0.0.1:{aport}/status?id={tid}", timeout=5).json()
        if r.get("done"):
            break
        time.sleep(0.5)

    passed = (r.get("ok") and r.get("downloaded") == len(urls))
    record("C.带Referer下载全部图", passed,
           f"下载 {r.get('downloaded')}/{r.get('total')}，失败 {r.get('failed')}")

    clean_dir = r.get("clean_dir")
    n_clean = len([f for f in os.listdir(clean_dir)
                   if not f.startswith("_")]) if clean_dir and os.path.isdir(clean_dir) else 0
    record("D2.全链路产物落地", n_clean == len(urls),
           f"clean/ 下 {n_clean} 张，输出目录: {r.get('out_dir')}")

    static.shutdown()
    httpd.shutdown()
    return r


def main():
    if not os.path.isdir(os.path.join(FIX, "wm")):
        print("缺少测试图，请先运行 make_fixtures.py")
        sys.exit(1)
    metrics = test_detect_and_remove()
    svc = test_download_and_service()

    passed = sum(1 for c in RESULTS["cases"] if c["pass"])
    total = len(RESULTS["cases"])
    RESULTS["summary"] = {"passed": passed, "total": total, "metrics": metrics}
    RESULTS["service_result"] = {k: svc.get(k) for k in
                                 ("downloaded", "total", "failed", "dewatermark", "out_dir")}
    with open(os.path.join(HERE, "test_result.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print(f"\n==== 结果 {passed}/{total} 通过 ====")
    print("详细结果已写入 tests/test_result.json")
    sys.exit(0 if passed == total else 2)


if __name__ == "__main__":
    main()
