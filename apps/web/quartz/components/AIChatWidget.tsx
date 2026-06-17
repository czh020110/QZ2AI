import { QuartzComponent, QuartzComponentConstructor } from "./types"
import style from "./styles/ai-chat.scss"
// @ts-expect-error - inline script imported as string by esbuild loader
import script from "./scripts/ai-chat.inline"

const AIChatWidget: QuartzComponent = () => {
  return <div id="ai-chat-widget-root" class="ai-chat-widget-root"></div>
}

AIChatWidget.displayName = "AIChatWidget"
AIChatWidget.css = style
AIChatWidget.afterDOMLoaded = script

export default (() => AIChatWidget) satisfies QuartzComponentConstructor
