import { QuartzComponent, QuartzComponentConstructor } from "./types"
import style from "./styles/appearance.scss"
// @ts-expect-error - inline script imported as string by esbuild loader
import script from "./scripts/appearance.inline"

/**
 * 外观设置运行时注入器。
 * 不渲染可见元素,仅注入 CSS + 内联脚本:脚本 fetch /api/appearance 后
 * 动态注入头像(左上角)、社交链接(右上角)、字体、favicon。
 * 配置变更免 Quartz 重建,二次访问用 localStorage 缓存消除闪烁。
 */
const AppearanceInjector: QuartzComponent = () => {
  return <></>
}

AppearanceInjector.displayName = "AppearanceInjector"
AppearanceInjector.css = style
AppearanceInjector.afterDOMLoaded = script

export default (() => AppearanceInjector) satisfies QuartzComponentConstructor
