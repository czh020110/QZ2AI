#!/usr/bin/env node
const fs = require("fs")
const path = require("path")
let GithubSlugger = null
try {
  GithubSlugger = require("github-slugger").default
} catch {
  GithubSlugger = require("../_vendor_quartz/node_modules/github-slugger").default
}

const notesDir = path.resolve(process.env.NOTES_DIR || "/data/notes")
const outDir = path.resolve(process.env.PUBLIC_CONTENT_DIR || "/quartz/_content_public")
const manifestFile = process.env.CONTENT_MANIFEST || "/data_store/public_content_manifest.json"

const HIDDEN_DIRS = new Set([".git", ".github", ".obsidian", ".trash", "trash", "private", "templates", "node_modules", "__pycache__"])
const SENSITIVE_NAMES = new Set([".env", "id_rsa", "id_ed25519"])
const SENSITIVE_EXTS = new Set([".pem", ".key", ".sqlite", ".sqlite3", ".db"])
const MARKDOWN_EXTS = new Set([".md", ".markdown"])

function normalizeRel(value, allowRoot = true) {
  let raw = String(value || "").trim().replace(/\\/g, "/")
  if (raw === "" || raw === "." || raw === "/") return allowRoot ? "" : null
  if (raw.startsWith("/") || raw.startsWith("~") || raw.split("/", 1)[0].includes(":")) return null
  raw = raw.replace(/^\/+|\/+$/g, "")
  const parts = raw.split("/").filter(Boolean)
  if (!parts.length) return allowRoot ? "" : null
  if (parts.some((p) => p === "." || p === "..")) return null
  return parts.join("/")
}

function safeJoin(root, rel) {
  const target = path.resolve(root, rel || ".")
  if (target === root || target.startsWith(root + path.sep)) return target
  return null
}

function isBlockedName(name, isDir) {
  if (!name || name.startsWith(".")) return true
  const lower = name.toLowerCase()
  if (isDir && HIDDEN_DIRS.has(lower)) return true
  if (SENSITIVE_NAMES.has(lower)) return true
  if (SENSITIVE_EXTS.has(path.extname(lower))) return true
  return false
}

function isBlockedRel(rel, isDir = false) {
  if (!rel) return false
  const parts = rel.split("/").filter(Boolean)
  return parts.some((part, idx) => isBlockedName(part, idx < parts.length - 1 || isDir))
}

function isMarkdown(file) {
  return MARKDOWN_EXTS.has(path.extname(file).toLowerCase())
}

function stripPrefix(rel, prefix) {
  const p = normalizeRel(prefix || "") || ""
  if (!p) return rel
  if (rel === p) return ""
  return rel.startsWith(p + "/") ? rel.slice(p.length + 1) : rel
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true })
}

function isUnderAssetsFolder(rel, assetsFolders) {
  const safe = normalizeRel(rel)
  if (safe === null) return false
  const parts = safe.split("/").filter(Boolean)
  return parts.some((part) => assetsFolders.includes(part))
}

function copyFileRel(srcRel, destRel) {
  if (!srcRel && srcRel !== "") return
  if (!destRel && destRel !== "") return
  if (destRel === "") return
  if (isBlockedRel(srcRel)) return
  const src = safeJoin(notesDir, srcRel)
  if (!src || !fs.existsSync(src)) return
  const stat = fs.lstatSync(src)
  if (!stat.isFile() || stat.isSymbolicLink()) return
  const dest = safeJoin(outDir, destRel)
  if (!dest) return
  ensureDir(path.dirname(dest))
  if (isMarkdown(srcRel)) {
    const markdown = fs.readFileSync(src, "utf8")
    const baseDir = path.posix.dirname(srcRel) === "." ? "" : path.posix.dirname(srcRel)
    fs.writeFileSync(dest, normalizeAnchors(markdown, baseDir), "utf8")
    return
  }
  fs.copyFileSync(src, dest)
}

function isExcludedPath(rel, excluded) {
  if (!excluded || !excluded.length || !rel) return false
  return excluded.some(ex => rel === ex || rel.startsWith(ex + "/"))
}

