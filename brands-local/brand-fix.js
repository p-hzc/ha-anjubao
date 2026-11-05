/**
 * 安居宝集成品牌图本地化（适用于 HA < 2026.3）
 *
 * 背景：HA 前端把集成图标的地址写死成
 *   https://brands.home-assistant.io/_/{domain}/icon.png
 * 自定义集成如果没被 brands 仓库收录，CDN 会返回一张 "icon not available"
 * 的占位图，于是「设备与服务」「添加集成」、设备详情页全是灰块。
 *
 * 本地 brand/ 目录要 HA 2026.3.0 才生效，所以在老版本上用这个模块，
 * 在浏览器里把 anjubao 的那张图换成 /local/anjubao/ 下的本地文件。
 *
 * 只处理 URL 里同时含 brands.home-assistant.io 和 /anjubao/ 的图片，
 * 不碰其它集成的图标。HA 升级到 2026.3+ 后本地 brand 生效，
 * 本脚本替换目标不存在，自然失效，可以删掉。
 */
const HOST = "brands.home-assistant.io";
const DOMAIN = "/anjubao/";
const LOCAL = "/local/anjubao/";

const target = (src) =>
  LOCAL + (src.includes("/logo") ? "logo.png" : "icon.png");

function swap(img) {
  let src = img.getAttribute("src") || "";

  // 有些地方只给了 srcset
  if (!src) {
    const srcset = img.getAttribute("srcset") || "";
    if (srcset.includes(HOST) && srcset.includes(DOMAIN)) {
      img.removeAttribute("srcset");
      img.src = target(srcset);
    }
    return;
  }

  // 幂等：已经是本地图就不再动，避免和 MutationObserver 互相触发
  if (src.startsWith(LOCAL)) return;
  if (!src.includes(HOST) || !src.includes(DOMAIN)) return;

  img.removeAttribute("srcset");
  img.src = target(src);
}

function scan(root) {
  if (!root || !root.querySelectorAll) return;
  for (const img of root.querySelectorAll("img")) swap(img);
}

const attached = new WeakSet();

function attach(root, depth = 0) {
  if (!root || attached.has(root) || depth > 12) return;
  attached.add(root);
  scan(root);

  new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "attributes") {
        if (record.target.tagName === "IMG") swap(record.target);
        continue;
      }
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.tagName === "IMG") swap(node);
        else {
          scan(node);
          walk(node, depth + 1);
        }
      }
    }
  }).observe(root, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["src", "srcset"],
  });
}

/** 递归挖 shadow DOM —— HA 的组件大量使用，光扫 document 是扫不到的 */
function walk(node, depth = 0) {
  if (!node || depth > 12) return;
  let all;
  try {
    all = node.querySelectorAll("*");
  } catch (e) {
    return;
  }
  for (const el of all) {
    if (el.shadowRoot) attach(el.shadowRoot, depth + 1);
  }
}

/** 新创建 shadow root 时立刻挂上观察器 */
const nativeAttachShadow = Element.prototype.attachShadow;
if (nativeAttachShadow) {
  Element.prototype.attachShadow = function (init) {
    const root = nativeAttachShadow.call(this, init);
    try {
      attach(root);
    } catch (e) {
      /* 忽略，主流程的定时扫描还会补上 */
    }
    return root;
  };
}

function boot() {
  attach(document);
  walk(document);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}

// HA 是 SPA，路由切换会重建大量节点，补几次即可；之后交给 MutationObserver
for (const delay of [300, 1000, 2500, 5000, 9000]) {
  setTimeout(boot, delay);
}

window.addEventListener("location-changed", () => setTimeout(boot, 400));
window.addEventListener("popstate", () => setTimeout(boot, 400));
