// 外观设置运行时注入:从 /api/appearance 拉取配置,动态注入头像/社交链接/字体/favicon。
// 配置变更免 Quartz 重建,二次访问用 localStorage 缓存消除闪烁。
// 幂等:SPA 导航(nav 事件)后侧边栏重建,重新注入已去重(按 id)。

const CACHE_KEY = "blog-appearance-v1"
const AVATAR_WRAPPER_ID = "appearance-avatar-wrapper"
const SOCIALS_ID = "appearance-socials"
const FONT_STYLE_ID = "appearance-font"

// 内置社交图标(完整 SVG,brand 用 fill,lucide 用 stroke,统一 currentColor)
const BUILTIN_ICONS: Record<string, string> = {
  github: `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>`,
  email: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>`,
  twitter: `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>`,
  rss: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></svg>`,
  linkedin: `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 0 1-2.063-2.065 2.063 2.063 0 1 1 2.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.225 0z"/></svg>`,
  bilibili: `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M17.813 4.653h.854c1.51.054 2.769.578 3.773 1.574 1.004.995 1.524 2.249 1.56 3.76v7.36c-.036 1.51-.556 2.769-1.56 3.773s-2.262 1.524-3.773 1.56H5.333c-1.51-.036-2.769-.556-3.773-1.56S.036 18.858 0 17.347v-7.36c.036-1.511.556-2.765 1.56-3.76 1.004-.996 2.262-1.52 3.773-1.574h.774l-1.174-1.12a1.234 1.234 0 0 1-.373-.906c0-.356.124-.658.373-.907l.027-.027c.267-.249.573-.373.92-.373.347 0 .653.124.92.373L9.653 4.44c.071.071.16.107.267.107h4.267c.107 0 .196-.036.267-.107l2.853-2.747c.267-.249.573-.373.92-.373.347 0 .662.151.929.4.267.249.391.551.391.907 0 .355-.124.657-.373.906zM5.333 7.24c-.746.018-1.373.276-1.88.773-.506.498-.769 1.13-.786 1.894v7.52c.017.764.28 1.395.786 1.893.507.498 1.134.756 1.88.773h13.334c.746-.017 1.373-.275 1.88-.773.506-.498.769-1.129.786-1.893v-7.52c-.017-.765-.28-1.396-.786-1.894-.507-.497-1.134-.755-1.88-.773zM8 11.107c.373 0 .684.124.933.373.25.249.383.569.4.96v1.173c-.017.391-.15.711-.4.96-.249.25-.56.374-.933.374s-.684-.125-.933-.374c-.25-.249-.383-.569-.4-.96V12.44c.017-.391.15-.711.4-.96.249-.249.56-.373.933-.373zm8 0c.373 0 .684.124.933.373.25.249.383.569.4.96v1.173c-.017.391-.15.711-.4.96-.249.25-.56.374-.933.374s-.684-.125-.933-.374c-.25-.249-.383-.569-.4-.96V12.44c.017-.391.15-.711.4-.96.249-.249.56-.373.933-.373z"/></svg>`,
  zhihu: `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5.721 0C2.251 0 0 2.25 0 5.719V18.28C0 21.751 2.252 24 5.721 24h12.56C21.751 24 24 21.75 24 18.281V5.72C24 2.249 21.75 0 18.281 0zm1.964 6.078h6.871l-.001 6.355h-2.725v4.027l1.376-.001 1.795 5.213h-2.012l-1.268-3.715h-.349l-1.268 3.715H8.085l1.783-5.213 1.4.001v-4.027H7.685zm13.998.001v9.166l-2.92.001 1.795 5.213-2.012-.001-1.268-3.713h-.351l-1.266 3.714h-2.014l1.795-5.213h-2.92V6.079z"/></svg>`,
  weibo: `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10.098 20.323c-3.977.391-7.414-1.406-7.672-4.02-.259-2.609 2.759-5.047 6.74-5.441 3.979-.394 7.413 1.404 7.671 4.018.259 2.6-2.759 5.049-6.737 5.439v.004h-.002zM9.05 17.219c-.384.616-1.208.884-1.829.602-.612-.279-.793-.991-.406-1.593.379-.595 1.176-.861 1.793-.601.622.263.82.972.442 1.592zm1.27-1.627c-.141.237-.449.353-.689.253-.236-.09-.313-.361-.177-.586.138-.227.436-.346.672-.24.239.09.315.36.18.573v.001zm.176-2.719c-1.893-.493-4.033.45-4.857 2.118-.836 1.704-.026 3.591 1.886 4.21 1.983.641 4.318-.327 5.132-2.179.799-1.82-.273-3.654-2.161-4.149zm7.563-1.224c-.346-.105-.57-.18-.405-.615.375-.977.42-1.804.014-2.404-.766-1.138-2.854-1.077-5.262-.014 0 0-.751.331-.567-.27.375-1.217.31-2.227-.27-2.812-1.335-1.346-4.876.045-7.917 3.108C2.179 9.396 1.063 11.373 1.063 13.071c0 3.246 4.164 5.222 8.134 5.222 5.274 0 8.792-3.061 8.792-5.493 0-1.471-1.241-2.305-2.43-2.66l.001-.001zm2.846-4.806c-.585-.65-1.455-.896-2.27-.748l-.459.082c-.405.072-.795-.195-.869-.598-.072-.405.195-.795.6-.867l.459-.083c1.275-.226 2.625.183 3.555 1.215.93 1.032 1.215 2.4.855 3.645l-.129.435c-.119.39-.525.615-.915.495-.39-.119-.615-.525-.495-.915l.131-.435c.214-.81.045-1.703-.563-2.379v.001zm-2.156 1.95c-.285-.315-.704-.435-1.095-.36l-.314.06c-.345.067-.681-.165-.748-.51-.067-.345.165-.681.51-.748l.314-.06c.705-.135 1.455.105 1.965.66.51.555.66 1.305.42 1.995l-.105.315c-.119.36-.51.555-.87.435-.36-.119-.555-.51-.435-.87l.105-.315c.105-.315.045-.66-.255-.99l.001.001z"/></svg>`,
}

