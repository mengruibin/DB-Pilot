# 浅色/深色主题自主切换 — 开发计划

## 1. 概述

### 1.1 背景

DB-Pilot 目前仅有深色主题，用户需要自主切换浅色/深色主题。项目已存在 `[data-theme="light"]` CSS 变量定义但未激活（死代码），Naive UI `darkTheme` 硬编码在 `App.vue` 中。

### 1.2 目标

实现用户可自主切换的浅色/深色主题功能，切换状态持久化到 localStorage，刷新页面后保持用户偏好。

### 1.3 设计规范

浅色主题严格遵循用户提供的设计规范：

| 属性 | 颜色值 | 说明 |
|------|--------|------|
| 页面背景 | `#F8FAFC` | 极浅灰蓝 |
| 侧边栏/卡片背景 | `#FFFFFF` | 纯白 |
| 用户气泡背景 | `#E8EAEF` | 略深浅灰 |
| 用户气泡边框 | `#D1D5DB` | 浅灰 |
| 工具调用卡片边框 | `#BFDBFE` | 蓝 |
| 工具调用卡片背景 | `rgba(191, 219, 254, 0.2)` | 浅蓝半透明 |
| 最终回答背景 | `#F1F5F9` | 中性浅灰蓝 |
| 最终回答边框 | `#E2E8F0` | 浅灰 |
| 代码块背景 | `#0F172A` | 深蓝黑 |
| 代码块文字 | `#60A5FA` | 亮蓝 |
| 分割线 | `#E2E8F0` | 浅灰 |
| 文本主色 | `#334155` | 深灰 |
| 文本次色 | `#64748B` | 中灰 |
| 文本淡色 | `#94A3B8` | 浅灰 |

---

## 2. 任务分解

### 任务 1：新建 Theme Store

**文件**: `frontend/src/stores/theme.ts`（新建）

**内容**:
- Pinia store `useThemeStore`
- `theme` ref: `'dark' | 'light'`，初始值从 `localStorage.getItem('db-pilot-theme')` 读取，默认 `'dark'`
- `naiveTheme` computed: dark → `darkTheme` (from naive-ui)，light → `null` (Naive UI 默认为浅色)
- `isDark` computed: `theme === 'dark'`
- `initTheme()`: 在 App.vue `onMounted` 中调用，设置 `document.documentElement.dataset.theme`
- `toggleTheme()`: 切换 `theme`，写入 `localStorage`，更新 `document.documentElement.dataset.theme`

**验收标准**:
- [ ] store 可从任何组件导入使用
- [ ] `toggleTheme()` 后 `document.documentElement.dataset.theme` 正确切换
- [ ] 刷新页面后主题偏好保持
- [ ] TypeScript 类型检查通过

---

### 任务 2：更新 Naive UI 主题配置

**文件**: `frontend/src/utils/theme.ts`

**改动**:
- 将现有 `themeOverrides` 重命名为 `darkThemeOverrides`
- 新增 `lightThemeOverrides`，覆盖 Naive UI 在浅色模式下的关键颜色：

```typescript
export const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    bodyColor: '#F8FAFC',
    cardColor: '#FFFFFF',
    modalColor: '#FFFFFF',
    popoverColor: '#FFFFFF',
    tableColor: '#FFFFFF',
    inputColor: '#FFFFFF',
    borderColor: '#E2E8F0',
    hoverColor: '#F1F5F9',
    textColor1: '#334155',
    textColor2: '#64748B',
    textColor3: '#94A3B8',
    // ... 等
  },
  // Button, Input, Select, DataTable, Dialog, Card, Collapse, Switch, Message, Tag
}
```

**验收标准**:
- [ ] 深色模式 Naive UI 组件样式不变
- [ ] 浅色模式 Naive UI 组件切换为浅色风格
- [ ] TypeScript 类型检查通过

---

### 任务 3：更新 App.vue — 动态主题 + 校准浅色 CSS 变量

**文件**: `frontend/src/App.vue`

**改动**:

#### 3.1 脚本部分
- 导入 `useThemeStore` 和 `lightTheme` (from naive-ui)
- `n-config-provider` 的 `:theme` 绑定为 `themeStore.naiveTheme`
- `:theme-overrides` 根据 `themeStore.isDark` 选择 `darkThemeOverrides` 或 `lightThemeOverrides`

```vue
<script setup lang="ts">
import { onMounted } from 'vue'
import { darkTheme, lightTheme, zhCN, NNotificationProvider } from 'naive-ui'
import AppLayout from '@/components/common/AppLayout.vue'
import { darkThemeOverrides, lightThemeOverrides } from '@/utils/theme'
import { useThemeStore } from '@/stores/theme'

const themeStore = useThemeStore()

onMounted(() => {
  themeStore.initTheme()
})
</script>

<template>
  <n-config-provider
    :theme="themeStore.naiveTheme"
    :theme-overrides="themeStore.isDark ? darkThemeOverrides : lightThemeOverrides"
    :locale="zhCN"
  >
    <n-notification-provider>
      <AppLayout />
    </n-notification-provider>
  </n-config-provider>
</template>
```

#### 3.2 浅色 CSS 变量校准（`[data-theme="light"]` 块）

按设计规范更新以下变量：

| 变量 | 当前值 | 更新为 |
|------|--------|--------|
| `--bg-primary` | `#F9FBFC` | `#F8FAFC` |
| `--chat-bg` | `#F9FBFC` | `#F8FAFC` |
| `--chat-user-bg` | `#F1F3F5` | `#E8EAEF` |
| `--chat-user-border` | `#E9ECEF` | `#D1D5DB` |
| `--chat-answer-bg` | `transparent` | `#F1F5F9` |
| `--chat-tool-bg` | `rgba(191,219,254,0.3)` | `rgba(191,219,254,0.2)` |
| `--chat-result-bg` | `#F9FAFB` | `#F8FAFC` |
| `--chat-result-border` | `#E5E7EB` | `#E2E8F0` |

