type ChatMessage = {
  role: "user" | "assistant" | "error"
  content: string
  sources?: { title: string; url: string }[]
}

const ROOT_ID = "ai-chat-widget-root"

// AI 聊天浮窗 UI 文案 i18n:按博客界面语言(blog_locale)翻译,与 appearance.inline.ts 共享 localStorage 缓存。
const AI_CHAT_I18N: Record<string, Record<string, string>> = {
  "en-US": {
    "AI 问答": "AI Chat",
    "基于当前博客内容回答问题，也可直接提交反馈哦": "Answers based on this blog's content. You can also submit feedback.",
    "关闭": "Close",
    "输入你的问题…": "Type your question…",
    "Enter 发送，Shift+Enter 换行": "Enter to send, Shift+Enter for newline",
    "发送": "Send",
    "来源": "Sources",
    "你": "You",
    "错误": "Error",
    "请求失败": "Request failed",
    "服务暂时不可用。": "Service temporarily unavailable.",
    "暂时无法完成回答，请稍后再试。": "Unable to complete the answer. Please try again later.",
  },
  "ja-JP": {
    "AI 问答": "AIチャット",
    "基于当前博客内容回答问题，也可直接提交反馈哦": "このブログの内容に基づいて回答。フィードバックも送信できます。",
    "关闭": "閉じる",
    "输入你的问题…": "質問を入力…",
    "Enter 发送，Shift+Enter 换行": "Enter で送信、Shift+Enter で改行",
    "发送": "送信",
    "来源": "出典",
    "你": "あなた",
    "错误": "エラー",
    "请求失败": "リクエスト失敗",
    "服务暂时不可用。": "サービスは一時的に利用できません。",
    "暂时无法完成回答，请稍后再试。": "回答を完了できません。後でもう一度お試しください。",
  },
}

// 取博客界面语言:优先 localStorage 缓存(appearance.inline.ts 写入),无则 en-US。
function aiChatLocale(): string {
  try {
    const cached = localStorage.getItem("blog-appearance-v1")
    if (cached) {
      const cfg = JSON.parse(cached)
      if (cfg && typeof cfg.blog_locale === "string") return cfg.blog_locale
    }
  } catch {
    /* ignore */
  }
  return "en-US"
}

function aiT(zh: string): string {
  const locale = aiChatLocale()
  if (locale === "zh-CN") return zh
  return (AI_CHAT_I18N[locale] || {})[zh] || zh
}

const escapeHtml = (v: string) =>
  v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;")

// ================================================================
// 精简 Markdown 渲染器（无外部依赖，自包含）
// 优先级：代码块 > 行内代码 > 图片 > 标题 > 水平线 > 表格 >
//         删除线 > 加粗 > 斜体 > 链接 > 脚注引用 > 无序列表 > 有序列表 > 引用 > 段落
// ================================================================

// --- KaTeX 懒加载（Quartz 页面已注入 katex.min.css，只缺渲染 JS） ---
let _katexLoading: Promise<void> | null = null
const loadKatex = (): Promise<void> => {
  if ((window as unknown as { katex?: unknown }).katex) return Promise.resolve()
  if (_katexLoading) return _katexLoading
  _katexLoading = new Promise<void>((resolve, reject) => {
    const s = document.createElement("script")
    s.src = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => reject(new Error("KaTeX 加载失败"))
    document.head.appendChild(s)
  })
  return _katexLoading
}

const renderKatex = async (latex: string, displayMode: boolean): Promise<string> => {
  try {
    await loadKatex()
    const katex = (window as unknown as { katex: { renderToString: (s: string, o: { displayMode: boolean; throwOnError: boolean }) => string } }).katex
    return katex.renderToString(latex, { displayMode, throwOnError: false })
  } catch {
    return `<code class="ai-chat-md__inline-code">${escapeHtml(latex)}</code>`
  }
}

