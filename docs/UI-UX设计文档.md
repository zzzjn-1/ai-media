# UI/UX 设计文档

> 版本：V1.0.0 ｜ 日期：2026-10-12 ｜ 对应计划书 T5.4「UI/UX 设计文档」
>
> 适用范围：`frontend/`（React + TypeScript + Vite + Ant Design 5 + Tailwind CSS）

---

## 1. 设计目标与原则

| 原则 | 落地方式 |
|---|---|
| 一致性 | 全站统一设计令牌（色彩/圆角/阴影/字号），六页共用 `PageHeader`/`EmptyState` 组件，交互反馈统一走 AntD `message` |
| 低认知负荷 | 「热点→选题→生成→适配→导出」核心闭环由侧边栏顺序导航 + `FlowContext` 跨页流转串联，用户始终知道自己在流程哪一步 |
| 可见的状态 | 加载（Skeleton/Spin）、空（EmptyState）、演示模式（顶部 amber 横幅）、错误（message + 表单校验）四类状态全覆盖 |
| 无障碍 | 次级文字对比度 ≥ WCAG AA（4.5:1）、全局 `focus-visible` 焦点环、支持 `prefers-reduced-motion`、AntD zhCN 语言包 |
| 演示优先 | `VITE_USE_MOCK=true` 时无需后端即可完整体验全链路，页面顶部明确提示当前为演示数据 |

## 2. 技术栈与主题

- **组件库**：Ant Design 5（`ConfigProvider` 注入 `locale=zhCN`、`colorPrimary=#185fa5`）
- **样式方案**：Tailwind CSS（**关闭 preflight**，避免与 AntD v5 重置冲突）+ `index.css` 最小重置
- **图标**：`@ant-design/icons`（导航/操作）+ 自绘线性 SVG 图标集 `src/components/icons.tsx`（Flame/Inbox/Spark/Doc/LogoMark，Heroicons 风格）
- **路由**：react-router-dom 6，六页 `React.lazy` 按路由拆包，`Suspense` 回落「页面加载中…」

### 2.1 设计令牌（`tailwind.config.js` / `index.css`）

| 类别 | 令牌 | 值 | 用途 |
|---|---|---|---|
| 品牌 | `brand` | `#185fa5` | 主色（按钮/链接/高亮），AntD `colorPrimary` 同值 |
| | `brand-700` | `#124a82` | hover/按压加深 |
| | `brand-soft` | `#e6f1fb` | 选中底色/浅色强调背景 |
| 文本 | `ink` | `#1f2329` | 主文本 |
| | `muted` | `#475569` | 次级文本（约 7:1，过 AA） |
| | `muted-soft` | `#64748b` | 辅助/占位（仅用于非正文） |
| 线条 | `line` / `line-strong` | `#e5e7eb` / `#d8dee6` | 卡片描边/分隔线 |
| 背景 | `bg` | `#f4f6f9` | 页面画布；卡片 `#fff` |
| 语义 | `ok` / `warn` / `danger` | `#0f6e56` / `#854f0b` / `#a32d2d` | 成功/警示/错误（均过 AA） |
| 圆角 | `card` / `lg` | `12px` / `10px` | 卡片/控件 |
| 阴影 | `card` / `soft` / `float` | 三级 elevation | 静态卡/悬浮卡/浮层 |
| 字体 | 系统栈 | `-apple-system, PingFang SC, Microsoft YaHei` | 中文优先，无 Web 字体开销 |
| 基准 | 字号/行高 | `14px` / `1.6` | 后台工具型产品密度 |

## 3. 信息架构与布局

```
┌────────────────────────────────────────────────────────┐
│ [演示模式横幅：仅 Mock 模式显示，28px，amber]              │
├────────────┬───────────────────────────────────────────┤
│ 品牌区      │  PageHeader（标题 + 副标题 + 右侧操作槽）      │
│ LogoMark   │ ┌───────────────────────────────────────┐ │
│ 创作助手    │ │                                       │ │
│            │ │           页面内容区                    │ │
│ ── 导航 ──  │ │   （卡片/表格/表单，最大宽度约束）        │ │
│ ▸ 素材库管理 │ │                                       │ │
│ ▸ 热点看板   │ └───────────────────────────────────────┘ │
│ ▸ 选题列表   │                                           │
│ ▸ 文案生成   │                                           │
│ ▸ 平台适配   │                                           │
│ ▸ 账号人设   │                                           │
│ ── 用户 ──  │                                           │
│ 头像+用户名  │                                           │
│ 退出        │                                           │
└────────────┴───────────────────────────────────────────┘
   232px 固定侧边栏            自适应主内容区
```

- **侧边栏**：232px 固定宽度；当前页左侧 3px 品牌色高亮指示条；导航项图标 + 文案；底部用户区（首字母头像、用户名、退出）。
- **主内容区**：`PageHeader` 统一六页头部；内容以白卡（`shadow-card`）承载，卡片间距 14px。
- **登录页**：居中卡片式登录（用户名/密码），宽屏下左侧渐变品牌展示区（LogoMark + 产品能力清单），演示账号提示 `demo_seed`。

