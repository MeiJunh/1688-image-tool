"""跨平台图片读写：兼容 Windows 上的中文/非 ASCII 路径。

OpenCV 的 cv2.imread / cv2.imwrite 在 Windows 上用 ANSI 编码处理路径，
遇到中文目录名会静默失败（返回 None / 不写出）。
这里改用 numpy.fromfile + cv2.imdecode 读、cv2.imencode + tofile 写，
Python 的文件层能正确处理 Unicode 路径，从而绕过该问题。
"""
import numpy as np
import cv2


def imread(path, flags=cv2.IMREAD_COLOR):
    """读图，兼容中文路径。失败返回 None。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except Exception:  # noqa: BLE001
        return None


def imwrite(path, img):
    """写图，兼容中文路径。成功返回 True。扩展名决定编码格式。"""
    try:
        import os
        ext = os.path.splitext(path)[1]
        if not ext:
            ext = ".png"
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        buf.tofile(path)
        return True
    except Exception:  # noqa: BLE001
        return False
