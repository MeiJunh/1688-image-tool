# 用显卡(GPU)加速去水印

iopaint 用 NVIDIA 显卡跑 LaMa，比 CPU 快很多（一张从约 25 秒降到约 1 秒）。
代码已支持自动选设备（`config.json` 的 `"device": "auto"` 会优先用显卡），
但 Windows 上 `pip install iopaint` 默认装的是 **CPU 版 torch**，必须换成 **CUDA 版** 才能真正用上显卡。

> 仅支持 **NVIDIA 显卡**。AMD / Intel 核显用不了 CUDA，维持 CPU 即可。

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
