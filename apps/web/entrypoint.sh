#!/bin/sh
# Quartz 构建入口：把挂载的 /data/notes 作为内容源，构建静态站到 /public。
# 构建完成即退出（Quartz 是构建工具，非常驻服务）；rebuild 时由编排重跑本容器。
set -eu

NOTES_DIR="${NOTES_DIR:-/data/notes}"
OUTPUT_DIR="${OUTPUT_DIR:-/public}"

# content 指向挂载的笔记目录：docs-research 确认 v5 推荐 symlink 方式接外部内容
rm -rf /quartz/content
if [ -d "$NOTES_DIR" ] && [ -n "$(ls -A "$NOTES_DIR" 2>/dev/null || true)" ]; then
    ln -s "$NOTES_DIR" /quartz/content
else
    # 内容源为空时建空目录，保证构建不因缺 content 失败（骨架期容错）
    echo "[entrypoint] 警告：$NOTES_DIR 为空，使用空 content 构建"
    mkdir -p /quartz/content
fi

echo "[entrypoint] 开始构建 Quartz → $OUTPUT_DIR"
# Quartz 构建前会尝试 rmdir 输出目录；$OUTPUT_DIR 是挂载点不可删，
# 故先构建到内部目录，再把产物同步进挂载卷（清空卷内容而非删卷本身）。
BUILD_DIR=/quartz/_public
rm -rf "$BUILD_DIR"
npx quartz build -o "$BUILD_DIR"

echo "[entrypoint] 同步产物到 $OUTPUT_DIR"
find "$OUTPUT_DIR" -mindepth 1 -delete 2>/dev/null || true
cp -a "$BUILD_DIR"/. "$OUTPUT_DIR"/
echo "[entrypoint] 构建完成"