同步更新 `frontend/index.html` 中浅色主题 CSS 变量（如有）。

**验收标准**:
- [ ] 页面加载时根据 localStorage 自动应用正确主题
- [ ] Naive UI 主题随 `data-theme` 同步切换
- [ ] 浅色模式 CSS 变量符合设计规范
- [ ] 深色模式 CSS 变量保持不变
- [ ] TypeScript 类型检查通过

---

### 任务 4：AppLayout.vue — 内容区背景适配主题

**文件**: `frontend/src/components/common/AppLayout.vue`

**改动**:
- 将 `.content` 中硬编码的深色网格点背景改为使用 CSS 变量：

```css
/* 替换现有硬编码 */
.content {
  background:
    linear-gradient(var(--grid-dot-color, rgba(30, 41, 59, 0.3)) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-dot-color, rgba(30, 41, 59, 0.3)) 1px, transparent 1px);
  background-size: 24px 24px;
}
```

- 在 `App.vue` 中为深色/浅色分别定义 `--grid-dot-color`:
  - 深色: `rgba(30, 41, 59, 0.3)`（现有值）
  - 浅色: `rgba(226, 232, 240, 0.5)`（极浅灰网格点）

**验收标准**:
- [ ] 深色模式网格点不变
- [ ] 浅色模式网格点为极浅灰，视觉效果协调

---

### 任务 5：Sidebar.vue — 添加主题切换按钮

**文件**: `frontend/src/components/common/Sidebar.vue`

**改动**:
- 在 `.sidebar-footer` 中、设置按钮上方新增主题切换行
- 导入 `useThemeStore`
- 图标：深色模式下显示太阳图标（切换到浅色），浅色模式下显示月亮图标（切换到深色）

```vue
<script setup lang="ts">
// 新增导入
import { useThemeStore } from '@/stores/theme'
const themeStore = useThemeStore()

// 新增图标映射
const iconMap: Record<string, string> = {
  // ... existing icons
  sun: `<svg>...</svg>`,    // 太阳图标
  moon: `<svg>...</svg>`,   // 月亮图标
}
</script>

<template>
  <!-- sidebar-footer 中、设置按钮上方 -->
  <div class="sidebar-footer">
    <button class="nav-item theme-toggle-btn" @click="themeStore.toggleTheme()">
      <span class="nav-icon" v-html="themeStore.isDark ? iconMap['sun'] : iconMap['moon']"></span>
      <span class="nav-label">{{ themeStore.isDark ? '浅色模式' : '深色模式' }}</span>
    </button>
    <router-link to="/settings" class="nav-item settings-btn">
      <!-- 现有设置按钮 -->
    </router-link>
  </div>
</template>
```

**验收标准**:
- [ ] 按钮显示在侧边栏底部、设置按钮上方
- [ ] 点击切换主题，图标和文字同步更新
- [ ] 按钮样式与现有 `nav-item` 一致
- [ ] hover 效果正常

---

## 3. 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/stores/theme.ts` | **新建** | Pinia theme store |
| `frontend/src/utils/theme.ts` | 修改 | 新增 `lightThemeOverrides`，导出 `darkThemeOverrides` |
| `frontend/src/App.vue` | 修改 | 动态 Naive UI 主题 + 校准浅色 CSS 变量 |
| `frontend/src/components/common/AppLayout.vue` | 修改 | 网格背景使用 CSS 变量适配主题 |
| `frontend/src/components/common/Sidebar.vue` | 修改 | 底部添加主题切换按钮 |

---

## 4. 任务状态

| 任务 | 状态 | 开始时间 | 完成时间 |
|------|------|----------|----------|
| 任务 1：Theme Store | ✅ 已完成 | 2026-07-09 | 2026-07-09 |
| 任务 2：Naive UI 主题配置 | ✅ 已完成 | 2026-07-09 | 2026-07-09 |
| 任务 3：App.vue 更新 | ✅ 已完成 | 2026-07-09 | 2026-07-09 |
| 任务 4：AppLayout 背景适配 | ✅ 已完成 | 2026-07-10 | 2026-07-10 |
| 任务 5：Sidebar 切换按钮 | ✅ 已完成 | 2026-07-10 | 2026-07-10 |

状态标记: 📋 待开始 | 🔄 进行中 | ✅ 已完成 | ❌ 已取消

---

## 5. 验证流程

1. 启动开发服务器：`cd frontend && npm run dev`
2. 打开浏览器访问 `http://localhost:5173`
3. **默认主题**：页面加载为深色主题（与现有样式完全一致）
4. **切换浅色**：点击侧边栏底部「浅色模式」按钮 → 界面切换为浅色
5. **刷新保持**：刷新页面 → 主题保持为浅色（localStorage 持久化验证）
6. **切回深色**：点击「深色模式」按钮 → 切回深色
7. **浅色模式视觉检查**：
   - 侧边栏白色背景 `#FFFFFF`
   - 主内容区背景 `#F8FAFC`
   - 用户气泡 `#E8EAEF`
   - 工具调用卡片蓝色调 `#BFDBFE`
   - 最终回答区 `#F1F5F9`
   - Naive UI 组件（按钮、输入框等）为浅色风格
   - 代码块保持深色背景
8. **功能验证**：在浅色模式下发送对话消息，确认所有 AI 响应组件（思考面板、工具调用卡片、最终回答）渲染正常
9. 运行 TypeScript 类型检查：`npx vue-tsc --noEmit`
