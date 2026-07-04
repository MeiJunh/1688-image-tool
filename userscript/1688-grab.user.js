// ==UserScript==
// @name         1688 商品图一键下载+去水印
// @namespace    https://local.1688.tool/
// @version      1.0.0
// @description  在 1688 商品详情页一键抓取全部商品图(主图/SKU/详情图)，发送到本地服务下载并自动去水印
// @author       you
// @match        *://detail.1688.com/*
// @match        *://*.1688.com/offer/*
// @match        *://m.1688.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const SERVER = "http://127.0.0.1:8788/process";
  const MIN_SIDE = 200; // 小于该边长的图当作图标过滤掉（凭 naturalWidth/Height）

  // ---------- 把 alicdn 缩略图链接还原成原图 ----------
  function normalize(url) {
    if (!url) return null;
    url = url.trim();
    if (url.startsWith("//")) url = "https:" + url;
    if (!/^https?:\/\//i.test(url)) return null;
    // 只要 alicdn / 1688 图片 CDN
    if (!/alicdn\.com|1688\.com/i.test(url)) return null;
    // 去掉查询串
    url = url.split("?")[0];
    // 去掉尺寸后缀： xxx.jpg_310x310.jpg / xxx.jpg_.webp / xxx.jpg_q90.jpg 等
    url = url.replace(/\.(jpg|jpeg|png|gif)_[^/]*$/i, ".$1");
    // 去掉形如 _960x960q80.jpg 直接跟在文件名后的情况
    url = url.replace(/_\d+x\d+(q\d+)?\.(jpg|jpeg|png|webp)$/i, ".$1"); // 谨慎：仅当剩余仍是图片
    return url;
  }

  // ---------- 收集页面上所有商品图 ----------
  function collectFromNode(root, set) {
    // img 标签的多种懒加载属性
    const imgs = root.querySelectorAll("img");
    imgs.forEach((img) => {
      const cands = [
        img.currentSrc,
        img.src,
        img.getAttribute("data-src"),
        img.getAttribute("data-lazy-src"),
        img.getAttribute("data-ks-lazyload"),
        img.getAttribute("data-original"),
      ];
      let big = 0;
      if (img.naturalWidth) big = Math.min(img.naturalWidth, img.naturalHeight);
      cands.forEach((c) => {
        const u = normalize(c);
        if (!u) return;
        // 无法判断尺寸时也收（详情图常未渲染），能判断则过滤小图标
        if (big && big < MIN_SIDE) return;
        set.add(u);
      });
    });
    // 背景图
    root.querySelectorAll("[style*='background']").forEach((el) => {
      const m = (el.style.backgroundImage || "").match(/url\(["']?(.*?)["']?\)/i);
      if (m) {
        const u = normalize(m[1]);
        if (u) set.add(u);
      }
    });
  }

  function collectAll() {
    const set = new Set();
    collectFromNode(document, set);
    // 详情描述常在 iframe 里
    document.querySelectorAll("iframe").forEach((f) => {
      try {
        if (f.contentDocument) collectFromNode(f.contentDocument, set);
      } catch (e) {
        /* 跨域 iframe 读不到，忽略 */
      }
    });
    return Array.from(set);
  }

  // ---------- 自动滚动，触发详情长图懒加载 ----------
  function autoScroll() {
    return new Promise((resolve) => {
      const step = Math.max(400, window.innerHeight * 0.8);
      let y = 0;
      const maxY = () => document.body.scrollHeight;
      const timer = setInterval(() => {
        window.scrollTo(0, y);
        y += step;
        if (y >= maxY() + step) {
          clearInterval(timer);
          window.scrollTo(0, 0);
          setTimeout(resolve, 600);
        }
      }, 220);
    });
  }

  function productName() {
    const h =
      document.querySelector("h1") ||
      document.querySelector(".title-text, .d-title, .offer-title");
    let t = (h && h.textContent) || document.title || "1688商品";
    return t.replace(/\s+/g, " ").trim().slice(0, 60);
  }

  // ---------- UI ----------
  function makeBtn() {
    const btn = document.createElement("div");
    btn.textContent = "⬇ 下载+去水印";
    Object.assign(btn.style, {
      position: "fixed",
      right: "24px",
      bottom: "90px",
      zIndex: 999999,
      background: "#ff6a00",
      color: "#fff",
      padding: "12px 18px",
      borderRadius: "24px",
      fontSize: "15px",
      fontWeight: "600",
      cursor: "pointer",
      boxShadow: "0 4px 14px rgba(0,0,0,.25)",
      userSelect: "none",
      fontFamily: "system-ui, sans-serif",
    });
    document.body.appendChild(btn);

    const toast = (msg, color) => {
      btn.textContent = msg;
      btn.style.background = color || "#ff6a00";
    };

    btn.addEventListener("click", async () => {
      if (btn.dataset.busy) return;
      btn.dataset.busy = "1";
      toast("⏳ 正在加载全部图片…", "#666");
      await autoScroll();
      const urls = collectAll();
      if (!urls.length) {
        toast("未找到图片，换个页面?", "#c00");
        delete btn.dataset.busy;
        return;
      }
      toast(`⏳ 发送 ${urls.length} 张，处理中…`, "#666");
      GM_xmlhttpRequest({
        method: "POST",
        url: SERVER,
        headers: { "Content-Type": "application/json" },
        data: JSON.stringify({
          name: productName(),
          urls: urls,
          source_url: location.href,
        }),
        timeout: 300000,
        onload: (resp) => {
          delete btn.dataset.busy;
          try {
            const r = JSON.parse(resp.responseText);
            if (r.ok) {
              toast(`✅ 完成 ${r.downloaded}/${r.total}`, "#0a0");
              console.log("[1688-tool] 输出目录:", r.out_dir, r);
              setTimeout(() => toast("⬇ 下载+去水印"), 4000);
            } else {
              toast("❌ " + (r.error || "失败"), "#c00");
            }
          } catch (e) {
            toast("❌ 服务返回异常", "#c00");
          }
        },
        onerror: () => {
          delete btn.dataset.busy;
          toast("❌ 连不上本地服务(先启动 server.py)", "#c00");
        },
        ontimeout: () => {
          delete btn.dataset.busy;
          toast("❌ 处理超时", "#c00");
        },
      });
    });
  }

  if (document.body) makeBtn();
  else window.addEventListener("DOMContentLoaded", makeBtn);
})();