// --- 主渲染函数 ---
const renderMarkdown = (md: string): string => {
  let html = md

  // --- 1. 脚注预处理（收集定义，替换引用）---
  const fnDefs: Record<string, string> = {}
  const defRe = /^\[\^([^\]]+)\]:[ \t]*(.*(?:\n[ \t]+.*)*)/gm
  let dm: RegExpExecArray | null
  const footnotes: string[] = []
  let fi = 0
  while ((dm = defRe.exec(md)) !== null) {
    fnDefs[dm[1]] = dm[2].replace(/\n[ \t]+/g, " ").trim()
  }
  html = html.replace(/\[\^([^\]]+)\]/g, (_m, label: string) => {
    if (!(label in fnDefs)) return _m
    fi++
    footnotes.push(fnDefs[label])
    return `<sup class="ai-chat-md__fn-ref" data-fn="${fi - 1}">[${fi}]</sup>`
  })
  // 清除脚注定义行
  html = html.replace(defRe, "")

  // --- 2. 转义 HTML ---
  html = escapeHtml(html)

  // --- 3. 代码块（```...```）---
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) => {
    return `<pre class="ai-chat-md__code-block"><code>${code.trim()}</code></pre>`
  })

  // --- 4. 行内代码（`...`）---
  html = html.replace(/`([^`\n]+)`/g, '<code class="ai-chat-md__inline-code">$1</code>')

  // --- 5. 图片 ![alt](url) ---
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img class="ai-chat-md__img" src="$2" alt="$1" />')

  // --- 6. 标题（限制 h3~h6，小浮窗里 h1/h2 太大）---
  html = html.replace(/^##### (.+)$/gm, '<h5 class="ai-chat-md__h">$1</h5>')
  html = html.replace(/^#### (.+)$/gm, '<h4 class="ai-chat-md__h">$1</h4>')
  html = html.replace(/^### (.+)$/gm, '<h3 class="ai-chat-md__h">$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h3 class="ai-chat-md__h">$1</h3>')
  html = html.replace(/^# (.+)$/gm, '<h3 class="ai-chat-md__h">$1</h3>')

  // --- 7. 水平线（--- 或 ***，≥3 个）---
  html = html.replace(/^([-*])\1{2,}$/gm, '<hr class="ai-chat-md__hr" />')

  // --- 8. 表格（GFM 风格，≥3 行 pipe 分隔）---
  html = html.replace(
    /((?:^\|.+\|$\n?){3,})/gm,
    (block) => {
      const lines = block.trim().split("\n").filter(Boolean)
      if (lines.length < 3) return block
      const headerCells = lines[0].split("|").filter(Boolean).map(c => `<th class="ai-chat-md__th">${c.trim()}</th>`).join("")
      const bodyRows = lines.slice(2).map(row =>
        `<tr class="ai-chat-md__tr">` +
        row.split("|").filter(Boolean).map(c => `<td class="ai-chat-md__td">${c.trim()}</td>`).join("") +
        `</tr>`
      ).join("")
      return `<table class="ai-chat-md__table"><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table>`
    }
  )

  // --- 9. 删除线 ~~text~~ ---
  html = html.replace(/~~(.+?)~~/g, "<s>$1</s>")

  // --- 10. 加粗 **...** ---
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")

  // --- 11. 斜体 *...* ---
  html = html.replace(/\*(?!\*)(.+?)(?<!\*)\*/g, "<em>$1</em>")

  // --- 12. 链接 [text](url) ---
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')

  // --- 13. 无序列表 ---
  html = html.replace(/^[\-\*] (.+)$/gm, '<li class="ai-chat-md__li">$1</li>')
  html = html.replace(/((?:<li class="ai-chat-md__li">.*?<\/li>\n?)+)/g, '<ul class="ai-chat-md__ul">$1</ul>')

  // --- 14. 有序列表 ---
  html = html.replace(/^\d+\. (.+)$/gm, '<li class="ai-chat-md__ol-li">$1</li>')
  html = html.replace(/((?:<li class="ai-chat-md__ol-li">.*?<\/li>\n?)+)/g, '<ol class="ai-chat-md__ol">$1</ol>')

  // --- 15. 引用块（> 开头）---
  html = html.replace(/^&gt; (.+)$/gm, '<p class="ai-chat-md__blockquote-line">$1</p>')
  html = html.replace(
    /((?:<p class="ai-chat-md__blockquote-line">.*?<\/p>\n?)+)/g,
    '<blockquote class="ai-chat-md__blockquote">$1</blockquote>'
  )

  // --- 16. 段落（按双换行分段）---
  html = html
    .split(/\n{2,}/)
    .map(chunk => {
      const t = chunk.trim()
      if (!t) return ""
      if (/^<(ul|ol|pre|blockquote|h[3-6]|table|hr)[\s>]/.test(t)) return t
      return `<p>${t.replace(/\n/g, "<br>")}</p>`
    })
    .join("")

  // --- 17. KaTeX 公式占位（$$...$$ 块级，$...$ 行内）---
  html = html.replace(/\$\$([\s\S]+?)\$\$/g, (_m, latex: string) => {
    return `<span class="ai-chat-md__katex-block" data-latex="${escapeHtml(latex.trim())}"></span>`
  })
  html = html.replace(/(?<![\\$])\$([^$\n]+?)\$/g, (_m, latex: string) => {
    return `<span class="ai-chat-md__katex-inline" data-latex="${escapeHtml(latex.trim())}"></span>`
  })

  // --- 18. 脚注列表 ---
  if (footnotes.length > 0) {
    html += `<div class="ai-chat-md__footnotes"><sup class="ai-chat-md__fn-sep">†</sup>` +
      footnotes.map((fn, i) =>
        `<span class="ai-chat-md__fn-item">[${i + 1}] ${escapeHtml(fn)}</span>`
      ).join("") + `</div>`
  }

  return html
}

// 二次渲染 KaTeX（katex.js 加载完成后调用，将公式占位替换为真实渲染 HTML）
const postRenderKatex = (root: HTMLElement) => {
  const katex = (window as unknown as { katex?: { renderToString: (s: string, o: { displayMode: boolean; throwOnError: boolean }) => string } }).katex
  if (!katex) return
  root.querySelectorAll(".ai-chat-md__katex-block").forEach((el) => {
    const latex = el.getAttribute("data-latex")
    if (!latex) return
    try {
      el.innerHTML = katex.renderToString(latex, { displayMode: true, throwOnError: false })
      el.className = "ai-chat-md__katex-rendered"
    } catch {
      el.textContent = `$$${latex}$$`
    }
  })
  root.querySelectorAll(".ai-chat-md__katex-inline").forEach((el) => {
    const latex = el.getAttribute("data-latex")
    if (!latex) return
    try {
      el.innerHTML = katex.renderToString(latex, { displayMode: false, throwOnError: false })
      el.className = "ai-chat-md__katex-rendered"
    } catch {
      el.textContent = `$${latex}$`
    }
  })
}
function mountAIChatWidget() {
  const roots = Array.from(document.querySelectorAll<HTMLDivElement>(`#${ROOT_ID}`))
  let root = roots.find((node) => node.dataset.mounted === "true") ?? roots[0] ?? null

  if (!root) {
    root = document.createElement("div")
    root.id = ROOT_ID
  }

  // Quartz 会在页面内容里重复渲染占位节点；真实浮窗统一挂到 body，避免局部容器影响 fixed/backdrop-filter。
  if (root.parentElement !== document.body) {
    document.body.appendChild(root)
  }
  roots.forEach((node) => {
    if (node !== root) node.remove()
  })

  root.classList.add("ai-chat-widget-root")
  if (root.dataset.mounted === "true") return
  root.dataset.mounted = "true"

  let isOpen = false
  let isLoading = false
  const messages: ChatMessage[] = []

  root.innerHTML = `
    <div class="ai-chat-widget" data-state="closed">
      <button type="button" class="ai-chat-widget__launcher" aria-label="${aiT("AI 问答")}">
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 6V2H8"/><path d="M15 11v2"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M20 16a2 2 0 0 1-2 2H8.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 4 20.286V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/><path d="M9 11v2"/></svg>
      </button>
      <section class="ai-chat-widget__panel" aria-hidden="true">
        <header class="ai-chat-widget__header">
          <div class="ai-chat-widget__header-brand">
            <div class="ai-chat-widget__header-badge">
              <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 6V2H8"/><path d="M15 11v2"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M20 16a2 2 0 0 1-2 2H8.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 4 20.286V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/><path d="M9 11v2"/></svg>
            </div>
            <div class="ai-chat-widget__header-text">
              <div class="ai-chat-widget__header-title">${aiT("AI 问答")}</div>
              <div class="ai-chat-widget__header-subtitle">${aiT("基于当前博客内容回答问题，也可直接提交反馈哦")}</div>
            </div>
          </div>
          <button type="button" class="ai-chat-widget__close" aria-label="${aiT("关闭")}">
            <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
          </button>
        </header>
        <div class="ai-chat-widget__messages"></div>
        <form class="ai-chat-widget__composer">
          <textarea class="ai-chat-widget__textarea" placeholder="${aiT("输入你的问题…")}" rows="3"></textarea>
          <div class="ai-chat-widget__footer">
            <span class="ai-chat-widget__hint">${aiT("Enter 发送，Shift+Enter 换行")}</span>
            <button type="submit" class="ai-chat-widget__send">${aiT("发送")}</button>
          </div>
        </form>
      </section>
    </div>
  `

  const widget = root.querySelector<HTMLElement>(".ai-chat-widget")!
  const launcher = root.querySelector<HTMLButtonElement>(".ai-chat-widget__launcher")!
  const panel = root.querySelector<HTMLElement>(".ai-chat-widget__panel")!
  const closeBtn = root.querySelector<HTMLButtonElement>(".ai-chat-widget__close")!
  const messagesEl = root.querySelector<HTMLElement>(".ai-chat-widget__messages")!
  const form = root.querySelector<HTMLFormElement>(".ai-chat-widget__composer")!
  const textarea = root.querySelector<HTMLTextAreaElement>(".ai-chat-widget__textarea")!
  const sendBtn = root.querySelector<HTMLButtonElement>(".ai-chat-widget__send")!

  const syncUI = () => {
    widget.dataset.state = isOpen ? "open" : "closed"
    panel.setAttribute("aria-hidden", isOpen ? "false" : "true")
    sendBtn.disabled = isLoading
    textarea.disabled = isLoading
    renderMessages()
  }

  const renderMessages = () => {
    if (messages.length === 0) {
      messagesEl.innerHTML = `<div class="ai-chat-widget__empty">
          <div class="ai-chat-widget__empty-icon">
            <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 6V2H8"/><path d="M15 11v2"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M20 16a2 2 0 0 1-2 2H8.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 4 20.286V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/><path d="M9 11v2"/></svg>
          </div>
          <div class="ai-chat-widget__empty-text">${aiT("基于当前博客内容回答问题，也可直接提交反馈哦")}</div>
        </div>`
      postRenderKatex(messagesEl)
      return
    }
    messagesEl.innerHTML = messages
      .map((m) => {
        const roleLabel = m.role === "user" ? aiT("你") : m.role === "error" ? aiT("错误") : "AI"
        const sourcesHtml =
          m.sources && m.sources.length > 0
            ? `<div class="ai-chat-widget__sources">
                <div class="ai-chat-widget__sources-title">${aiT("来源")}</div>
                <div class="ai-chat-widget__sources-list">
                  ${m.sources.filter((s) => s.url).map((s) => `<a class="ai-chat-widget__source-link" href="${s.url}">${escapeHtml(s.title || s.url)}</a>`).join("")}
                </div>
              </div>`
            : ""
        const errorHtml = m.role === "error" ? `<div class="ai-chat-widget__message-error">${escapeHtml(m.content)}</div>` : ""
        const waiting = m.role === "assistant" && m.content === "" && isLoading
        const contentHtml = waiting
          ? `<div class="ai-chat-widget__typing"><span></span><span></span><span></span></div>`
          : renderMarkdown(m.content)
        return `<div class="ai-chat-widget__message ai-chat-widget__message--${m.role}">
          <div class="ai-chat-widget__message-card">
            <div class="ai-chat-widget__message-role">${roleLabel}</div>
            <div class="ai-chat-widget__message-content">${contentHtml}</div>
            ${sourcesHtml}${errorHtml}
          </div>
        </div>`
      })
      .join("")
    messagesEl.scrollTop = messagesEl.scrollHeight
    postRenderKatex(messagesEl)
  }

  const openPanel = () => {
    isOpen = true
    syncUI()
    queueMicrotask(() => textarea.focus())
  }

  const closePanel = () => {
    isOpen = false
    syncUI()
  }

  const getPageContext = () => ({
    slug: document.body?.dataset?.slug || ""
  })

  const submitQuestion = async (question: string) => {
    messages.push({ role: "user", content: question })
    const assistantMsg: ChatMessage = { role: "assistant", content: "" }
    messages.push(assistantMsg)
    isLoading = true
    syncUI()

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, page_context: getPageContext() }),
      })

      if (!res.ok) {
        let detail = `${aiT("请求失败")}（${res.status}）`
        try {
          const data = await res.json()
          if (data?.message) detail = data.message
        } catch {}
        throw new Error(detail)
      }

      if (!res.body) throw new Error("浏览器未返回可读取的响应流。")

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      const sources: { title: string; url: string }[] = []

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const frames = buffer.split("\n\n")
        buffer = frames.pop() ?? ""

        for (const frame of frames) {
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data:")) continue
            const payload = line.slice(5).trim()
            if (!payload) continue

            try {
              const evt = JSON.parse(payload)
              if ("delta" in evt && typeof evt.delta === "string") {
                assistantMsg.content += evt.delta
                renderMessages()
              } else if ("done" in evt && evt.done) {
                if (evt.sources) {
                  for (const s of evt.sources) {
                    if (!sources.some((x) => x.url === s.source_url)) {
                      sources.push({ title: s.title, url: s.source_url })
                    }
                  }
                }
              } else if ("error" in evt) {
                throw new Error(evt.error?.message || aiT("服务暂时不可用。"))
              }
            } catch (e) {
              if (e instanceof Error && e.message !== aiT("服务暂时不可用。") && !e.message.startsWith(aiT("请求失败"))) throw e
            }
          }
        }
      }

      assistantMsg.sources = sources
      renderMessages()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : aiT("暂时无法完成回答，请稍后再试。")
      messages.pop()
      if (assistantMsg.content === "") messages.pop()
      messages.push({ role: "error", content: msg })
      renderMessages()
    } finally {
      isLoading = false
      syncUI()
    }
  }

  const handleSubmit = (e: Event) => {
    e.preventDefault()
    if (isLoading) return
    const question = textarea.value.trim()
    if (!question) return
    textarea.value = ""
    submitQuestion(question)
  }

  const handleKeydown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      form.requestSubmit()
    }
  }

  // ESC 关闭面板；仅在面板内按键时响应，避免拦截页面其他快捷键。
  const handleEsc = (e: KeyboardEvent) => {
    if (e.key === "Escape" && isOpen) {
      e.stopPropagation()
      closePanel()
    }
  }

  // 点击面板外部关闭；面板打开时拦截 launcher 自身冒泡，避免点开即关。
  const handleOutsideClick = (e: MouseEvent) => {
    if (!isOpen) return
    if (widget.contains(e.target as Node)) return
    closePanel()
  }

  launcher.addEventListener("click", openPanel)
  closeBtn.addEventListener("click", closePanel)
  form.addEventListener("submit", handleSubmit)
  textarea.addEventListener("keydown", handleKeydown)
  panel.addEventListener("keydown", handleEsc)
  document.addEventListener("click", handleOutsideClick)

  if (typeof window.addCleanup === "function") {
    window.addCleanup(() => launcher.removeEventListener("click", openPanel))
    window.addCleanup(() => closeBtn.removeEventListener("click", closePanel))
    window.addCleanup(() => form.removeEventListener("submit", handleSubmit))
    window.addCleanup(() => textarea.removeEventListener("keydown", handleKeydown))
    window.addCleanup(() => panel.removeEventListener("keydown", handleEsc))
    window.addCleanup(() => document.removeEventListener("click", handleOutsideClick))
  }

  syncUI()

  // 异步拉取最新 blog_locale:appearance 缓存首次可能未就绪,locale 变化则更新 UI 文案。
  fetch("/api/appearance").then(r => (r.ok ? r.json() : null)).then(c => {
    if (!c || typeof c.blog_locale !== "string") return
    const prev = aiChatLocale()
    try {
      localStorage.setItem("blog-appearance-v1", JSON.stringify(c))
    } catch {
      /* ignore */
    }
    if (c.blog_locale === prev) return
    const set = (sel: string, fn: (el: HTMLElement) => void) => {
      const el = root.querySelector<HTMLElement>(sel)
      if (el) fn(el)
    }
    set(".ai-chat-widget__launcher", (el) => el.setAttribute("aria-label", aiT("AI 问答")))
    set(".ai-chat-widget__header-title", (el) => (el.textContent = aiT("AI 问答")))
    set(".ai-chat-widget__header-subtitle", (el) => (el.textContent = aiT("基于当前博客内容回答问题，也可直接提交反馈哦")))
    set(".ai-chat-widget__close", (el) => el.setAttribute("aria-label", aiT("关闭")))
    textarea.placeholder = aiT("输入你的问题…")
    set(".ai-chat-widget__hint", (el) => (el.textContent = aiT("Enter 发送，Shift+Enter 换行")))
    sendBtn.textContent = aiT("发送")
    renderMessages()
  }).catch(() => {
    /* ignore */
  })
}

function initialize() {
  mountAIChatWidget()
}

document.addEventListener("nav", initialize)
document.addEventListener("render", initialize)
