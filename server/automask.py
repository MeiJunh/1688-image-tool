"""自动生成水印蒙版（白色=需要去除的区域）。

三种策略：
- consistency：跨图一致性。1688 同一商品的多张图水印位置/样式通常一致，
  水印会在每张图的相同位置产生"相同的边缘结构"，把所有图的边缘图叠加取平均，
  水印边缘会被反复强化，而商品内容边缘会互相抵消 —— 阈值化即得到水印蒙版。
  这是本工具默认、且最适合 1688 的免费自动方案。
- region：固定矩形。对常买的固定店铺，手动配一次水印位置(相对比例)，最准。
- heuristic：单图启发式。检测半透明的浅色/灰色文字，兜底用，效果一般。
"""
import os
from collections import defaultdict

import cv2
import numpy as np

import imio


def _aspect_bucket(w, h):
    """按宽高比分组：主图(近方形)、竖长图、横图 会被分开处理。"""
    r = w / float(h)
    return round(r, 1)


def _load_gray(path, size):
    img = imio.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def consistency_masks(image_paths, params):
    """跨图一致性生成蒙版。返回 {path: mask(uint8, 0/255)}。

    对无法分组(数量不足 min_group)的图，返回全黑蒙版（即不处理）。
    """
    min_group = int(params.get("min_group", 3))
    pct = float(params.get("edge_percentile", 96.5))
    dilate = int(params.get("dilate", 6))
    max_side = int(params.get("analysis_max_side", 1000))  # 分析降采样上限，防大图卡死/爆内存

    # 每张图只解码一次：记录 原始尺寸 + 降采样后的灰度图（大图在此被缩小，随后释放原图）
    entries = []  # (path, (w,h), gray_small)
    total = len(image_paths)
    for idx, p in enumerate(image_paths, 1):
        img = imio.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            entries.append((p, None, None))
        else:
            h, w = img.shape[:2]
            scale = min(1.0, max_side / float(max(h, w)))
            gw, gh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
            gray = cv2.cvtColor(cv2.resize(img, (gw, gh), interpolation=cv2.INTER_AREA),
                                cv2.COLOR_BGR2GRAY)
            entries.append((p, (w, h), gray))
        del img  # 及时释放，峰值内存只占单张
        if idx % 20 == 0 or idx == total:
            print(f"    [去水印] 读图分析 {idx}/{total} ...", flush=True)

    groups = defaultdict(list)  # aspect_bucket -> [entry index]
    for i, (p, wh, gray) in enumerate(entries):
        if wh is not None:
            groups[_aspect_bucket(*wh)].append(i)

    masks = {}
    for _, idxs in groups.items():
        if len(idxs) < min_group:
            for i in idxs:
                masks[entries[i][0]] = None  # 组太小，交给兜底策略
            continue
        # 统一到该组降采样灰度的中位尺寸（都是小图，计算快、内存小）
        gws = sorted(entries[i][2].shape[1] for i in idxs)
        ghs = sorted(entries[i][2].shape[0] for i in idxs)
        asize = (gws[len(gws) // 2], ghs[len(ghs) // 2])

        # 对每张图取"强边缘"二值图，统计每个像素在多少张图里都是强边缘。
        # 水印固定位置 → 几乎每张图都有强边缘；商品内容边缘 → 各图位置不同 → 少。
        presence = np.zeros((asize[1], asize[0]), dtype=np.float32)
        n = 0
        for i in idxs:
            g = cv2.resize(entries[i][2], asize, interpolation=cv2.INTER_AREA)
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
            mag = cv2.magnitude(gx, gy)
            t = np.percentile(mag, pct)
            presence += (mag >= t).astype(np.float32)
            n += 1
        presence /= max(1, n)
        need = max(0.8, (n - 1.0) / n - 1e-6)
        raw = (presence >= need).astype(np.uint8) * 255
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
        raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, k)
        raw = cv2.dilate(raw, k, iterations=1)
        # 组内蒙版(小图)放大回各图自身原始尺寸
        for i in idxs:
            w, h = entries[i][1]
            masks[entries[i][0]] = cv2.resize(raw, (w, h), interpolation=cv2.INTER_NEAREST)
    return masks


def heuristic_mask(path, params):
    """单图启发式：找半透明浅色/低饱和文字水印。兜底用。"""
    img = imio.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    white_thresh = int(params.get("white_thresh", 205))
    sat_max = int(params.get("sat_max", 40))
    mask = ((v >= white_thresh) & (s <= sat_max)).astype(np.uint8) * 255
    dilate = int(params.get("dilate", 4))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return mask


def region_mask(path, rects):
    """固定矩形蒙版。rects 为 [[x0,y0,x1,y1], ...]，取值 0..1 的相对比例。"""
    img = imio.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for x0, y0, x1, y1 in rects:
        cv2.rectangle(
            mask,
            (int(x0 * w), int(y0 * h)),
            (int(x1 * w), int(y1 * h)),
            255,
            thickness=-1,
        )
    return mask


def region_stroke_mask(path, rects, params):
    """在固定区域内检测水印笔画（半透明浅色文字）→ 生成贴合笔画的细蒙版。

    适合 1688 这种"固定位置的半透明文字水印"：只在水印可能出现的区域(中间/底部)里
    找比周围亮的笔画像素，区域外一律不动；没水印的图那块找不到笔画 → 近乎空蒙版 → 自动跳过。
    比"跨图一致性"可靠得多（不依赖多图、也不怕半透明弱边缘）。
    """
    img = imio.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    region = np.zeros((h, w), np.uint8)
    for x0, y0, x1, y1 in rects:
        cv2.rectangle(region, (int(x0 * w), int(y0 * h)),
                      (int(x1 * w), int(y1 * h)), 255, -1)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 白帽变换(top-hat)：提取"比背景亮、且小于结构元"的结构=水印笔画。
    # 关键优点：均匀白底/灰底 top-hat≈0 不会触发（比自适应阈值更少误报），
    # 同时能抓到压在深色/浅色上的亮笔画。kernel 要略大于笔画宽度。
    k = int(params.get("kernel", 21)) | 1
    thresh = int(params.get("tophat_thresh", 12))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    th = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, ker)
    cand = ((th > thresh) * 255).astype(np.uint8)
    # 可选：叠加"高亮低饱和"补捕极淡的白字。默认关闭——会把整片白/灰底误当水印。
    if bool(params.get("use_white", False)):
        v_min = int(params.get("v_min", 150))
        s_max = int(params.get("s_max", 60))
        white = ((hsv[:, :, 2] > v_min) & (hsv[:, :, 1] < s_max)).astype(np.uint8) * 255
        cand = cv2.bitwise_or(cand, white)
    mask = cv2.bitwise_and(cand, region)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    dilate = int(params.get("dilate", 5))
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate)))
    return mask