function copyDirRel(srcRel, destRel, options = {}) {
  const assetsFolders = options.assetsFolders || []
  const excluded = options.excluded || []
  if (isBlockedRel(srcRel, true)) return
  const src = safeJoin(notesDir, srcRel)
  if (!src || !fs.existsSync(src)) return
  const stat = fs.lstatSync(src)
  if (!stat.isDirectory() || stat.isSymbolicLink()) return
  const entries = fs.readdirSync(src, { withFileTypes: true })
  for (const entry of entries) {
    const childSrcRel = srcRel ? `${srcRel}/${entry.name}` : entry.name
    if (isExcludedPath(childSrcRel, excluded)) continue
    const childDestRel = destRel ? `${destRel}/${entry.name}` : entry.name
    if (entry.isSymbolicLink()) continue
    if (entry.isDirectory()) {
      if (isBlockedName(entry.name, true)) continue
      copyDirRel(childSrcRel, childDestRel, options)
    } else if (entry.isFile()) {
      if (isBlockedName(entry.name, false)) continue
      if (isMarkdown(entry.name) && (options.onlyAssets || isUnderAssetsFolder(childSrcRel, assetsFolders))) continue
      copyFileRel(childSrcRel, childDestRel)
    }
  }
}

function readManifest() {
  if (fs.existsSync(manifestFile)) {
    try {
      return JSON.parse(fs.readFileSync(manifestFile, "utf8"))
    } catch (err) {
      console.warn(`[content] manifest parse failed, fallback to empty: ${err.message}`)
    }
  }
  // manifest 缺失时不再回退到全量根目录，避免误公开私有内容；
  // 用户需先在管理后台保存内容展示范围生成 manifest。
  console.warn("[content] manifest 缺失，发布空内容；请先在管理后台保存内容展示范围")
  return {
    version: 1,
    mode: "legacy",
    selected: [],
    strip_prefix: "",
    directories: [],
    files: [],
    assets_folders: String(process.env.NOTES_ASSETS_FOLDERS || "assets").split(",").map((s) => s.trim()).filter(Boolean),
  }
}

function isExternalRef(ref) {
  return !ref || ref.startsWith("#") || ref.startsWith("//") || /^[a-zA-Z][a-zA-Z\d+.-]*:/.test(ref)
}

function cleanMarkdownRef(rawRef) {
  let ref = String(rawRef || "").trim()
  if (!ref) return ""
  if (ref.startsWith("<") && ref.endsWith(">")) ref = ref.slice(1, -1).trim()
  ref = ref.replace(/\s+["'][^"']*["']\s*$/, "")
  const hashIndex = ref.indexOf("#")
  if (hashIndex >= 0) ref = ref.slice(0, hashIndex)
  const queryIndex = ref.indexOf("?")
  if (queryIndex >= 0) ref = ref.slice(0, queryIndex)
  return ref.trim()
}

function decodeAnchorFragment(fragment) {
  let value = String(fragment || "").trim()
  if (!value) return ""
  try {
    value = decodeURIComponent(value)
  } catch {}
  return value.trim()
}

function stripHeadingText(markdown) {
  return String(markdown || "")
    .replace(/!\[([^\]]*)\]\(([^)\n]+)\)/g, "$1")
    .replace(/\[([^\]]*)\]\(([^)\n]+)\)/g, "$1")
    .replace(/!\[\[([^\]]+)\]\]/g, (_, inner) => inner.split("|").pop() || inner)
    .replace(/\[\[([^\]]+)\]\]/g, (_, inner) => inner.split("|").pop() || inner.split("#").pop() || inner)
    .replace(/<[^>]+>/g, "")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/[\*_~]/g, "")
    .replace(/\\([\\`*_{}\[\]()#+\-.!])/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
}

function extractHeadingSlugMap(markdown) {
  const slugger = new GithubSlugger()
  const lines = String(markdown || "").split(/\r?\n/)
  const map = new Map()
  let inFence = false

  const addHeading = (raw) => {
    const text = stripHeadingText(raw)
    if (!text) return
    const slug = slugger.slug(text)
    if (!slug) return
    map.set(text, slug)
    map.set(decodeAnchorFragment(text), slug)
    map.set(slug, slug)
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (/^(```|~~~)/.test(line.trim())) {
      inFence = !inFence
      continue
    }
    if (inFence) continue

    const atx = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/)
    if (atx) {
      addHeading(atx[1])
      continue
    }

    const next = lines[i + 1] || ""
    if (/^\s{0,3}(=+|-+)\s*$/.test(next)) {
      addHeading(line.trim())
      i += 1
    }
  }
  return map
}

