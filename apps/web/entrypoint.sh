#!/bin/sh
# Quartz 构建入口：把挂载的 /data/notes 作为内容源，构建静态站到 /public。
# 构建完成即退出（Quartz 是构建工具，非常驻服务）；rebuild 时由编排重跑本容器。
set -eu

NOTES_DIR="${NOTES_DIR:-/data/notes}"
OUTPUT_DIR="${OUTPUT_DIR:-/public}"

# GitHub 模式下只把 notes_github_prefix 子目录作为内容源，避免把仓库里的
# 非发布内容（My/Study/Template 等私密目录）构建成网页公网可见。
# prefix 为空（COS 模式或仓库根即博客）时回退到整个 notes_dir。
CONTENT_SRC="$NOTES_DIR"
if [ -n "$NOTES_GITHUB_PREFIX" ]; then
  SUBDIR="$NOTES_DIR/$(echo "$NOTES_GITHUB_PREFIX" | sed 's#^/*##; s#/*$##')"
  if [ -d "$SUBDIR" ]; then
    # 安全兜底：防止 prefix 含 .. 跨越 NOTES_DIR 向上遍历
    RESOLVED="$(cd "$SUBDIR" && pwd -P)"
    if [ "${RESOLVED##"$NOTES_DIR"}" != "$RESOLVED" ]; then
      CONTENT_SRC="$SUBDIR"
    else
      echo "[entrypoint] 危险：$SUBDIR 不在 $NOTES_DIR 内，拒绝使用，回退到 $NOTES_DIR"
    fi
  else
    echo "[entrypoint] 警告：子目录 $SUBDIR 不存在，回退到 $NOTES_DIR"
  fi
fi

# content 指向实际内容源：docs-research 确认 v5 推荐 symlink 方式接外部内容
rm -rf /quartz/content
if [ -d "$CONTENT_SRC" ] && [ -n "$(ls -A "$CONTENT_SRC" 2>/dev/null || true)" ]; then
    ln -s "$CONTENT_SRC" /quartz/content
else
    # 内容源为空时建空目录，保证构建不因缺 content 失败（骨架期容错）
    echo "[entrypoint] 警告：$CONTENT_SRC 为空，使用空 content 构建"
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

echo "[entrypoint] 同步产物到 $OUTPUT_DIR"
find "$OUTPUT_DIR" -mindepth 1 -delete 2>/dev/null || true
cp -a "$BUILD_DIR"/. "$OUTPUT_DIR"/
# 修复权限：nginx worker 以 nginx 用户运行，需要 other 可读
chmod -R o+rX "$OUTPUT_DIR"
echo "[entrypoint] 构建完成"
