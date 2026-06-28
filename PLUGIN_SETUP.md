# Quartz 插件安装与镜像加速指南

## 问题1：国内访问 GitHub 慢导致插件安装失败

### 现状
- Quartz 插件通过 `git clone https://github.com/...` 从 GitHub 下载
- 国内访问 GitHub 很慢，经常超时
- **Quartz CLI 没有内置镜像源配置功能**

### 解决方案：Git 全局配置镜像加速

#### 方法1：使用 ghfast.top 镜像（推荐）

```bash
# 配置 Git 使用 HTTPS 代理镜像
git config --global url."https://ghfast.top/https://github.com/".insteadOf "https://github.com/"

# 验证配置
git config --global --get-regexp url

# 测试克隆速度
git clone https://github.com/quartz-community/note-properties.git /tmp/test-clone
```

#### 方法2：使用 ghproxy.com 镜像

```bash
git config --global url."https://ghproxy.com/https://github.com/".insteadOf "https://github.com/"
```

#### 方法3：取消镜像（恢复默认）

```bash
git config --global --unset url."https://ghfast.top/https://github.com/".insteadOf
```

---

## 问题2：当前插件安装状态

### 已安装插件
- 总共需要：46 个插件
- 已安装：27 个插件
- **note-properties 已安装**（✓）

### 检查方法

```bash
cd /home/ubuntu/Blog/apps/web/_vendor_quartz
node quartz/bootstrap-cli.mjs plugin list
```

---

## 问题3：note-properties 插件默认只显示部分字段

### 问题描述

Quartz 社区插件 `note-properties` 的默认配置只显示 `description`、`tags`、`aliases` 三个字段，其他 frontmatter 字段不显示。

### 根本原因

插件的 `package.json` 中的 `quartz.defaultOptions` 配置为：
```json
{
  "includeAll": false,
  "includedProperties": ["description", "tags", "aliases"]
}
```

这个默认配置的优先级高于用户在 `quartz.config.yaml` 中的配置。

### 解决方案

**在 `apps/web/Dockerfile` 的构建阶段自动修改插件配置**（已实现）：

```dockerfile
# 修复 note-properties 插件默认配置：将 includeAll 改为 true，清空 includedProperties
RUN if [ -f /quartz/.quartz/plugins/note-properties/package.json ]; then \
    sed -i 's/"includeAll": false/"includeAll": true/g' \
        /quartz/.quartz/plugins/note-properties/package.json && \
    sed -i 's/"includedProperties": \["description", "tags", "aliases"\]/"includedProperties": []/g' \
        /quartz/.quartz/plugins/note-properties/package.json && \
    echo "✓ note-properties plugin patched: includeAll=true, includedProperties=[]"; \
fi
```

### 修改内容

修改插件的 `package.json` 中的 `quartz.defaultOptions`：
- `includeAll: false` → `includeAll: true`
- `includedProperties: ["description", "tags", "aliases"]` → `includedProperties: []`

### 效果

现在所有笔记的 frontmatter 字段都会在属性面板中显示（除了 Quartz 内部字段）。

### 为什么这样做

1. **不污染 git 仓库**：不提交第三方插件代码到自己的仓库
2. **自动应用**：别人 clone 项目后，构建镜像时自动应用修改
3. **持久有效**：即使插件更新，构建时仍然会自动应用修改
4. **保持清爽**：`_vendor_quartz` 目录不纳入版本控制

### 用户配置

用户仍然可以通过 `quartz.config.yaml` 中的 `options` 覆盖默认配置：

```yaml
- source: github:quartz-community/note-properties
  enabled: true
  options:
    includeAll: true        # 显示所有字段
    excludedProperties:     # 排除某些字段
      - password
      - draft
```

### 验证方式

构建镜像时，终端会输出：
```
✓ note-properties plugin patched: includeAll=true, includedProperties=[]
```

笔记页面的属性面板会显示所有 frontmatter 字段。

---

## 问题4：完成剩余插件安装

### 使用镜像加速安装所有插件

```bash
cd /home/ubuntu/Blog/apps/web/_vendor_quartz

# 配置 Git 镜像
git config --global url."https://ghfast.top/https://github.com/".insteadOf "https://github.com/"

# 安装所有插件
node quartz/bootstrap-cli.mjs plugin install

# 完成后取消镜像（可选）
git config --global --unset url."https://ghfast.top/https://github.com/".insteadOf
```

---

## 部署建议

### 为其他用户提供预安装的插件

**方法1：提交插件到 Git（不推荐）**
- 插件体积大（几百MB）
- Git 仓库会非常臃肿

**方法2：提供安装脚本（推荐）**

创建 `setup-plugins.sh`：

```bash
#!/bin/bash
set -e

echo "配置 GitHub 镜像加速..."
git config --global url."https://ghfast.top/https://github.com/".insteadOf "https://github.com/"

echo "安装 Quartz 插件..."
cd apps/web/_vendor_quartz
node quartz/bootstrap-cli.mjs plugin install

echo "取消镜像配置..."
git config --global --unset url."https://ghfast.top/https://github.com/".insteadOf

echo "✓ 插件安装完成"
```

**方法3：使用 Docker 镜像（最佳）**
- 将完整的 `.quartz/plugins/` 打包到 Docker 镜像中
- 用户直接 pull 镜像，无需重新下载插件

---

## 总结

1. ✅ **国内访问慢**：通过 Git 全局配置镜像解决
2. ✅ **插件未全部安装**：使用镜像加速安装剩余 19 个插件
3. ⚠️ **note-properties 未渲染**：需要清除缓存重建或寻找替代方案
4. ✅ **部署建议**：提供安装脚本或 Docker 镜像

---

## 快速修复命令

```bash
# 1. 配置镜像
git config --global url."https://ghfast.top/https://github.com/".insteadOf "https://github.com/"

# 2. 完成插件安装
cd /home/ubuntu/Blog/apps/web/_vendor_quartz
node quartz/bootstrap-cli.mjs plugin install

# 3. 清除缓存并重建
cd /home/ubuntu/Blog/apps/web/_vendor_quartz
rm -rf .quartz-cache/ _public/
cd /home/ubuntu/Blog
docker compose run --rm -T web

# 4. 取消镜像（可选）
git config --global --unset url."https://ghfast.top/https://github.com/".insteadOf
```
