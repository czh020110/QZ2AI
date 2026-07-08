#!/bin/sh
# Quartz 构建入口：把挂载的 /data/notes 作为内容源，构建静态站到 /public。
# 构建完成即退出（Quartz 是构建工具，非常驻服务）；rebuild 时由编排重跑本容器。
set -eu

NOTES_DIR="${NOTES_DIR:-/data/notes}"
OUTPUT_DIR="${OUTPUT_DIR:-/public}"

# 根据 API 生成的公开内容清单准备 Quartz 内容目录。
# 清单缺失时脚本会按旧 prefix 规则 fallback,避免首次启动时突然全量发布。
PUBLIC_CONTENT_DIR=/quartz/_content_public
CONTENT_MANIFEST="/data_store/public_content_manifest.json"
PUBLIC_CONTENT_DIR="$PUBLIC_CONTENT_DIR" CONTENT_MANIFEST="$CONTENT_MANIFEST" node /quartz/scripts/build-public-content.cjs

# content 指向过滤后的公开内容源。
rm -rf /quartz/content
if [ -d "$PUBLIC_CONTENT_DIR" ] && [ -n "$(ls -A "$PUBLIC_CONTENT_DIR" 2>/dev/null || true)" ]; then
    ln -s "$PUBLIC_CONTENT_DIR" /quartz/content
else
    echo "[entrypoint] 警告：$PUBLIC_CONTENT_DIR 为空，使用空 content 构建"
    mkdir -p /quartz/content
fi

# 运行时注入博客语言:从 data_store/appearance.json 读 blog_locale,覆盖 quartz.config.yaml 的 locale。
# quartz.config.yaml 在镜像 build 阶段 COPY 进来(locale 固定),rebuild 不重读;
# 故每次构建前根据 appearance.json 单一数据源 sed 改 locale,使语言切换免重建镜像。
APPEARANCE_FILE="/data_store/appearance.json"
if [ -f "$APPEARANCE_FILE" ]; then
  BLOG_LOCALE=$(node -e "try{console.log(require('$APPEARANCE_FILE').blog_locale||'en-US')}catch{console.log('en-US')}" 2>/dev/null || echo "en-US")
else
  BLOG_LOCALE="en-US"
fi
# 白名单校验,防 appearance.json 的 locale 字段被篡改注入 sed
case "$BLOG_LOCALE" in
  en-US|zh-CN|ja-JP) ;;
  *) BLOG_LOCALE="en-US" ;;
esac
sed -i "s/^  locale: .*/  locale: ${BLOG_LOCALE}/" /quartz/quartz.config.yaml
echo "[entrypoint] 博客语言 locale=${BLOG_LOCALE}"

echo "[entrypoint] 开始构建 Quartz → $OUTPUT_DIR"
# Quartz 构建前会尝试 rmdir 输出目录；$OUTPUT_DIR 是挂载点不可删，
# 故先构建到内部目录，再把产物同步进挂载卷（清空卷内容而非删卷本身）。
BUILD_DIR=/quartz/_public
rm -rf "$BUILD_DIR"
npx quartz build -o "$BUILD_DIR"

# Quartz 默认不生成 robots.txt；这里按 baseUrl 生成一份，允许全站抓取并指向 sitemap。
# Google 抓取前必查 robots.txt，缺失会返回 404 并对站点可信度产生负面影响。
BASE_URL=$(sed -n 's/^[[:space:]]*baseUrl:[[:space:]]*\([^[:space:]]*\).*/\1/p' /quartz/quartz.config.yaml | head -1)
case "$BASE_URL" in
  http://*|https://*) ROBOTS_BASE="$BASE_URL" ;;
  *)                  ROBOTS_BASE="https://${BASE_URL}" ;;
esac
if [ -n "$BASE_URL" ]; then
  cat > "$BUILD_DIR/robots.txt" <<EOF
User-agent: *
Allow: /

Sitemap: ${ROBOTS_BASE}/sitemap.xml
EOF
  echo "[entrypoint] 已生成 robots.txt (sitemap=${ROBOTS_BASE}/sitemap.xml)"
fi

echo "[entrypoint] 同步产物到 $OUTPUT_DIR"
find "$OUTPUT_DIR" -mindepth 1 -delete 2>/dev/null || true
cp -a "$BUILD_DIR"/. "$OUTPUT_DIR"/
# 修复权限：nginx worker 以 nginx 用户运行，需要 other 可读
chmod -R o+rX "$OUTPUT_DIR"
echo "[entrypoint] 构建完成"
