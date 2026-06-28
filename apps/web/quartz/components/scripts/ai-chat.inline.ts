const ROOT_ID = "ai-chat-widget-root"

const escapeHtml = (v: string) =>
  v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;")

const renderMarkdown = (md: string): string => {
  // 先转义 HTML 特殊字符
  let html = escapeHtml(md)

  // 代码块（```...```）— 必须最先处理，内部不再解析
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) => {
    return `<pre class="ai-chat-md__code-block"><code>${code.trim()}</code></pre>`
  })

  // 行内代码（`...`）
  html = html.replace(/`([^`\n]+)`/g, '<code class="ai-chat-md__inline-code">$1</code>')

  // 引用块（&gt; 开头）→ 转义后 > 变为 &gt;，按行匹配后包裹成 blockquote
  html = html.replace(/^&gt; (.+)$/gm, '<p class="ai-chat-md__blockquote-line">$1</p>')
  html = html.replace(
    /((?:<p class="ai-chat-md__blockquote-line">.*<\/p>\n?)+)/g,
    '<blockquote class="ai-chat-md__blockquote">$1</blockquote>',
  )

  // 加粗（**...**）
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")

  // 斜体（*...*）— 不匹配 ** 已处理的情况
  html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>")

  // 链接（[text](url)）
  html = html.replace(
    /\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>',
  )

  // 无序列表：先收集连续行再包裹，避免与有序列表交叉
  html = html.replace(/^[\-\*] (.+)$/gm, '<li class="ai-chat-md__li">$1</li>')
  html = html.replace(
    /((?:<li class="ai-chat-md__li">.*<\/li>\n?)+)/g,
    '<ul class="ai-chat-md__ul">$1</ul>',
  )

  // 有序列表：行首 数字. 开头
  html = html.replace(/^\d+\. (.+)$/gm, '<li class="ai-chat-md__ol-li">$1</li>')
  html = html.replace(
    /((?:<li class="ai-chat-md__ol-li">.*<\/li>\n?)+)/g,
    '<ol class="ai-chat-md__ol">$1</ol>',
  )

  // 标题（### / ## / #）
  html = html.replace(/^### (.+)$/gm, '<h4 class="ai-chat-md__h">$1</h4>')
  html = html.replace(/^## (.+)$/gm, '<h4 class="ai-chat-md__h">$1</h4>')
  html = html.replace(/^# (.+)$/gm, '<h4 class="ai-chat-md__h">$1</h4>')

  // 段落：按双换行分段，块级元素直接输出不套 <p>
  html = html
    .split(/\n{2,}/)
    .map(chunk => {
      const t = chunk.trim()
      if (!t) return ""
      if (/^<(ul|ol|pre|blockquote|h4)[\s>]/.test(t)) return t
      return `<p>${t.replace(/\n/g, "<br>")}</p>`
    })
    .join("")

  return html
}

type ChatMessage = {
  role: "user" | "assistant" | "error"
  content: string
  sources?: { title: string; url: string }[]
}

function mountAIChatWidget() {
  let root = document.getElementById(ROOT_ID) as HTMLDivElement | null
  if (!root) {
    root = document.createElement("div")
    root.id = ROOT_ID
    document.body.appendChild(root)
  }
  root.classList.add("ai-chat-widget-root")
  if (root.dataset.mounted === "true") return
  root.dataset.mounted = "true"

  let isOpen = false
  let isLoading = false
  const messages: ChatMessage[] = []

  root.innerHTML = `
    <div class="ai-chat-widget" data-state="closed">
      <button type="button" class="ai-chat-widget__launcher" aria-label="AI 问答">
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12zM7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg>
      </button>
      <section class="ai-chat-widget__panel" hidden>
        <header class="ai-chat-widget__header">
          <div>
            <div class="ai-chat-widget__header-title">AI 问答</div>
            <div class="ai-chat-widget__header-subtitle">基于当前博客内容回答问题，也可直接提交反馈哦</div>
          </div>
          <button type="button" class="ai-chat-widget__close" aria-label="关闭">&times;</button>
        </header>
        <div class="ai-chat-widget__messages"></div>
        <form class="ai-chat-widget__composer">
          <textarea class="ai-chat-widget__textarea" placeholder="输入你的问题…" rows="3"></textarea>
          <div class="ai-chat-widget__footer">
            <span class="ai-chat-widget__hint">Enter 发送，Shift+Enter 换行</span>
            <button type="submit" class="ai-chat-widget__send">发送</button>
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
    panel.hidden = !isOpen
    launcher.hidden = isOpen
    sendBtn.disabled = isLoading
    textarea.disabled = isLoading
    renderMessages()
  }

  const renderMessages = () => {
    if (messages.length === 0) {
      messagesEl.innerHTML = `<div class="ai-chat-widget__empty">基于当前博客内容回答问题，也可直接提交反馈哦</div>`
      return
    }
    messagesEl.innerHTML = messages
      .map((m) => {
        const roleLabel = m.role === "user" ? "你" : m.role === "error" ? "错误" : "AI"
        const sourcesHtml =
          m.sources && m.sources.length > 0
            ? `<div class="ai-chat-widget__sources">
                <div class="ai-chat-widget__sources-title">来源</div>
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
        let detail = `请求失败（${res.status}）`
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
                throw new Error(evt.error?.message || "服务暂时不可用。")
              }
            } catch (e) {
              if (e instanceof Error && e.message !== "服务暂时不可用。" && !e.message.startsWith("请求失败")) throw e
            }
          }
        }
      }

      assistantMsg.sources = sources
      renderMessages()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "暂时无法完成回答，请稍后再试。"
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

  launcher.addEventListener("click", openPanel)
  closeBtn.addEventListener("click", closePanel)
  form.addEventListener("submit", handleSubmit)
  textarea.addEventListener("keydown", handleKeydown)

  if (typeof window.addCleanup === "function") {
    window.addCleanup(() => launcher.removeEventListener("click", openPanel))
    window.addCleanup(() => closeBtn.removeEventListener("click", closePanel))
    window.addCleanup(() => form.removeEventListener("submit", handleSubmit))
    window.addCleanup(() => textarea.removeEventListener("keydown", handleKeydown))
  }

  syncUI()
}

function initialize() {
  mountAIChatWidget()
}

document.addEventListener("nav", initialize)
document.addEventListener("render", initialize)