def _empty_mask(path):
    img = imio.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    return np.zeros((h, w), dtype=np.uint8)


def _heuristic_or_empty(path, params):
    """启发式蒙版；拿不到就返回全黑蒙版。不能用 `a or b`（数组真值判断会报错）。"""
    m = heuristic_mask(path, params)
    return m if m is not None else _empty_mask(path)


def build_masks(image_paths, dw_cfg, host_key=None):
    """根据配置为所有图生成蒙版。返回 {path: mask}。"""
    strategy = dw_cfg.get("mask_strategy", "consistency")
    regions = dw_cfg.get("regions", {})

    def _rects_for_host():
        r = regions.get(host_key) if host_key else None
        return r or regions.get("default")

    # region_stroke：固定区域内抓水印笔画（推荐用于半透明文字水印）
    if strategy == "region_stroke":
        rects = _rects_for_host()
        if rects:
            rp = dw_cfg.get("region_stroke", {})
            return {p: region_stroke_mask(p, rects, rp) for p in image_paths}
        print("    [去水印] region_stroke 未配置 regions，退回一致性检测", flush=True)

    # region：固定矩形整块填充（用于不透明水印）
    rects = regions.get(host_key) if host_key else None
    if strategy == "region" or (rects and strategy != "consistency"):
        if not rects:
            rects = regions.get("default")
        if rects:
            return {p: region_mask(p, rects) for p in image_paths}

    if strategy == "consistency":
        masks = consistency_masks(image_paths, dw_cfg.get("consistency", {}))
        # 对一致性无法覆盖(组太小)的图的兜底策略：
        # 默认 fallback_heuristic=False —— 用空蒙版(跳过)，因为启发式会把白底整片误判为水印。
        use_heuristic = bool(dw_cfg.get("fallback_heuristic", False))
        for p in image_paths:
            if masks.get(p) is None:
                masks[p] = (_heuristic_or_empty(p, dw_cfg.get("heuristic", {}))
                            if use_heuristic else _empty_mask(p))
        return masks

    if strategy == "heuristic":
        return {p: _heuristic_or_empty(p, dw_cfg.get("heuristic", {}))
                for p in image_paths}

    # none / 未知
    return {p: _empty_mask(p) for p in image_paths}