interface SocialLink {
  id: string
  name: string
  url: string
  icon_type: "builtin" | "custom"
  icon: string
  icon_url: string
  shape: string
  action: "jump" | "copy"  // jump=跳转(邮箱自动 mailto), copy=复制到剪贴板
}

interface AppearanceConfig {
  title_text: string
  title_font_family: string
  font_family: string
  explorer_font_family: string
  toc_font_family: string
  favicon_url: string
  avatar_url: string
  avatar_shape: string
  avatar_link: string
  social_links: SocialLink[]
  background: { name: string; light: string; dark: string } | null
}

let currentConfig: AppearanceConfig | null = null
let fetched = false

function esc(s: string): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : c === ">" ? "&gt;" : c === '"' ? "&quot;" : "&#39;"
  )
}

function safeUrl(u: string): string {
  const s = String(u ?? "").trim()
  if (!s) return "#"
  if (/^(https?:|mailto:|\/)/i.test(s)) return s
  return "#"
}

// 需要远程加载的字体预设:值匹配时额外 @import 本地字体包,客户端无需本地安装。
// LXGW WenKai Screen 按 unicode-range 分 97 个 woff2 分片,浏览器只下载页面实际用到字符的分片。
// 字体包内置于 apps/web/fonts/,nginx 通过 /fonts/ 托管,无 CDN 依赖、离线可用。
const REMOTE_FONTS: Record<string, string> = {
  "LXGW WenKai Screen": "/fonts/lxgw/lxgwwenkaiscreen.css",
}

