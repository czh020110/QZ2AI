import { PageTypeDispatcher as UpstreamPageTypeDispatcher } from "./plugins/pageTypes/dispatcher"
import type { QuartzEmitterPlugin } from "./plugins/types"
import type { BuildCtx } from "./util/ctx"
import type { FullPageLayout } from "./cfg"
import AIChatWidget from "./components/AIChatWidget"
import FontOverride from "./components/FontOverride"

const chatComponent = AIChatWidget()
const fontOverrideComponent = FontOverride()

export const PageTypeDispatcher: QuartzEmitterPlugin<any> = (userOpts) => {
  const upstream = UpstreamPageTypeDispatcher(userOpts)

  return {
    ...upstream,
    getQuartzComponents(ctx: BuildCtx) {
      const components = upstream.getQuartzComponents?.(ctx) ?? []
      return [...components, chatComponent, fontOverrideComponent]
    },
  }
}