## 4. 页面设计（六页 + 登录）

| 页面 | 路由 | 核心元素 | 关键交互 |
|---|---|---|---|
| 登录 | `/login` | 品牌区 + 登录表单 | 任意账号密码可登录（演示模式）；登录成功写入 JWT 到 `AuthContext`；后端 401 全局监听后自动退出回登录 |
| 素材库管理 | `/materials` | 统计条 + 搜索/筛选（领域×平台×风格）+ 素材表格 + 导入抽屉 + 分块预览抽屉 | 语义检索（q）与列表模式切换；分块预览只读展示解密分块；空列表走 `EmptyState` |
| 热点看板 | `/hotspots` | 领域筛选 + 热点卡片流 | 热度/趋势可视化（线性 FlameIcon，不用 emoji）；「生成选题」携带热点进入选题页（FlowContext） |
| 选题列表 | `/topics` | 选题表格 + 生成选题表单 | 一键选用跳转生成页；生成中 Spin 反馈 |
| 文案生成对比 | `/generate` | 版本并排对比卡 | `copy/generate` SSE 流式推送（进度→版本→done）；每版本含标题/正文/优化稿/标签/一致性报告/同质化建议；支持风格优化与多平台适配结果 |
| 平台适配预览 | `/adapt` | 平台 Tab（weixin/xhs/douyin） | 按 `platform_rules` 硬性规范渲染预览；导出 txt/docx |
| 账号人设 | `/persona` | 人设表单（定位/语气/领域多选） | 领域多选下拉动态引用 `DOMAINS` 唯一真源；保存后进入 `AuthContext`/后端 persona |

## 5. 组件规范

| 组件 | 职责 | 规范 |
|---|---|---|
| `PageHeader` | 六页统一头部 | 标题（16px/600）+ 副标题（muted）+ 右侧操作槽 |
| `EmptyState` | 统一空状态 | 线性图标 + 一句说明 + 一个主操作按钮 |
| `RouteFallback` | 懒加载回落 | 居中「页面加载中…」，muted 色 |
| `useMessage` | 全局提示 | 基于 AntD `App.useApp`，消除 React 18 静态 `message.*` context 警告；**禁止**再用 `alert()` |
| `LogoMark` | 品牌标记 | 侧边栏/登录页复用，保证品牌一致 |
| `FlowContext` | 跨页流转 | 承载 taskId/topicTitle/targetPlatforms，串联核心闭环，避免 URL 传参碎片化 |

## 6. 交互与反馈规范

1. **反馈**：所有操作结果必须可见——成功/失败走 `message`，长任务走 `Spin`/进度，禁止静默失败。
2. **破坏性操作**：删除类操作走 AntD `Popconfirm` 二次确认。
3. **表单**：AntD `Form` 校验（必填/格式），错误信息内联展示；原生 `<select>` 已全部替换为 AntD `Select`（视觉语言统一）。
4. **键盘与焦点**：非 AntD 控件补 `focus-visible` 焦点环（`rgba(24,95,165,.22)` 3px）；Tab 顺序遵循 DOM 序。
5. **动效**：卡片 hover 阴影/边框/≤2px 微位移，≤300ms；全局尊重 `prefers-reduced-motion`。

## 7. 响应式与兼容

- **目标场景**：桌面端后台工具（≥1024px 完整体验）；侧边栏 232px 固定，主内容区自适应。
- **移动端**：未做专项布局（侧边栏不折叠），属已知限制——计划书 M5 后续可迭代为可折叠侧边栏 + 底部导航。
- **浏览器**：Chromium / Edge / Firefox / Safari 近两年主流版本；构建产物为静态文件，无 IE 兼容目标。

## 8. 性能设计（T5.2 拆包）

| 措施 | 效果（vite build 实测） |
|---|---|
| 六页 `React.lazy` 按路由拆包 | 入口 chunk 1.05MB → 674KB |
| `manualChunks` 拆 `react-vendor` / `antd-vendor` | 入口 chunk 降至 **29KB（gzip 14.3KB）**；react-vendor 163.7KB、antd-vendor 838.4KB 按需加载并独立缓存 |
| 关闭 Tailwind preflight、无 Web 字体 | CSS 17.1KB（gzip 4.35KB），无字体网络请求 |

> 说明：antd-vendor 838KB 为 AntD 全量引入的固有体积（多页面组件并集），首次进入业务路由时加载一次，之后命中缓存；后续如需进一步压缩可评估 AntD 按需引入或轻量组件库替换（非本期范围）。

## 9. 验收自查

- [x] 设计令牌全覆盖，无散落硬编码颜色（语义色/品牌色均在令牌表内）
- [x] 六页头部/空状态/提示反馈统一
- [x] WCAG AA：muted 7:1、语义色均 ≥4.5:1
- [x] 登录/六页/人设在 Mock 与真实后端两种模式下视觉与功能一致（Mock 模式仅多一条演示横幅）
