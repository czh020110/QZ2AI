import { QuartzComponent, QuartzComponentConstructor } from "./types"
import style from "./styles/font-override.scss"

/**
 * 覆盖 Quartz fonts 插件生成的 CSS 变量。
 * Quartz 字体系统会在用户自定义字体系列后追加 system-ui / sans-serif 回退链，
 * 覆盖用户指定的 serif 回退，导致 Linux/非中文系统上显示 sans-serif 而非宋体。
 * 本组件 CSS 在 fonts 插件 CSS 之后加载，通过级联规则覆盖 --headerFont / --bodyFont。
 */
const FontOverride: QuartzComponent = () => {
  return <></>
}

FontOverride.displayName = "FontOverride"
FontOverride.css = style

export default (() => FontOverride) satisfies QuartzComponentConstructor
