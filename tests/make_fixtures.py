"""生成测试用合成图：多张不同背景 + 同一位置同一水印。

模拟 1688 同一商品的多张图（水印位置/样式一致），用于验证：
1) 跨图一致性能否自动定位水印；2) 去水印后水印区误差是否明显下降。

产出：
  wm/     带水印的图（喂给工具）
  gt/     无水印的真值背景（评估用）
  truth/  水印真实位置蒙版（评估用）
"""
import os
import cv2
import numpy as np

SIZE = 600
N = 6


def make_background(seed):
    rng = np.random.RandomState(seed)
    img = np.zeros((SIZE, SIZE, 3), np.uint8)
    # 渐变底色
    c1 = rng.randint(40, 220, 3)
    c2 = rng.randint(40, 220, 3)
    for y in range(SIZE):
        t = y / SIZE
        img[y, :] = (c1 * (1 - t) + c2 * t).astype(np.uint8)
    # 随机色块/圆，模拟商品内容（每张不同）
    for _ in range(rng.randint(6, 12)):
        x, y = rng.randint(0, SIZE, 2)
        r = rng.randint(30, 120)
        color = tuple(int(v) for v in rng.randint(0, 255, 3))
        if rng.rand() > 0.5:
            cv2.circle(img, (x, y), r, color, -1)
        else:
            cv2.rectangle(img, (x, y), (x + r, y + r), color, -1)
    # 轻噪声
    noise = rng.randint(-12, 12, img.shape, dtype=np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return img


def watermark_layer():
    """返回 (overlay, alpha)：固定位置的半透明店铺水印。所有图共用。"""
    overlay = np.zeros((SIZE, SIZE, 3), np.uint8)
    a8 = np.zeros((SIZE, SIZE), np.uint8)  # putText 需要 8 位图
    # 底部中间店铺名
    cv2.putText(overlay, "SHOP-1688-OFFICIAL", (60, 520),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(a8, "SHOP-1688-OFFICIAL", (60, 520),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, 255, 3, cv2.LINE_AA)
    # 右上角小 logo 文字
    cv2.putText(overlay, "WM", (500, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 4, cv2.LINE_AA)
    cv2.putText(a8, "WM", (500, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, 255, 4, cv2.LINE_AA)
    alpha = (a8.astype(np.float32) / 255.0) * 0.42  # 半透明强度
    return overlay, alpha


def apply_watermark(bg, overlay, alpha):
    a = alpha[:, :, None]
    out = bg.astype(np.float32) * (1 - a) + overlay.astype(np.float32) * a
    return np.clip(out, 0, 255).astype(np.uint8)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(here, "fixtures")
    for sub in ("wm", "gt", "truth"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    overlay, alpha = watermark_layer()
    truth = (alpha > 0.02).astype(np.uint8) * 255
    truth = cv2.dilate(truth, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))

    for i in range(N):
        bg = make_background(seed=100 + i)
        wm = apply_watermark(bg, overlay, alpha)
        cv2.imwrite(os.path.join(root, "gt", f"{i:02d}.png"), bg)
        cv2.imwrite(os.path.join(root, "wm", f"{i:02d}.png"), wm)
        cv2.imwrite(os.path.join(root, "truth", f"{i:02d}.png"), truth)
    print(f"生成 {N} 组测试图 -> {root}")


if __name__ == "__main__":
    main()