function normalizeSamePageAnchors(markdown) {
  const source = String(markdown || "")
  const headingMap = extractHeadingSlugMap(source)
  if (!headingMap.size) return source

  return source.replace(/(^|[^!])\[([^\]]*)\]\((#[^)\n]+)\)/gm, (full, prefix, text, href) => {
    const rawAnchor = decodeAnchorFragment(href.slice(1))
    if (!rawAnchor || rawAnchor.startsWith("^")) return full
    const normalized = headingMap.get(rawAnchor)
    if (!normalized) return full
    return `${prefix}[${text}](#${normalized})`
  })
}

// 跨页/同页标题锚点归一化：兼容 Obsidian 两种默认写法。
// 1) Markdown 链接 [text](path#标题) 或 [text](#标题)，Obsidian 会把空格等转成 URL 编码（%20）。
//    Quartz 用 github-slugger 给目标标题生成 id，原始编码片段匹配不上，点击无法跳转。
//    这里按目标笔记的真实标题 slug 归一化 fragment，路径部分保持不动交给 CrawlLinks 解析。
// 2) [[...]] 链接的标题片段若被 URL 编码，解码回原文即可让 Quartz ObsidianFlavoredMarkdown 按标题文本匹配。
const headingMapCache = new Map()

function getNoteHeadingMap(relPath) {
  const rel = normalizeRel(relPath, false)
  if (!rel) return null
  if (headingMapCache.has(rel)) return headingMapCache.get(rel)
  let result = null
  const src = safeJoin(notesDir, rel)
  if (src && fs.existsSync(src)) {
    const stat = fs.lstatSync(src)
    if (stat.isFile() && !stat.isSymbolicLink() && isMarkdown(rel)) {
      result = extractHeadingSlugMap(fs.readFileSync(src, "utf8"))
    }
  }
  headingMapCache.set(rel, result)
  return result
}

function normalizeMarkdownAnchors(markdown, baseDir) {
  const source = String(markdown || "")
  const samePageMap = extractHeadingSlugMap(source)

  return source.replace(/(^|[^!])\[([^\]]*)\]\(([^)\n]+)\)/gm, (full, prefix, text, rawHref) => {
    let href = String(rawHref || "").trim()
    if (!href) return full
    if (href.startsWith("<") && href.endsWith(">")) href = href.slice(1, -1).trim()
    href = href.replace(/\s+["'][^"']*["']\s*$/, "")
    const hashIndex = href.indexOf("#")
    if (hashIndex < 0) return full
    const preFragment = href.slice(0, hashIndex)
    const anchorPart = href.slice(hashIndex + 1)
    if (!anchorPart || anchorPart.startsWith("^")) return full
    const decoded = decodeAnchorFragment(anchorPart)
    let map = samePageMap
    if (preFragment) {
      const targetRel = resolveLocalRef(baseDir, preFragment)
      if (!targetRel) return full
      const targetMap = getNoteHeadingMap(targetRel)
      if (targetMap && targetMap.size) map = targetMap
      else return full
    }
    const normalized = map.get(decoded)
    if (!normalized) return full
    return `${prefix}[${text}](${preFragment}#${normalized})`
  })
}

function normalizeWikilinkAnchors(markdown) {
  return String(markdown || "").replace(/!?\[\[([^\]]+)\]\]/g, (full, inner) => {
    const parts = inner.split("|")
    const target = parts[0]
    const alias = parts.slice(1).join("|")
    const hashIndex = target.indexOf("#")
    if (hashIndex < 0) return full
    const pre = target.slice(0, hashIndex)
    const anchor = target.slice(hashIndex + 1)
    if (!anchor || anchor.startsWith("^")) return full
    const decoded = decodeAnchorFragment(anchor)
    if (decoded === anchor) return full
    const newTarget = `${pre}#${decoded}`
    const newInner = alias ? `${newTarget}|${alias}` : newTarget
    return full.startsWith("![") ? `![[${newInner}]]` : `[[${newInner}]]`
  })
}

