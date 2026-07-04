# 1688 商品图批量下载 + 自动去水印

仓库地址：https://github.com/MeiJunh/1688-image-tool

在 1688 商品详情页点一个按钮 → 自动抓取本页所有商品图（主图 / SKU / 详情长图）→ 本地下载并自动去水印 → 存到文件夹。

## 结构（两段式）

```
[浏览器] 油猴脚本 userscript/1688-grab.user.js
    抓取当前商品页所有图片链接，发给本地服务
        │  HTTP POST http://127.0.0.1:8788/process
        ▼
[本地] Python 服务 server/server.py
    带 Referer 下载原图 → 自动去水印 → 存到 output/商品名_时间/
        ├─ original/   下载的原图
        ├─ clean/      去水印后的成品
        └─ clean/_masks/  自动识别出的水印蒙版（调参时看）
```

- 浏览器脚本：JavaScript（Tampermonkey 只能跑 JS）
- 本地服务：Python（IOPaint / OpenCV 都是 Python 生态）

## 去水印怎么做到"自动"

1688 同一商品的多张图，水印位置/样式通常一致。工具对一组图做**跨图一致性分析**：
水印在每张图的相同位置产生相同边缘，叠加统计后"几乎每张图都出现在同位置"的边缘就是水印，
据此自动生成蒙版，再用图像修复填补。**不需要你手动标注**。

去水印引擎两选一（`config.json` 的 `engine`）：
- `opencv`：`cv2.inpaint`，无需大模型，安装快，默认。
- `iopaint`：基于 LaMa 深度模型，效果更好。装了 `pip install iopaint` 后设 `engine=auto` 会自动优先用它。

## 快速开始（Windows）

详见 `docs/使用手册.md`。三步：
1. 装 Python（勾选 Add to PATH）→ `cd server && pip install -r requirements.txt`
2. `python server.py` 启动本地服务（用时保持窗口开启）
3. Chrome/Edge 装 Tampermonkey，导入 `userscript/1688-grab.user.js`，在 1688 商品页点右下角按钮

## 测试

```
python tests/make_fixtures.py     # 生成合成测试图
python tests/test_pipeline.py     # 端到端测试，结果写入 tests/test_result.json
```
测试报告见 `docs/测试报告.md`。

## 目录

```
server/        本地服务（Python）
  server.py        HTTP 服务入口
  downloader.py    带 Referer 并发下载
  automask.py      自动生成水印蒙版
  dewatermark.py   去水印引擎（opencv / iopaint）
  config.json      配置
  requirements.txt 依赖
userscript/    油猴脚本（JavaScript）
tests/         测试
docs/          使用手册 + 测试报告
output/        处理结果（运行后生成）
```

## 合规提醒

去水印涉及版权。请仅对**你有授权的图**（自有/供应商授权/自拍）使用。扒他人品牌图去水印再商用，在海外平台有被投诉下架乃至封店风险，请自行把关。
