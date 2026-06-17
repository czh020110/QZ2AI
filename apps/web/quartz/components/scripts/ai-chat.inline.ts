type ChatSource = {
  title: string
  source_url: string | null
}

type ChatMessage = {
  role: "user" | "assistant"
  content: string
  sources?: ChatSource[]
  error?: { code?: string; message: string }
}

type ChatStatus = "idle" | "submitting" | "streaming" | "done" | "error"

type ChatEvent =
  | { delta: string }
  | { done: true; sources: ChatSource[] }
  | { error: { code?: string; message: string; detail?: unknown }; done: true; sources: ChatSource[] }

const ROOT_ID = "ai-chat-widget-root"

const registerCleanup = (cleanup: () => void) => {
  if (typeof window.addCleanup === "function") {
    window.addCleanup(cleanup)
  }
}

const escapeHtml = (value: string) =>
  value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;")

const createId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

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
  let isStreaming = false
  let status: ChatStatus = "idle"
  let statusText = ""
  let messages: ChatMessage[] = []
  let draft = ""

  root.innerHTML = `
    <div class="ai-chat-widget" data-state="closed">
      <button type="button" class="ai-chat-widget__launcher" aria-expanded="false">
        <span>AI 问答</span>
      </button>
      <section class="ai-chat-widget__panel" hidden>
        <header class="ai-chat-widget__header">
          <div>
            <div class="ai-chat-widget__header-title">AI 问答</div>
            <div class="ai-chat-widget__header-subtitle">基于当前博客内容回答，并附来源链接</div>
          </div>
          <button type="button" class="ai-chat-widget__close" aria-label="关闭">×</button>
        </header>
        <div class="ai-chat-widget__messages"></div>
        <form class="ai-chat-widget__composer">
          <textarea class="ai-chat-widget__textarea" placeholder="例如：这篇博客为什么要求索引重建必须统一在 API 进程内触发？"></textarea>
          <div class="ai-chat-widget__footer">
            <div>
              <div class="ai-chat-widget__hint">Enter 发送，Shift+Enter 换行</div>
              <div class="ai-chat-widget__status"></div>
            </div>
            <button type="submit" class="ai-chat-widget__send">发送</button>
          </div>
        </form>
      </section>
    </div>
  `

  const widget = root.querySelector<HTMLElement>(".ai-chat-widget")
  const launcher = root.querySelector<HTMLButtonElement>(".ai-chat-widget__launcher")
  const panel = root.querySelector<HTMLElement>(".ai-chat-widget__panel")
  const closeButton = root.querySelector<HTMLButtonElement>(".ai-chat-widget__close")
  const messagesEl = root.querySelector<HTMLElement>(".ai-chat-widget__messages")
  const form = root.querySelector<HTMLFormElement>(".ai-chat-widget__composer")
  const textarea = root.querySelector<HTMLTextAreaElement>(".ai-chat-widget__textarea")
  const sendButton = root.querySelector<HTMLButtonElement>(".ai-chat-widget__send")
  const statusEl = root.querySelector<HTMLElement>(".ai-chat-widget__status")

  if (!widget || !launcher || !panel || !closeButton || !messagesEl || !form || !textarea || !sendButton || !statusEl) {
    return
  }

  const syncUI = () => {
    widget.dataset.state = isOpen ? "open" : "closed"
    panel.hidden = !isOpen
    launcher.setAttribute("aria-expanded", String(isOpen))
    launcher.hidden = isOpen
    textarea.value = draft
    textarea.disabled = isStreaming
    sendButton.disabled = isStreaming
    statusEl.textContent = statusText
    renderMessages()
  }

  const renderMessages = () => {
    if (messages.length === 0) {
      messagesEl.innerHTML = `<div class="ai-chat-widget__empty">你可以直接提问，我会基于当前博客内容回答，并在结尾附上来源页面。</div>`
      return
    }

    messagesEl.innerHTML = messages
      .map((message) => {
        const roleLabel = message.role === "user" ? "你" : "AI"
        const roleClass = message.error
          ? "ai-chat-widget__message ai-chat-widget__message--assistant ai-chat-widget__message--error"
          : `ai-chat-widget__message ai-chat-widget__message--${message.role}`
        const sources = message.sources?.length
          ? `
            <div class="ai-chat-widget__sources">
              <div class="ai-chat-widget__sources-title">引用来源</div>
              <div class="ai-chat-widget__sources-list">
                ${message.sources
                  .filter((source) => source.source_url)
                  .map(
                    (source) =>
                      `<a class="ai-chat-widget__source-link" href="${source.source_url}">${escapeHtml(source.title || source.source_url || "来源")}</a>`,
                  )
                  .join("")}
              </div>
            </div>`
          : ""
        const errorBlock = message.error
          ? `<div class="ai-chat-widget__message-error">${escapeHtml(message.error.message)}</div>`
          : ""

        return `
          <article class="${roleClass}">
            <div class="ai-chat-widget__message-card">
              <div class="ai-chat-widget__message-role">${roleLabel}</div>
              <div class="ai-chat-widget__message-content">${escapeHtml(message.content)}</div>
              ${errorBlock}
              ${sources}
            </div>
          </article>
        `
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

  const setStatus = (nextStatus: ChatStatus, text = "") => {
    status = nextStatus
    statusText = text
    isStreaming = nextStatus === "submitting" || nextStatus === "streaming"
    syncUI()
  }

  const appendUserMessage = (content: string) => {
    messages.push({ role: "user", content })
  }

  const appendAssistantPlaceholder = () => {
    messages.push({ role: "assistant", content: "" })
  }

  const getCurrentAssistantMessage = () => {
    const message = messages[messages.length - 1]
    if (!message || message.role !== "assistant") return null
    return message
  }

  const updateAssistantContent = (delta: string) => {
    const message = getCurrentAssistantMessage()
    if (!message) return
    message.content += delta
    renderMessages()
  }

  const finalizeAssistant = (sources: ChatSource[]) => {
    const message = getCurrentAssistantMessage()
    if (!message) return
    message.sources = sources
    renderMessages()
  }

  const failAssistant = (error: { code?: string; message: string }) => {
    const message = getCurrentAssistantMessage()
    if (!message) return
    if (!message.content) {
      message.content = "本次回答未能完成。"
    }
    message.error = error
    renderMessages()
  }

  const toHistory = () => {
    return messages.slice(-20).map(({ role, content }) => ({ role, content }))
  }

  const parseSseFrames = (buffer: string) => {
    const parts = buffer.split("\n\n")
    const remainder = parts.pop() ?? ""
    const events: ChatEvent[] = []

    for (const frame of parts) {
      const payload = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n")

      if (!payload) continue

      try {
        events.push(JSON.parse(payload) as ChatEvent)
      } catch {
        events.push({ error: { message: "收到无法解析的流式响应。" }, done: true, sources: [] })
      }
    }

    return { events, remainder }
  }

  const submitQuestion = async (question: string) => {
    const history = toHistory()
    appendUserMessage(question)
    appendAssistantPlaceholder()
    setStatus("submitting", "正在连接…")

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question,
          history,
        }),
      })

      if (!response.ok) {
        let message = `请求失败（${response.status}）`
        try {
          const data = await response.json()
          if (typeof data?.message === "string" && data.message) {
            message = data.message
          }
        } catch {}
        throw new Error(message)
      }

      if (!response.body) {
        throw new Error("浏览器未返回可读取的响应流。")
      }

      setStatus("streaming", "正在生成回答…")
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      let finished = false

      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parsed = parseSseFrames(buffer)
        buffer = parsed.remainder

        for (const event of parsed.events) {
          if ("delta" in event && typeof event.delta === "string") {
            updateAssistantContent(event.delta)
            continue
          }

          if ("error" in event) {
            failAssistant({ code: event.error.code, message: event.error.message || "服务暂时不可用。" })
            setStatus("error", event.error.message || "服务暂时不可用。")
            finished = true
            break
          }

          if ("done" in event && event.done) {
            finalizeAssistant(event.sources ?? [])
            setStatus("done", "回答完成")
            finished = true
            break
          }
        }

        if (finished) break
      }

      if (!finished) {
        throw new Error("响应已结束，但未收到完成信号。")
      }
    } catch (error) {
      const message = error instanceof Error && error.message ? error.message : "暂时无法完成回答，请稍后再试。"
      failAssistant({ message })
      setStatus("error", message)
    }
  }

  const handleInput = () => {
    draft = textarea.value
  }

  const handleKeydown = (event: KeyboardEvent) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      form.requestSubmit()
    }
  }

  const handleSubmit = async (event: SubmitEvent) => {
    event.preventDefault()
    if (isStreaming) return

    const question = draft.trim()
    if (!question) {
      setStatus("error", "请输入问题后再发送。")
      queueMicrotask(() => textarea.focus())
      return
    }

    draft = ""
    syncUI()
    await submitQuestion(question)
  }

  launcher.addEventListener("click", openPanel)
  closeButton.addEventListener("click", closePanel)
  textarea.addEventListener("input", handleInput)
  textarea.addEventListener("keydown", handleKeydown)
  form.addEventListener("submit", handleSubmit)

  registerCleanup(() => launcher.removeEventListener("click", openPanel))
  registerCleanup(() => closeButton.removeEventListener("click", closePanel))
  registerCleanup(() => form.removeEventListener("submit", handleSubmit))
  registerCleanup(() => textarea.removeEventListener("input", handleInput))
  registerCleanup(() => textarea.removeEventListener("keydown", handleKeydown))

  syncUI()
}

function initialize() {
  mountAIChatWidget()
}

document.addEventListener("nav", initialize)
document.addEventListener("render", initialize)
