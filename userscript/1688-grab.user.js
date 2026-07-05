// ==UserScript==
// @name         1688 商品图一键下载+去水印
// @namespace    https://local.1688.tool/
// @version      1.2.0
// @description  在 1688 商品详情页一键抓取全部商品图(主图/SKU/详情图)，发送到本地服务下载并自动去水印(后台异步处理+进度轮询)
// @author       you
// @match        *://*.1688.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  "use strict";

  const SERVER = "http://127.0.0.1:8788/process";
  const MIN_SIDE = 200; // 小于该边长的图当作图标过滤
  const BTN_ID = "wm-grab-btn-1688";

  console.log("[1688-tool] 脚本已加载，当前页:", location.href);

  // ---------- 把 alicdn 缩略图链接还原成原图 ----------
  function normalize(url) {
    if (!url) return null;
    url = String(url).trim();
    if (url.startsWith("//")) url = "https:" + url;
    if (!/^https?:\/\//i.test(url)) return null;
    if (!/alicdn\.com|1688\.com/i.test(url)) return null;
    url = url.split("?")[0];
    url = url.replace(/\.(jpg|jpeg|png|gif)_[^/]*$/i, ".$1");
    url = url.replace(/_\d+x\d+(q\d+)?\.(jpg|jpeg|png|webp)$/i, ".$2");
    return url;
  }

  function collectFromNode(root, set) {
    root.querySelectorAll("img").forEach((img) => {
      const cands = [
        img.currentSrc, img.src,
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
        if (big && big < MIN_SIDE) return;
        set.add(u);
      });
    });
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
    document.querySelectorAll("iframe").forEach((f) => {
      try { if (f.contentDocument) collectFromNode(f.contentDocument, set); } catch (e) {}
    });
    return Array.from(set);
  }

  function autoScroll() {
    return new Promise((resolve) => {
      const step = Math.max(400, window.innerHeight * 0.8);
      let y = 0;
      const timer = setInterval(() => {
        window.scrollTo(0, y);
        y += step;
        if (y >= document.body.scrollHeight + step) {
          clearInterval(timer);
          window.scrollTo(0, 0);
          setTimeout(resolve, 600);
        }
      }, 220);
    });
  }

  function productName() {
    const h = document.querySelector("h1") ||
      document.querySelector(".title-text, .d-title, .offer-title");
    let t = (h && h.textContent) || document.title || "1688商品";
    return t.replace(/\s+/g, " ").trim().slice(0, 60);
  }

  // ---------- 轮询后台任务进度 ----------
  function pollStatus(tid, setStatus, done) {
    const base = SERVER.replace(/\/process$/, "");
    let tries = 0;
    const timer = setInterval(() => {
      tries++;
      if (tries > 2400) { clearInterval(timer); done && done(); return; } // ~2小时上限
      GM_xmlhttpRequest({
        method: "GET",
        url: base + "/status?id=" + tid,
        timeout: 15000,
        onload: (resp) => {
          let s;
          try { s = JSON.parse(resp.responseText); } catch (e) { return; }
          if (s.done) {
            clearInterval(timer);
            if (s.ok) {
              setStatus(`✅ 完成 ${s.downloaded}/${s.total}`, "#0a0");
              console.log("[1688-tool] 输出目录:", s.out_dir, s);
              setTimeout(() => setStatus("⬇ 下载+去水印", "#ff6a00"), 6000);
            } else {
              setStatus("❌ " + (s.error || "失败"), "#c00");
            }
            done && done();
          } else {
            const stage = s.stage || "处理中";
            let txt;
            if (stage === "去水印中") {
              const d = s.wm_done || 0, t = s.wm_total || 0;
              txt = t ? `⏳ 去水印 ${d}/${t} (CPU较慢,请耐心)` : "⏳ 去水印准备中…";
            } else {
              txt = `⏳ ${stage} ${s.downloaded || 0}/${s.total || 0}`;
            }
            setStatus(txt, "#666");
          }
        },
        onerror: () => { /* 偶尔连不上，继续轮询即可 */ },
      });
    }, 3000);
  }

  // ---------- 核心：抓图并发送（后台异步处理 + 轮询进度）----------
  function run(setStatus) {
    return new Promise((resolve) => {
      setStatus("⏳ 正在加载全部图片…", "#666");
      autoScroll().then(() => {
        const urls = collectAll();
        if (!urls.length) {
          setStatus("未找到图片，换个商品页?", "#c00");
          return resolve();
        }
        setStatus(`⏳ 发送 ${urls.length} 张…`, "#666");
        GM_xmlhttpRequest({
          method: "POST",
          url: SERVER,
          headers: { "Content-Type": "application/json" },
          data: JSON.stringify({ name: productName(), urls, source_url: location.href }),
          timeout: 30000,
          onload: (resp) => {
            let r;
            try { r = JSON.parse(resp.responseText); }
            catch (e) { setStatus("❌ 服务返回异常", "#c00"); return resolve(); }
            if (r.ok && r.task_id) {
              setStatus("⏳ 已开始后台处理…", "#666");
              pollStatus(r.task_id, setStatus, resolve);
            } else if (r.ok) {  // 兼容旧版同步返回
              setStatus(`✅ 完成 ${r.downloaded}/${r.total}`, "#0a0");
              resolve();
            } else {
              setStatus("❌ " + (r.error || "失败"), "#c00");
              resolve();
            }
          },
          onerror: () => { setStatus("❌ 连不上本地服务(先启动 server.py)", "#c00"); resolve(); },
          ontimeout: () => { setStatus("❌ 发送超时", "#c00"); resolve(); },
        });
      });
    });
  }

  // ---------- UI：按钮 + 自愈 ----------
  function ensureButton() {
    if (!document.body) return;
    if (document.getElementById(BTN_ID)) return; // 已存在
    const btn = document.createElement("div");
    btn.id = BTN_ID;
    btn.textContent = "⬇ 下载+去水印";
    Object.assign(btn.style, {
      position: "fixed", right: "24px", bottom: "90px", zIndex: 2147483647,
      background: "#ff6a00", color: "#fff", padding: "12px 18px",
      borderRadius: "24px", fontSize: "15px", fontWeight: "600",
      cursor: "pointer", boxShadow: "0 4px 14px rgba(0,0,0,.25)",
      userSelect: "none", fontFamily: "system-ui, sans-serif", lineHeight: "1",
    });
    const setStatus = (msg, color) => {
      btn.textContent = msg;
      if (color) btn.style.background = color;
    };
    btn.addEventListener("click", async () => {
      if (btn.dataset.busy) return;
      btn.dataset.busy = "1";
      try { await run(setStatus); } finally { delete btn.dataset.busy; }
    });
    document.body.appendChild(btn);
    console.log("[1688-tool] 按钮已注入右下角");
  }

  // 动态页面(SPA)会重渲染，定时确保按钮在
  ensureButton();
  setInterval(ensureButton, 1500);

  // 备用入口：Tampermonkey 图标菜单里也能触发
  if (typeof GM_registerMenuCommand === "function") {
    GM_registerMenuCommand("⬇ 下载本页图片+去水印", () => {
      const b = document.getElementById(BTN_ID);
      run((m, c) => { if (b) { b.textContent = m; if (c) b.style.background = c; } });
    });
  }
})();
