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

    # 读取尺寸并按宽高比分组
    metas = []
    for p in image_paths:
        img = imio.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            metas.append((p, None))
            continue
        h, w = img.shape[:2]
        metas.append((p, (w, h)))

    groups = defaultdict(list)
    for p, wh in metas:
        if wh is None:
            continue
        groups[_aspect_bucket(*wh)].append((p, wh))

    masks = {}
    for _, members in groups.items():
        paths = [m[0] for m in members]
        if len(paths) < min_group:
            for p in paths:
                masks[p] = None  # 组太小，交给兜底策略
            continue
        # 统一到该组的中位尺寸
        ws = sorted(m[1][0] for m in members)
        hs = sorted(m[1][1] for m in members)
        size = (ws[len(ws) // 2], hs[len(hs) // 2])

        # 关键思路：对每张图取"强边缘"二值图，统计每个像素在多少张图里都是强边缘。
        # 水印固定位置 → 几乎每张图都有强边缘（presence 高）；
        # 商品内容边缘 → 各图位置不同 → presence 低。据此区分，避免误框内容。
        presence = np.zeros((size[1], size[0]), dtype=np.float32)
        n = 0
        for p in paths:
            g = _load_gray(p, size)
            if g is None:
                continue
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
            mag = cv2.magnitude(gx, gy)
            # 该图自身的强边缘阈值（分位），二值化
            t = np.percentile(mag, pct)
            presence += (mag >= t).astype(np.float32)
            n += 1
        if n == 0:
            for p in paths:
                masks[p] = None
            continue
        presence /= n  # 0..1：该位置强边缘出现在多少比例的图里
        # 出现在 ≥ (n-1)/n 或至少 80% 的图里，才判为水印
        need = max(0.8, (n - 1.0) / n - 1e-6)
        raw = (presence >= need).astype(np.uint8) * 255
        # 形态学：连通 + 膨胀，覆盖笔画间隙和边缘外沿
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
        raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, k)
        raw = cv2.dilate(raw, k, iterations=1)
        # 该组蒙版按各图自身尺寸缩放回去
        for p in paths:
            img = imio.imread(p, cv2.IMREAD_COLOR)
            if img is None:
                masks[p] = None
                continue
            h, w = img.shape[:2]
            masks[p] = cv2.resize(raw, (w, h), interpolation=cv2.INTER_NEAREST)
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


def _empty_mask(path):
    img = imio.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    return np.zeros((h, w), dtype=np.uint8)


def build_masks(image_paths, dw_cfg, host_key=None):
    """根据配置为所有图生成蒙版。返回 {path: mask}。"""
    strategy = dw_cfg.get("mask_strategy", "consistency")
    regions = dw_cfg.get("regions", {})

    # region 优先：若为当前店铺/域名配了固定区域，直接用
    rects = regions.get(host_key) if host_key else None
    if strategy == "region" or rects:
        if not rects:
            rects = regions.get("default")
        if rects:
            return {p: region_mask(p, rects) for p in image_paths}

    if strategy == "consistency":
        masks = consistency_masks(image_paths, dw_cfg.get("consistency", {}))
        # 对一致性无法覆盖(组太小)的图，用启发式兜底
        for p in image_paths:
            if masks.get(p) is None:
                masks[p] = heuristic_mask(p, dw_cfg.get("heuristic", {})) or _empty_mask(p)
        return masks

    if strategy == "heuristic":
        return {p: (heuristic_mask(p, dw_cfg.get("heuristic", {})) or _empty_mask(p))
                for p in image_paths}

    # none / 未知
    return {p: _empty_mask(p) for p in image_paths}
