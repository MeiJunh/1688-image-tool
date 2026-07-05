# 用显卡(GPU)加速去水印

iopaint 用 NVIDIA 显卡跑 LaMa，比 CPU 快很多（一张从约 25 秒降到约 1 秒）。
代码已支持自动选设备（`config.json` 的 `"device": "auto"` 会优先用显卡），
但 Windows 上 `pip install iopaint` 默认装的是 **CPU 版 torch**，必须换成 **CUDA 版** 才能真正用上显卡。

> ⚠️ **只有 NVIDIA 显卡能加速。** 下面的 CUDA 步骤仅适用于 N 卡。

## 显卡兼容性（先看这里）

| 显卡 | Windows 上能否加速 iopaint | 怎么办 |
|---|---|---|
| **NVIDIA(N卡)** | ✅ 能，用 CUDA | 按下面步骤换 CUDA 版 torch |
| **AMD(A卡，如 RX 6600XT)** | ❌ 不能 | 只能用 CPU（见下） |
| **Intel 核显** | ❌ 不能 | 只能用 CPU |

**为什么 A 卡不行**：PyTorch 的 GPU 加速要么靠 CUDA（仅 N 卡），要么靠 ROCm（AMD 方案，但**只在 Linux 上支持，Windows 没有**）。
DirectML 虽能让 A 卡跑 PyTorch，但 iopaint 未适配，接不通。所以 **AMD / Intel 在 Windows 上只能 CPU 跑 iopaint**。

### AMD / Intel / 无独显 用户怎么办

`config.json` 的 `"device"` 保持 `"auto"` 即可（会自动落到 CPU），**不要**去装 CUDA 版 torch（装了也用不上）。加速思路改为「少跑 iopaint」：

- **只有检测到水印的图才会走 iopaint**，没水印的直接跳过 —— 所以一批里真正慢的只有那几张。
- iopaint 对**小水印**（角标、URL）其实不慢（它只处理水印周围一小块）；慢的是**大面积水印**（如整条居中大字）。
- 追求速度：把 `engine` 设成 `"opencv"`（秒级，质量稍差）；追求质量：设 `"iopaint"`（CPU 慢但干净）。可按商品重要性切换。
- 详情长图很多时，`consistency.analysis_max_side` 已把分析降采样，检测不会慢；慢只慢在 iopaint 修复本身。

---

## 以下步骤仅 NVIDIA 显卡适用

## 步骤

### 1. 确认是 NVIDIA 显卡、看 CUDA 版本
命令行执行：
```bat
nvidia-smi
```
- 能出一张表 → 是 NVIDIA 显卡。看右上角 `CUDA Version: 12.x`（记住大版本，12 或 11）。
- 报"不是内部命令"/没有 → 不是 NVIDIA 或没装驱动，用不了 GPU，保持 CPU。

### 2. 把 CPU 版 torch 换成 CUDA 版
在项目虚拟环境里（先 `.venv\Scripts\activate`）：
```bat
pip uninstall -y torch torchvision
```
然后按第 1 步看到的 CUDA 版本二选一：
```bat
:: CUDA 12.x（较新驱动，绝大多数选这个）
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

:: 或 CUDA 11.x
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```
这个包较大（约 2.5GB），下载需要等一会儿。

### 3. 验证显卡可用
```bat
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```
打印 `True 你的显卡型号` 就成功了。若是 `False`，说明装的还是 CPU 版或驱动/CUDA 不匹配，回到第 2 步换版本。

### 4. 启动即可
`config.json` 的 `"device"` 保持 `"auto"`，重启 `python server.py`。
处理时黑窗口会打印：
```
[去水印] 引擎=iopaint，设备=cuda（GPU 加速）
```
看到 `设备=cuda` 就说明在用显卡了。

## 配置说明

`config.json` 的 `dewatermark.device`：
- `"auto"`（默认）：有 NVIDIA 显卡自动用 `cuda`，否则 `cpu`
- `"cuda"`：强制用显卡（没配好会报错）
- `"cpu"`：强制用 CPU

## 常见问题

**装完 torch 后 `torch.cuda.is_available()` 还是 False**
→ 多半是装成了 CPU 版。确认第 2 步用了 `--index-url .../cu121`（或 cu118），且先卸载了旧 torch。

**跑起来报显存不足(out of memory)**
→ 详情长图很大时可能爆显存。可在 `config.json` 把 `consistency.analysis_max_side` 调小，
或对超大图维持 CPU。一般 6GB 以上显存没问题。

**装了 CUDA 版 torch，但想临时用 CPU**
→ 把 `device` 改成 `"cpu"` 重启即可，不用重装。
