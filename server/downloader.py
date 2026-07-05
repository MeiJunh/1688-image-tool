"""下载模块：带 Referer / User-Agent 抓取 1688 图片原图，绕过防盗链。"""
import os
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# 常见图片扩展名
_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|bmp)$", re.IGNORECASE)


def _guess_ext(url, content_type):
    m = _EXT_RE.search(url.split("?")[0])
    if m:
        return "." + m.group(1).lower().replace("jpeg", "jpg")
    if content_type:
        ct = content_type.lower()
        if "png" in ct:
            return ".png"
        if "webp" in ct:
            return ".webp"
        if "gif" in ct:
            return ".gif"
    return ".jpg"


def download_one(url, out_dir, index, cfg):
    """下载单张图，返回 (url, saved_path 或 None, error 或 None)。"""
    headers = {
        "User-Agent": cfg["user_agent"],
        "Referer": cfg["referer"],
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
    }
    try:
        r = requests.get(url, headers=headers, timeout=cfg["timeout"], stream=True)
        r.raise_for_status()
        data = r.content
        if len(data) < cfg.get("min_bytes", 0):
            return (url, None, f"文件过小({len(data)}B)，疑似图标或失效")
        ext = _guess_ext(url, r.headers.get("Content-Type", ""))
        # 用 序号 + url 哈希 命名，避免重名且可追溯
        short = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        name = f"{index:03d}_{short}{ext}"
        path = os.path.join(out_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return (url, path, None)
    except Exception as e:  # noqa: BLE001
        return (url, None, str(e))


def download_all(urls, out_dir, cfg):
    """并发下载所有 url 到 out_dir。返回 {saved:[...], errors:[{url,error}]}。"""
    os.makedirs(out_dir, exist_ok=True)
    saved, errors = [], []
    workers = max(1, int(cfg.get("max_workers", 4)))
    total = len(urls)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(download_one, url, out_dir, i, cfg): url
            for i, url in enumerate(urls)
        }
        for fut in as_completed(futs):
            url, path, err = fut.result()
            if path:
                saved.append(path)
            else:
                errors.append({"url": url, "error": err})
            done += 1
            if done % 10 == 0 or done == total:
                print(f"    [下载] {done}/{total} ...", flush=True)
    saved.sort()  # 按序号命名，排序即恢复原顺序
    return {"saved": saved, "errors": errors}
