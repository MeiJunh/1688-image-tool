"""去水印(图像修复)引擎。

两个引擎，按配置或可用性自动选择：
- iopaint：基于 LaMa 深度模型，效果最好（推荐，Windows 上用）。
- opencv ：cv2.inpaint(Telea)，无需 torch，安装快、纯本地，作为默认/兜底。
"""
import os
import shutil
import subprocess
import tempfile

import cv2
import numpy as np

import imio
from automask import build_masks


def _iopaint_available():
    return shutil.which("iopaint") is not None


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


def _inpaint_iopaint(image_paths, masks, in_dir, out_dir, device="cpu"):
    """批量调用 iopaint CLI：需要 image 目录与同名 mask 目录。"""
    mask_dir = tempfile.mkdtemp(prefix="mask_")
    try:
        valid = []
        for p in image_paths:
            m = masks.get(p)
            if m is None or m.max() == 0:
                # 无水印的图直接拷到输出，不进模型
                shutil.copy(p, os.path.join(out_dir, os.path.basename(p)))
                continue
            mp = os.path.join(mask_dir, os.path.basename(p))
            imio.imwrite(mp, m)
            valid.append(p)
        if not valid:
            return True, "无需修复的图（未检测到水印）"
        cmd = [
            "iopaint", "run",
            "--model", "lama",
            "--device", device,
            "--image", in_dir,
            "--mask", mask_dir,
            "--output", out_dir,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return False, proc.stderr[-800:] or proc.stdout[-800:]
        return True, "iopaint 完成"
    finally:
        shutil.rmtree(mask_dir, ignore_errors=True)


def run(image_paths, in_dir, out_dir, dw_cfg, host_key=None):
    """对一批图去水印，写入 out_dir。返回 (ok, info, mask_dir)。"""
    os.makedirs(out_dir, exist_ok=True)
    print(f"    [去水印] 生成蒙版中 ({len(image_paths)} 张)...", flush=True)
    masks = build_masks(image_paths, dw_cfg, host_key=host_key)

    # 安全阀：蒙版占比过大(多为误检，如把白底整片当水印)则丢弃，
    # 既避免 cv2.inpaint 修复超大区域时卡死数分钟，也避免擦花背景。
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

    engine = dw_cfg.get("engine", "auto")
    if engine == "auto":
        engine = "iopaint" if _iopaint_available() else "opencv"

    if engine == "iopaint" and _iopaint_available():
        device = resolve_device(dw_cfg)
        print(f"    [去水印] 引擎=iopaint，设备={device}"
              + ("（GPU 加速）" if device in ("cuda", "mps") else "（CPU，较慢）"), flush=True)
        ok, info = _inpaint_iopaint(image_paths, masks, in_dir, out_dir, device=device)
        if ok:
            return True, f"引擎=iopaint(device={device})；{info}", mask_dbg
        # iopaint 失败则回退 opencv
        info_iop = info

    # opencv 路径
    n = 0
    total = len(image_paths)
    for i, p in enumerate(image_paths, 1):
        out_path = os.path.join(out_dir, os.path.basename(p))
        if _inpaint_opencv(p, masks.get(p), out_path):
            n += 1
        if i % 10 == 0 or i == total:
            print(f"    [去水印] {i}/{total} ...", flush=True)
    note = f"引擎=opencv；处理 {n}/{len(image_paths)} 张"
    if engine == "iopaint":
        note += f"（iopaint 不可用/失败，已回退。原因：{info_iop[:200]}）"
    return True, note, mask_dbg