function normalizeAnchors(markdown, baseDir) {
  return normalizeWikilinkAnchors(normalizeMarkdownAnchors(markdown, baseDir))
}

function extractLocalRefs(markdown) {
  const refs = new Set()
  const mdLinkRe = /!?\[[^\]]*\]\(([^)\n]+)\)/g
  const wikiRe = /!?\[\[([^\]]+)\]\]/g
  let match
  while ((match = mdLinkRe.exec(markdown)) !== null) {
    const ref = cleanMarkdownRef(match[1])
    if (ref && !isExternalRef(ref)) refs.add(ref)
  }
  while ((match = wikiRe.exec(markdown)) !== null) {
    const ref = cleanMarkdownRef(match[1].split("|")[0])
    if (ref && !isExternalRef(ref)) refs.add(ref)
  }
  return Array.from(refs)
}

function resolveLocalRef(baseDir, ref) {
  if (!ref || isExternalRef(ref)) return null
  if (ref.startsWith("/")) return normalizeRel(ref.slice(1), false)
  const joined = baseDir ? path.posix.join(baseDir, ref) : ref
  return normalizeRel(path.posix.normalize(joined), false)
}

function copySelectedFile(rel, strip, assetsFolders) {
  const destRel = stripPrefix(rel, strip)
  if (!destRel || !isMarkdown(rel)) return
  copyFileRel(rel, destRel)

  const src = safeJoin(notesDir, rel)
  if (!src || !fs.existsSync(src)) return
  const dir = path.posix.dirname(rel) === "." ? "" : path.posix.dirname(rel)
  const markdown = fs.readFileSync(src, "utf8")
  for (const ref of extractLocalRefs(markdown)) {
    const targetRel = resolveLocalRef(dir, ref)
    if (!targetRel || isBlockedRel(targetRel)) continue
    // 单文件公开模式：附件必须在当前文件所在目录或其子目录下，防止 ../ 越界复制私有内容
    if (dir && !(targetRel === dir || targetRel.startsWith(dir + "/"))) continue
    const target = safeJoin(notesDir, targetRel)
    if (!target || !fs.existsSync(target)) continue
    const stat = fs.lstatSync(target)
    if (stat.isSymbolicLink()) continue
    if (stat.isFile() && !isMarkdown(targetRel)) {
      copyFileRel(targetRel, stripPrefix(targetRel, strip))
      continue
    }
    if (stat.isDirectory() && isUnderAssetsFolder(targetRel, assetsFolders)) {
      copyDirRel(targetRel, stripPrefix(targetRel, strip), { onlyAssets: true, assetsFolders })
    }
  }
}

fs.rmSync(outDir, { recursive: true, force: true })
ensureDir(outDir)

const manifest = readManifest()
const strip = normalizeRel(manifest.strip_prefix || "") || ""
const dirs = Array.isArray(manifest.directories) ? manifest.directories : []
const files = Array.isArray(manifest.files) ? manifest.files : []
const assetsFolders = Array.isArray(manifest.assets_folders) ? manifest.assets_folders.map((s) => normalizeRel(s, false)).filter(Boolean) : ["assets"]
const excluded = Array.isArray(manifest.excluded) ? manifest.excluded.map((s) => normalizeRel(s, false)).filter(Boolean) : []

for (const relRaw of dirs) {
  const rel = normalizeRel(relRaw)
  if (rel === null) continue
  copyDirRel(rel, stripPrefix(rel, strip), { assetsFolders, excluded })
}
for (const relRaw of files) {
  const rel = normalizeRel(relRaw, false)
  if (!rel) continue
  copySelectedFile(rel, strip, assetsFolders)
}

console.log(`[content] public content prepared: dirs=${dirs.length}, files=${files.length}, strip_prefix=${strip || "<none>"}`)
