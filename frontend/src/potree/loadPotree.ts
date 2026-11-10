/**
 * 按需加载 Potree 1.8.2 全局脚本与样式。
 *
 * Potree 以传统 UMD 方式发布，`potree.js` 依赖 `document.currentScript` 推导
 * `Potree.scriptPath`，再据此加载 workers/ 与 resources/，因此不能用打包器
 * 处理，必须在运行时用 <script> 标签按固定顺序注入。
 *
 * 挂在 public/vendor/potree 下的资源不参与构建（.gitignore 已忽略），
 * Vite dev 与 Nginx 都按同名路径访问。
 */

const CSS_HREFS = [
  "/vendor/potree/libs/jquery-ui/jquery-ui.min.css",
  "/vendor/potree/libs/spectrum/spectrum.css",
  "/vendor/potree/libs/jstree/themes/mixed/style.min.css",
  "/vendor/potree/build/potree/potree.css",
];

/** 顺序与 potree.html 保持一致：potree.js 依赖前三个库的全局变量。 */
const SCRIPT_SRCS = [
  "/vendor/potree/libs/jquery/jquery-3.1.1.min.js",
  "/vendor/potree/libs/spectrum/spectrum.js",
  "/vendor/potree/libs/jquery-ui/jquery-ui.min.js",
  "/vendor/potree/libs/other/BinaryHeap.js",
  "/vendor/potree/libs/tween/tween.min.js",
  "/vendor/potree/libs/d3/d3.js",
  "/vendor/potree/libs/proj4/proj4.js",
  "/vendor/potree/libs/i18next/i18next.js",
  "/vendor/potree/libs/jstree/jstree.min.js",
  "/vendor/potree/build/potree/potree.js",
];

let assetsPromise: Promise<void> | null = null;

function injectLink(href: string): void {
  if (document.querySelector(`link[data-potree-asset="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.dataset.potreeAsset = href;
  document.head.appendChild(link);
}

function injectScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[data-potree-asset="${src}"]`,
    );
    if (existing) {
      if (existing.dataset.loaded === "1") resolve();
      else {
        existing.addEventListener("load", () => resolve(), { once: true });
        existing.addEventListener(
          "error",
          () => reject(new Error(`Potree 脚本加载失败：${src}`)),
          { once: true },
        );
      }
      return;
    }

    const script = document.createElement("script");
    script.src = src;
    script.dataset.potreeAsset = src;
    script.onload = () => {
      script.dataset.loaded = "1";
      resolve();
    };
    script.onerror = () => reject(new Error(`Potree 脚本加载失败：${src}`));
    document.head.appendChild(script);
  });
}

/**
 * 确保 Potree 全局可用；重复调用复用同一个 Promise。
 *
 * 失败时抛出原始错误（一般是 /vendor/potree 未挂载），由调用方展示缺资源提示。
 */
export function ensurePotreeAssets(): Promise<void> {
  if (typeof window !== "undefined" && (window as { Potree?: unknown }).Potree) {
    return Promise.resolve();
  }
  if (assetsPromise) return assetsPromise;

  assetsPromise = (async () => {
    CSS_HREFS.forEach(injectLink);
    for (const src of SCRIPT_SRCS) {
      await injectScript(src);
    }
    if (!(window as { Potree?: unknown }).Potree) {
      throw new Error("Potree 脚本已加载但未暴露全局对象，请检查 vendor 资源版本");
    }
  })().catch((error) => {
    // 失败后允许重试，不要把坏状态永久缓存
    assetsPromise = null;
    throw error;
  });

  return assetsPromise;
}