function applyFont(ff: string) {
  const el = document.getElementById(FONT_STYLE_ID) as HTMLStyleElement | null
  if (!ff || !ff.trim()) {
    if (el) el.remove()
    return
  }
  // 字体值里可能含逗号和引号,直接拼进 CSS 变量;由后台校验长度,这里只做转义防注入
  const safe = ff.replace(/[<>]/g, "")
  // 若该字体在远程字体表内,先 @import 本地字体包(浏览器自动按需加载分片)
  const importRule = REMOTE_FONTS[ff.trim()]
    ? `@import url("${REMOTE_FONTS[ff.trim()]}");`
    : ""
  // 仅控制正文与页头字体;--titleFont 由签名字体单独控制,见 applyTitleFont
  const css = `${importRule}:root{--bodyFont:${safe};--headerFont:${safe};}`
  if (!el) {
    const style = document.createElement("style")
    style.id = FONT_STYLE_ID
    style.textContent = css
    document.head.appendChild(style)
  } else {
    el.textContent = css
  }
}

// 签名字体:作用于 .page-title(站点标题),独立于正文字体。
// 值内可能含需远程加载的字体名(如 LXGW WenKai Screen),同样 @import 本地字体包。
function applyTitleFont(ff: string) {
  const el = document.getElementById("appearance-title-font") as HTMLStyleElement | null
  if (!ff || !ff.trim()) {
    if (el) el.remove()
    return
  }
  const safe = ff.replace(/[<>]/g, "")
  // 签名字体值里若含远程字体名,需 @import 对应字体包。
  // 字体值是 fallback 链(如 'HanziPen SC','LXGW WenKai Screen',cursive),
  // 检测链中是否包含 REMOTE_FONTS 的 key,命中则 @import。
  const imports = Object.keys(REMOTE_FONTS)
    .filter((name) => safe.includes(name))
    .map((name) => `@import url("${REMOTE_FONTS[name]}");`)
    .join("")
  const css = `${imports}.page-title{font-family:${safe} !important;}`
  if (!el) {
    const style = document.createElement("style")
    style.id = "appearance-title-font"
    style.textContent = css
    document.head.appendChild(style)
  } else {
    el.textContent = css
  }
}

// 左侧导航栏(Explorer)字体:注入 --explorerFont,空串不注入(回退主题 --headerFont)。
function applyExplorerFont(ff: string) {
  const el = document.getElementById("appearance-explorer-font") as HTMLStyleElement | null
  if (!ff || !ff.trim()) {
    if (el) el.remove()
    return
  }
  const safe = ff.replace(/[<>]/g, "")
  const importRule = REMOTE_FONTS[ff.trim()]
    ? `@import url("${REMOTE_FONTS[ff.trim()]}");`
    : ""
  const css = `${importRule}:root{--explorerFont:${safe};}`
  if (!el) {
    const style = document.createElement("style")
    style.id = "appearance-explorer-font"
    style.textContent = css
    document.head.appendChild(style)
  } else {
    el.textContent = css
  }
}

// 右侧目录栏(TOC)字体:注入 --tocFont,空串不注入(回退主题 --bodyFont)。
function applyTocFont(ff: string) {
  const el = document.getElementById("appearance-toc-font") as HTMLStyleElement | null
  if (!ff || !ff.trim()) {
    if (el) el.remove()
    return
  }
  const safe = ff.replace(/[<>]/g, "")
  const importRule = REMOTE_FONTS[ff.trim()]
    ? `@import url("${REMOTE_FONTS[ff.trim()]}");`
    : ""
  const css = `${importRule}:root{--tocFont:${safe};}`
  if (!el) {
    const style = document.createElement("style")
    style.id = "appearance-toc-font"
    style.textContent = css
    document.head.appendChild(style)
  } else {
    el.textContent = css
  }
}

// 站点标题文字:替换 .page-title 内链接的文字内容(默认配置里是 "zhChen's Blog")
function applyTitleText(text: string) {
  const titleEl = document.querySelector(".page-title a") as HTMLElement | null
  if (!titleEl) return
  const t = text && text.trim() ? text.trim() : ""
  if (!t) return
  if (titleEl.textContent !== t) titleEl.textContent = t
}

