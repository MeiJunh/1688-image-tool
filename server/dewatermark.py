"""去水印(图像修复)引擎。

两个引擎，按配置或可用性自动选择：
- iopaint：基于 LaMa 深度模型，效果最好（推荐，Windows 上用）。
- opencv ：cv2.inpaint(Telea)，无需 torch，安装快、纯本地，作为默认/兜底。
"""
import os
import importlib.util

import cv2
import numpy as np

import imio
from automask import build_masks

# 只加载一次 iopaint 模型，跨请求复用（加载较慢）
_MODEL_CACHE = {}


def _iopaint_available():
    return importlib.util.find_spec("iopaint") is not None


def _get_iopaint_model(device):
    key = ("lama", device)
    if key not in _MODEL_CACHE:
        import torch
        from iopaint.model_manager import ModelManager
        print(f"    [去水印] 首次加载 iopaint(lama) 模型到 {device}，请稍候...", flush=True)
        _MODEL_CACHE[key] = ModelManager(name="lama", device=torch.device(device))
        print("    [去水印] 模型加载完成", flush=True)
    return _MODEL_CACHE[key]


def resolve_device(dw_cfg):
    """解析去水印使用的计算设备。
    device=auto 时自动探测：有 NVIDIA 显卡(cuda)优先，其次苹果 mps，否则 cpu。
    也可在 config.json 里写死 "cuda" / "cpu" / "mps"。
    """
    dev = str(dw_cfg.get("device", "auto")).lower()
    if dev != "auto":
        return dev
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _inpaint_opencv(image_path, mask, out_path):
    img = imio.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        return False
    if mask is None or mask.max() == 0:
        # 没检测到水印，直接复制原图
        imio.imwrite(out_path, img)
        return True
    if mask.shape[:2] != img.shape[:2]:
        mask = cv2.resize(mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
    res = cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)
    imio.imwrite(out_path, res)
    return True


def _copy_through(p, out_dir):
    """无水印的图原样拷到输出。"""
    img = imio.imread(p, cv2.IMREAD_COLOR)
    if img is not None:
        imio.imwrite(os.path.join(out_dir, os.path.basename(p)), img)


def _inpaint_iopaint(need, masks, out_dir, device, progress=None):
    """用 iopaint(LaMa) Python 接口逐张去水印，带进度。need 为已确认有水印的图列表。"""
    from iopaint.schema import InpaintRequest
    model = _get_iopaint_model(device)
    req = InpaintRequest()
    done = 0
    total = len(need)
    for i, p in enumerate(need, 1):
        name = os.path.basename(p)
        img = imio.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            print(f"    [去水印] iopaint {i}/{total} 跳过(读图失败): {name}", flush=True)
            continue
        m = masks[p]
        if m.shape[:2] != img.shape[:2]:
            m = cv2.resize(m, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        res = model(rgb, m, req)  # 返回 BGR uint8
        imio.imwrite(os.path.join(out_dir, name), res)
        done += 1
        print(f"    [去水印] iopaint {i}/{total} 完成: {name}", flush=True)
        if progress:
            progress(done, total)
    return True, f"iopaint 逐张完成 {done}/{total}"


def run(image_paths, in_dir, out_dir, dw_cfg, host_key=None, progress=None):
    """对一批图去水印，写入 out_dir。返回 (ok, info, mask_dir)。
    progress(done, total)：去水印阶段的逐张进度回调（可选）。
    """
    os.makedirs(out_dir, exist_ok=True)
    print(f"    [去水印] 第1步 生成蒙版中 ({len(image_paths)} 张)...", flush=True)
    masks = build_masks(image_paths, dw_cfg, host_key=host_key)

    # 安全阀：蒙版占比过大(多为误检，如把白底整片当水印)则丢弃，
    # 既避免修复超大区域时卡死，也避免擦花背景。
    max_fill = float(dw_cfg.get("max_fill", 0.25))
    capped = 0
    for p, m in masks.items():
        if m is not None and m.size and (m > 0).sum() / float(m.size) > max_fill:
            masks[p] = np.zeros_like(m)
            capped += 1
    if capped:
        print(f"    [去水印] {capped} 张蒙版占比过大，判为误检已跳过（防卡死）", flush=True)

    # 保存蒙版供人工检查/调参
    mask_dbg = os.path.join(out_dir, "_masks")
    os.makedirs(mask_dbg, exist_ok=True)
    for p, m in masks.items():
        if m is not None:
            imio.imwrite(os.path.join(mask_dbg, os.path.basename(p) + ".png"), m)

    # 分类：哪些检测到水印(需处理) / 哪些没有(直接拷贝)
    need, nowm = [], []
    for p in image_paths:
        m = masks.get(p)
        if m is not None and m.size and int((m > 0).sum()) > 0:
            need.append(p)
        else:
            nowm.append(p)
    print(f"    [去水印] 第2步 检测结果：需去水印 {len(need)} 张，未检测到水印(直接保留) {len(nowm)} 张",
          flush=True)
    for p in need:
        wm_px = int((masks[p] > 0).sum())
        print(f"        ✔ 需处理: {os.path.basename(p)} (水印像素 {wm_px})", flush=True)
    if progress:
        progress(0, len(need))

    # 无水印的图先原样拷贝到 clean/
    for p in nowm:
        _copy_through(p, out_dir)

    if not need:
        return True, "未检测到任何水印，全部原样保留（可调低 edge_percentile 提高灵敏度）", mask_dbg

    engine = dw_cfg.get("engine", "auto")
    if engine == "auto":
        engine = "iopaint" if _iopaint_available() else "opencv"

    print(f"    [去水印] 第3步 开始修复 {len(need)} 张，引擎={engine}", flush=True)
    if engine == "iopaint" and _iopaint_available():
        device = resolve_device(dw_cfg)
        print(f"    [去水印] 设备={device}"
              + ("（GPU 加速）" if device in ("cuda", "mps") else f"（CPU，约每张20-30秒，共约{len(need)}张）"),
              flush=True)
        try:
            ok, info = _inpaint_iopaint(need, masks, out_dir, device, progress=progress)
            return True, f"引擎=iopaint(device={device})；{info}", mask_dbg
        except Exception as e:  # noqa: BLE001
            print(f"    [去水印] iopaint 出错，回退 opencv：{e}", flush=True)

    # opencv 路径（默认或 iopaint 回退）
    total = len(need)
    for i, p in enumerate(need, 1):
        _inpaint_opencv(p, masks.get(p), os.path.join(out_dir, os.path.basename(p)))
        print(f"    [去水印] opencv {i}/{total} 完成: {os.path.basename(p)}", flush=True)
        if progress:
            progress(i, total)
    return True, f"引擎=opencv；处理 {total} 张", mask_dbg
