import { loadQuartzConfig, loadQuartzLayout } from "./quartz/plugins/loader/config-loader"
import { PageTypeDispatcher } from "./quartz/pageTypeDispatcher"
import AIChatWidget from "./quartz/components/AIChatWidget"

const config = await loadQuartzConfig()
const layout = await loadQuartzLayout()

layout.defaults.afterBody = [...(layout.defaults.afterBody ?? []), AIChatWidget()]
config.plugins.emitters = config.plugins.emitters.filter((plugin) => plugin.name !== "PageTypeDispatcher")
config.plugins.emitters.push(
  PageTypeDispatcher({
    defaults: layout.defaults,
    byPageType: layout.byPageType,
  }),
)

export default config
export { layout }