// 背景颜色:覆盖 Quartz 根背景变量 --light。body 背景为 var(--light),覆盖即整站换肤。
// 利用 :root[saved-theme="dark"] 主题选择器,纯 CSS 自动跟随主题切换,无需 JS 监听。
// hex 已由后端校验,这里再做字符白名单防注入;某模式颜色为空则不覆盖该模式(保留 Quartz 默认)。
function applyBackground(bg: { name: string; light: string; dark: string } | null) {
  const el = document.getElementById("appearance-background") as HTMLStyleElement | null
  if (!bg || (!bg.light && !bg.dark)) {
    if (el) el.remove()
    return
  }
  const rules: string[] = []
  const light = String(bg.light || "").replace(/[^#0-9a-fA-F]/g, "")
  const dark = String(bg.dark || "").replace(/[^#0-9a-fA-F]/g, "")
  if (/^#[0-9A-Fa-f]{6}$/.test(light)) rules.push(`:root{--light:${light};}`)
  if (/^#[0-9A-Fa-f]{6}$/.test(dark)) rules.push(`:root[saved-theme="dark"]{--light:${dark};}`)
  if (!rules.length) {
    if (el) el.remove()
    return
  }
  const css = rules.join("")
  if (!el) {
    const style = document.createElement("style")
    style.id = "appearance-background"
    style.textContent = css
    document.head.appendChild(style)
  } else {
    el.textContent = css
  }
}

function applyFavicon(url: string) {
  if (!url) return
  // 幂等:已是目标 href 则跳过,避免 SPA 导航重复重建触发闪烁
  const current = document.querySelector('link[rel="icon"]') as HTMLLinkElement | null
  if (current && current.getAttribute("href") === url) return
  // 移除所有旧 icon link(含 shortcut icon),重建新元素。
  // 仅改 href 时 Chrome 标签页图标常不刷新;移除重建可触发浏览器重新请求与渲染。
  document.querySelectorAll('link[rel~="icon"], link[rel="shortcut icon"]').forEach((l) => l.remove())
  const link = document.createElement("link")
  link.rel = "icon"
  link.href = url
  document.head.appendChild(link)
}

function applyAvatar(cfg: AppearanceConfig) {
  const sidebar = document.querySelector(".left.sidebar") as HTMLElement | null
  if (!sidebar) return
  let wrapper = document.getElementById(AVATAR_WRAPPER_ID) as HTMLElement | null
  if (!cfg.avatar_url) {
    if (wrapper) wrapper.remove()
    return
  }
  const desiredTag = cfg.avatar_link ? "a" : "div"
  const shapeCls = `avatar-${cfg.avatar_shape || "circle"}`
  if (wrapper && wrapper.tagName.toLowerCase() !== desiredTag) {
    const newWrapper = document.createElement(desiredTag)
    newWrapper.id = AVATAR_WRAPPER_ID
    wrapper.replaceWith(newWrapper)
    wrapper = newWrapper
  }
  if (!wrapper) {
    wrapper = document.createElement(desiredTag)
    wrapper.id = AVATAR_WRAPPER_ID
    // absolute 定位,直接作为 sidebar 子元素即可,无需依赖 .page-title 位置
    sidebar.insertBefore(wrapper, sidebar.firstChild)
  }
  wrapper.className = `appearance-avatar-wrapper ${shapeCls}`
  if (cfg.avatar_link) {
    const a = wrapper as HTMLAnchorElement
    a.href = safeUrl(cfg.avatar_link)
    a.target = "_blank"
    a.rel = "noopener noreferrer"
  }
  wrapper.innerHTML = `<img src="${esc(cfg.avatar_url)}" alt="avatar" class="appearance-avatar ${shapeCls}">`
}

function applySocialLinks(links: SocialLink[]) {
  const sidebar = document.querySelector(".right.sidebar") as HTMLElement | null
  if (!sidebar) return
  let container = document.getElementById(SOCIALS_ID)
  if (!links || !links.length) {
    if (container) container.remove()
    return
  }
  if (!container) {
    container = document.createElement("div")
    container.id = SOCIALS_ID
    container.className = "appearance-socials"
    // absolute 定位,直接作为 sidebar 子元素即可,无需依赖 .graph 位置
    sidebar.insertBefore(container, sidebar.firstChild)
  }
  container.innerHTML = links
    .map((l) => {
      const shapeCls = `social-${l.shape || "circle"}`
      const iconHtml =
        l.icon_type === "custom" && l.icon_url
          ? `<img src="${esc(l.icon_url)}" alt="${esc(l.name)}" class="social-icon-img">`
          : BUILTIN_ICONS[l.icon] || BUILTIN_ICONS.github
      const action = l.action || "jump"
      const isEmail = l.icon === "email"
      // email 图标且 url 形如邮箱地址:跳转动作自动转 mailto:
      const resolvedUrl =
        action === "jump" && isEmail && l.url && !/^(https?:|mailto:|\/)/i.test(l.url) && /^[^\s@]+@[^\s@]+$/.test(l.url)
          ? `mailto:${l.url}`
          : safeUrl(l.url)
      const tag = action === "jump" ? "a" : "button"
      const hrefAttr = action === "jump" ? `href="${esc(resolvedUrl)}" target="_blank" rel="noopener noreferrer"` : `data-copy="${esc(l.url || "")}"`
      return `<${tag} ${hrefAttr} class="social-link ${shapeCls}" title="${esc(l.name)}" aria-label="${esc(l.name)}">${iconHtml}</${tag}>`
    })
    .join("")
}

// 复制动作:点击复制 data-copy 内容到剪贴板并显示 toast
function handleSocialClick(e: Event) {
  const target = (e.target as HTMLElement)?.closest(".social-link[data-copy]") as HTMLElement | null
  if (!target) return
  e.preventDefault()
  const text = target.getAttribute("data-copy") || ""
  if (!text) return
  navigator.clipboard?.writeText(text).then(
    () => showToast(`已复制: ${text}`, "success"),
    () => showToast("复制失败,请手动复制", "error"),
  )
}

// 轻量 toast(博客前端无 toast 工具,自建临时浮层)
let toastEl: HTMLElement | null = null
let toastTimer: number | undefined
function showToast(msg: string, _type: string) {
  if (!toastEl) {
    toastEl = document.createElement("div")
    toastEl.style.cssText =
      "position:fixed;top:20px;right:20px;padding:10px 16px;border-radius:8px;font-size:14px;z-index:9999;background:#222;color:#fff;border:1px solid #444;box-shadow:0 4px 12px rgba(0,0,0,.3);opacity:0;transition:opacity .25s;pointer-events:none;font-family:system-ui,sans-serif"
    document.body.appendChild(toastEl)
  }
  toastEl.textContent = msg
  requestAnimationFrame(() => (toastEl!.style.opacity = "1"))
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toastEl!.style.opacity = "0"), 2000)
}

function applyAll(cfg: AppearanceConfig | null) {
  if (!cfg) return
  applyFont(cfg.font_family)
  applyTitleFont(cfg.title_font_family)
  applyExplorerFont(cfg.explorer_font_family)
  applyTocFont(cfg.toc_font_family)
  applyTitleText(cfg.title_text)
  applyFavicon(cfg.favicon_url)
  applyAvatar(cfg)
  applySocialLinks(cfg.social_links || [])
  applyBackground(cfg.background)
}

function applyFromCache() {
  try {
    const cached = localStorage.getItem(CACHE_KEY)
    if (cached) applyAll(JSON.parse(cached))
  } catch {
    /* ignore */
  }
}

async function fetchAndApply() {
  if (fetched) return
  fetched = true
  try {
    const res = await fetch("/api/appearance")
    if (!res.ok) return
    const cfg = (await res.json()) as AppearanceConfig
    currentConfig = cfg
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(cfg))
    } catch {
      /* ignore */
    }
    applyAll(cfg)
  } catch {
    fetched = false // 失败允许下次 nav 重试
  }
}

// 启动:缓存立即应用 → 后台拉新
applyFromCache()
fetchAndApply()

// 社交链接复制动作:事件委托,绑定一次即可(SPA 切页后容器重建仍生效)
document.addEventListener("click", handleSocialClick)

// SPA 导航后 page-header 重建,重新注入(幂等);首屏也会触发 nav
document.addEventListener("nav", () => {
  if (currentConfig) applyAll(currentConfig)
  else applyFromCache()
  fetchAndApply()
})
