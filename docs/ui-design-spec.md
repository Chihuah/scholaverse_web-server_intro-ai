# Scholaverse UI 設計規範交接文件

> 生成日期：2026-02-22
> 來源：`scholaverse/` React SPA 前端 + `system-spec.md` 系統規格書
> 目標技術棧：**Jinja2 + HTMX + Tailwind CSS (CDN)**，部署於 vm-web-server (FastAPI)

---

## 1. 視覺設計系統

### 1.1 CSS 自訂屬性（`:root` 變數）

以下變數定義於 `app/static/css/style.css`（或 `base.html` 的 `<style>` 標籤內）：

```css
:root {
  /* ── 背景 ── */
  --rpg-bg-dark:        #0f0c29;   /* 頁面主背景（深紫夜空） */
  --rpg-bg-panel:       #1e3a1a;   /* 面板/卡片背景（深綠） */
  --rpg-bg-card:        #1a2e14;   /* 較深的卡片/表頭背景 */
  --rpg-bg-input:       #0d1a0a;   /* 進度條背景槽、輸入框底 */
  --rpg-bg-panel-light: #2d3a1a;   /* 淺綠面板（備用） */

  /* ── 金色系 ── */
  --rpg-gold:           #d4a847;   /* 主要金色（標題、icon、nav active） */
  --rpg-gold-dark:      #8b6914;   /* 深金/黃銅（邊框、分隔線） */
  --rpg-gold-bright:    #ffd700;   /* 亮金（代幣、高亮按鈕文字） */

  /* ── 卡牌邊框 ── */
  --rpg-border-gold:    #ffd700;   /* 金邊卡牌（等級 7+） */
  --rpg-border-silver:  #c0c0c0;   /* 銀邊卡牌（等級 4-6） */
  --rpg-border-copper:  #b87333;   /* 銅邊卡牌（等級 1-3） */

  /* ── 文字 ── */
  --rpg-text-primary:   #e8d5b0;   /* 主要內文（暖白/羊皮紙色） */
  --rpg-text-secondary: #a89070;   /* 次要/說明文字（淡棕） */

  /* ── 語意色 ── */
  --rpg-success:        #4ade80;   /* 成功/已完成 */
  --rpg-warning:        #f59e0b;   /* 警告/進行中 */
  --rpg-danger:         #ef4444;   /* 危險/低進度 */
  --rpg-mana-blue:      #6366f1;   /* 藍紫色（備用） */
}
```

### 1.2 字型規範

```html
<!-- base.html <head> 中引入 Google Fonts -->
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Noto+Sans+TC:wght@400;700;900&display=swap" rel="stylesheet">
```

| 用途 | CSS class | font-family | 說明 |
|------|-----------|-------------|------|
| 像素風標題/Logo | `.font-pixel` | `"Press Start 2P", monospace` | 品牌名稱、裝飾性文字 |
| 中文內文 | `.font-tc` | `"Noto Sans TC", sans-serif` | 所有可讀性文字（預設全站） |

```css
.font-pixel { font-family: "Press Start 2P", monospace; }
.font-tc    { font-family: "Noto Sans TC", sans-serif; }
```

### 1.3 邊框 / 圓角 / 陰影規則

| 元素 | 邊框 | 圓角 | 陰影 |
|------|------|------|------|
| 面板容器 | `2px solid var(--rpg-gold-dark)` | `rounded`（4px） | 無 |
| 卡牌圖片 | `3px solid <邊框色>` | `rounded`（4px） | 最新卡/金邊：`box-shadow: 0 0 12px 2px <邊框色>66` |
| 按鈕（普通） | `2px solid var(--rpg-gold-dark)` | `rounded`（4px） | 無 |
| 按鈕（高亮） | `2px solid var(--rpg-gold)` | `rounded`（4px） | 無 |
| 分隔線 | 高度 `0.5`（2px），`bg-[var(--rpg-gold-dark)]` | — | — |
| 鎖定單元 | `2px solid #333` | `rounded`（4px） | 無 |

### 1.4 進度條樣式

```html
<!-- 通用進度條結構 -->
<div class="w-full h-2 rounded-sm bg-[var(--rpg-bg-input)] overflow-hidden">
  <div class="h-full rounded-sm bg-[var(--rpg-success)]" style="width: 80%"></div>
</div>
```

顏色規則（依百分比）：
- `>= 70%` → `bg-[var(--rpg-success)]`（綠）
- `40% ~ 69%` → `bg-[var(--rpg-warning)]`（琥珀）
- `< 40%` → `bg-[var(--rpg-danger)]`（紅）

### 1.5 Icon 系統

使用 **Lucide Icons**。在 Jinja2 環境中，建議以 SVG inline 或 Lucide CDN 引入：

```html
<!-- 方法 1：CDN（推薦） -->
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<script>lucide.createIcons();</script>

<!-- 使用方式 -->
<i data-lucide="shield" class="w-6 h-6 text-[var(--rpg-gold)]"></i>
```

**各頁面使用的 icon：**

| Icon 名稱 | 使用位置 |
|-----------|---------|
| `shield` | Logo |
| `layout-dashboard` | 儀表板 nav |
| `image` | 我的卡牌 nav、快速操作 |
| `swords` | 角色大廳 nav/header |
| `scroll-text` | 學習歷程 nav/header |
| `settings` | 管理後台 nav/header |
| `coins` | 代幣顯示 |
| `menu` / `x` | 手機 hamburger / 關閉 |
| `crown` | 金色邊框標示 |
| `lock` | 未解鎖單元 |
| `lock-open` / `unlock` | 解鎖中 / 已解鎖 |
| `star` | 最新卡牌標記 |
| `sparkles` | 生成卡牌按鈕 |
| `check-circle-2` | 已完成狀態 |
| `clock` | 進行中狀態 |
| `users` | 註冊學生統計 |
| `bar-chart-3` | 平均進度統計 |
| `file-spreadsheet` | 已生成卡牌統計 |
| `upload` | 匯入 CSV 按鈕 |
| `trending-up` | 統計報表按鈕 |
| `user-check` | 學生管理標題 |
| `alert-triangle` | 低進度警告 |

### 1.6 scrollbar-hide 工具類

```css
.scrollbar-hide {
  -ms-overflow-style: none;
  scrollbar-width: none;
}
.scrollbar-hide::-webkit-scrollbar {
  display: none;
}
```

---

## 2. 全域佈局（Layout）

### 2.1 HTML 結構

對應 React 的 `Layout.tsx`，Jinja2 等價於 `base.html`：

```html
<!-- base.html -->
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Scholaverse - {% block title %}{% endblock %}</title>

  <!-- Tailwind CDN -->
  <script src="https://cdn.tailwindcss.com"></script>

  <!-- Google Fonts -->
  <link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Noto+Sans+TC:wght@400;700;900&display=swap" rel="stylesheet">

  <!-- Lucide Icons -->
  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>

  <!-- HTMX -->
  <script src="https://unpkg.com/htmx.org@2/dist/htmx.min.js"></script>

  <!-- Custom CSS (CSS 變數 + 工具類) -->
  <link rel="stylesheet" href="/static/css/style.css">
</head>
<body class="h-screen font-tc bg-[var(--rpg-bg-dark)]">
  <div id="app" class="relative flex h-full overflow-hidden">

    <!-- Mobile overlay backdrop (JS toggle) -->
    <div id="sidebar-backdrop"
         class="fixed inset-0 z-40 bg-black/60 lg:hidden hidden"
         onclick="closeSidebar()">
    </div>

    <!-- Sidebar -->
    <aside id="sidebar"
           class="flex flex-col w-60 shrink-0 h-full gap-4 px-3 py-4
                  bg-[var(--rpg-bg-panel)] border-r-2 border-[var(--rpg-gold-dark)]
                  fixed inset-y-0 left-0 z-50 transition-transform duration-200
                  -translate-x-full lg:static lg:translate-x-0">

      <!-- Close button (mobile) -->
      <button class="self-end p-1 lg:hidden text-[var(--rpg-gold)]"
              onclick="closeSidebar()">
        <i data-lucide="x" class="w-5 h-5"></i>
      </button>

      <!-- Logo -->
      <div class="flex flex-col items-center gap-1 px-2 py-2">
        <i data-lucide="shield" class="w-8 h-8 text-[var(--rpg-gold)]"></i>
        <span class="font-pixel text-[11px] tracking-widest text-center text-[var(--rpg-gold)]">
          SCHOLAVERSE
        </span>
        <span class="font-tc text-[9px] font-bold tracking-widest text-center text-[var(--rpg-text-secondary)]">
          學習冒險之旅
        </span>
      </div>

      <div class="h-0.5 w-full bg-[var(--rpg-gold-dark)] shrink-0"></div>

      <!-- Navigation -->
      <nav class="flex flex-col gap-1 flex-1">
        {% set nav_items = [
          ('/', 'layout-dashboard', '儀表板'),
          ('/cards', 'image', '我的卡牌'),
          ('/hall', 'swords', '角色大廳'),
          ('/progress', 'scroll-text', '學習歷程'),
          ('/admin', 'settings', '管理後台'),
        ] %}
        {% for href, icon, label in nav_items %}
        <a href="{{ href }}"
           onclick="closeSidebar()"
           class="flex items-center gap-2.5 w-full rounded px-3 py-2.5 text-xs font-bold font-tc transition-colors text-left
                  {% if request.url.path == href %}
                    bg-[var(--rpg-bg-card)] border border-[var(--rpg-gold-dark)] text-[var(--rpg-gold)]
                  {% else %}
                    border border-transparent text-[var(--rpg-text-secondary)] hover:text-[var(--rpg-gold)] hover:bg-[var(--rpg-bg-card)]/50
                  {% endif %}">
          <i data-lucide="{{ icon }}" class="w-[18px] h-[18px]"></i>
          <span>{{ label }}</span>
        </a>
        {% endfor %}
      </nav>

      <div class="h-0.5 w-full bg-[var(--rpg-gold-dark)] shrink-0"></div>

      <!-- User Info -->
      <div class="flex flex-col gap-2 px-1 py-2">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 rounded-full shrink-0 bg-[var(--rpg-gold-dark)] flex items-center justify-center">
            <span class="font-pixel text-[10px] text-[var(--rpg-gold)]">
              {{ current_user.email[0] | upper }}
            </span>
          </div>
          <span class="font-tc text-[11px] text-[var(--rpg-text-secondary)] truncate">
            {{ current_user.email }}
          </span>
        </div>
        <div class="flex items-center gap-1.5">
          <i data-lucide="coins" class="w-4 h-4 text-[var(--rpg-gold-bright)]"></i>
          <span class="font-tc text-[10px] font-bold text-[var(--rpg-gold-bright)]">
            {{ current_user.tokens }} 代幣
          </span>
        </div>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="flex flex-col flex-1 min-w-0 overflow-hidden gap-6
                 px-4 py-5 md:px-6 md:py-6 lg:px-8 lg:py-7">

      <!-- Hamburger (mobile) -->
      <button class="self-start p-1 lg:hidden text-[var(--rpg-gold)] shrink-0"
              onclick="openSidebar()">
        <i data-lucide="menu" class="w-6 h-6"></i>
      </button>

      {% block content %}{% endblock %}
    </main>
  </div>

  <script>
    // Initialize Lucide icons
    lucide.createIcons();

    // Sidebar toggle
    function openSidebar() {
      document.getElementById('sidebar').classList.remove('-translate-x-full');
      document.getElementById('sidebar-backdrop').classList.remove('hidden');
    }
    function closeSidebar() {
      document.getElementById('sidebar').classList.add('-translate-x-full');
      document.getElementById('sidebar-backdrop').classList.add('hidden');
    }
  </script>
</body>
</html>
```

### 2.2 Sidebar 行為

| 斷點 | 行為 |
|------|------|
| `lg` (1024px+) | Sidebar 固定可見（`lg:static lg:translate-x-0`），hamburger 隱藏 |
| `< 1024px` | Sidebar 預設隱藏（`-translate-x-full`），點擊 hamburger 滑入 + 半透明 backdrop |

### 2.3 Admin 路由權限

Sidebar 中的「管理後台」連結在 Jinja2 中應根據角色條件渲染：

```jinja2
{% if current_user.role in ('teacher', 'admin') %}
  <a href="/admin" ...>管理後台</a>
{% endif %}
```

---

## 3. 頁面規範

### 3.1 儀表板（Dashboard）

**路由**：`GET /`
**模板**：`templates/index.html`
**API**：伺服器端直接渲染（user data 從 DB 讀取）

#### 結構

```
header  →  標題「冒險者儀表板」+ 「歡迎回來，冒險者！」
  ├── flex-col gap-1 sm:flex-row sm:items-center sm:justify-between

top-row →  角色預覽 + 學習進度面板
  ├── flex-col gap-6 flex-1 min-h-0 overflow-y-auto lg:flex-row lg:overflow-y-hidden
  │
  ├── CharacterPreview（左）
  │     class: flex flex-col items-center w-full shrink-0 rounded p-4 gap-3
  │            bg-[var(--rpg-bg-panel)] border-2 border-[var(--rpg-gold-dark)]
  │            lg:w-80 lg:h-full
  │     內容: 標題「我的角色」、卡牌圖片、Lv/職業名、邊框等級指示
  │     圖片: w-40 h-56 md:w-[200px] md:h-[280px] rounded object-cover
  │            border-[3px] border-[var(--rpg-border-gold)]
  │
  └── ProgressPanel（右）
        class: flex flex-col flex-1 min-w-0 rounded px-4 py-4 gap-4
               bg-[var(--rpg-bg-panel)] border-2 border-[var(--rpg-gold-dark)]
               overflow-y-auto md:px-6 md:py-5
        內容: 5 個 UnitProgressRow（進度條 + 百分比）

quick-actions →  3 個快速操作按鈕
  ├── flex-col gap-3 w-full shrink-0 sm:flex-row sm:gap-4
  │
  ├── 普通按鈕: bg-[var(--rpg-bg-panel)] border-2 border-[var(--rpg-gold-dark)]
  │             text-[var(--rpg-text-primary)]
  │
  └── 高亮按鈕（生成卡牌）: bg-[var(--rpg-gold-dark)] border-2 border-[var(--rpg-gold)]
                             text-[var(--rpg-gold-bright)]
```

#### UnitProgressRow 元件

```html
<div class="flex flex-col w-full gap-1.5">
  <div class="flex items-center justify-between w-full">
    <span class="font-tc text-[10px] font-bold text-[var(--rpg-text-primary)]">
      {{ unit.name }}
    </span>
    {% if unit.status != 'locked' %}
      <span class="font-tc text-[8px] font-black
        {% if unit.status == 'success' %}text-[var(--rpg-success)]
        {% else %}text-[var(--rpg-warning)]{% endif %}">
        {{ unit.progress }}%
      </span>
    {% else %}
      <div class="flex items-center gap-1">
        <i data-lucide="lock" class="w-3 h-3 text-[var(--rpg-text-secondary)]"></i>
        <span class="font-tc text-[10px] font-bold text-[var(--rpg-text-secondary)]">未解鎖</span>
      </div>
    {% endif %}
  </div>
  <div class="w-full h-3 rounded-sm bg-[var(--rpg-bg-input)] overflow-hidden">
    {% if unit.status != 'locked' and unit.progress > 0 %}
      <div class="h-full rounded-sm
        {% if unit.status == 'success' %}bg-[var(--rpg-success)]{% else %}bg-[var(--rpg-warning)]{% endif %}"
           style="width: {{ unit.progress }}%">
      </div>
    {% endif %}
  </div>
  <span class="font-tc text-xs text-[var(--rpg-text-secondary)]">{{ unit.attr }}</span>
</div>
```

#### 資料欄位

```python
# 傳入 template 的 context
{
    "character": {
        "image_url": "/static/images/placeholder/card-latest.png",
        "level": 8,
        "class_name": "精靈大法師",
        "border_style": "gold",  # copper | silver | gold
    },
    "units": [
        {"name": "單元一：人工智慧簡介", "progress": 95, "status": "success", "attr": "已解鎖：種族與性別"},
        {"name": "單元二：多層感知器",   "progress": 80, "status": "success", "attr": "已解鎖：職業與體型"},
        {"name": "單元三：卷積神經網路", "progress": 60, "status": "warning", "attr": "已解鎖：服飾裝備"},
        {"name": "單元四：RNN/LSTM",     "progress": 0,  "status": "locked",  "attr": "武器"},
        {"name": "單元五：深度學習進階", "progress": 0,  "status": "locked",  "attr": "背景場景"},
    ],
    "current_user": {"email": "student@gmail.com", "tokens": 15}
}
```

---

### 3.2 卡牌收藏（Card Gallery）

**路由**：`GET /cards`
**模板**：`templates/cards/gallery.html`
**API**：`GET /api/cards` (JSON) 或伺服器端渲染

#### 結構

```
header  →  標題「我的卡牌收藏」+ 「已收集 N 張卡牌」
  ├── flex-col gap-1 sm:flex-row sm:items-center sm:justify-between

description →  說明文字
  ├── font-tc text-[13px] text-[var(--rpg-text-secondary)]

card-grid →  卡牌列表（橫向滾動/居中）
  ├── flex gap-4 flex-1 min-h-0 items-start
  │   overflow-x-auto scrollbar-hide snap-x snap-mandatory px-2 pb-4
  │   md:gap-6 md:justify-center md:overflow-x-visible md:px-0 md:pb-0 md:snap-none
```

#### CardItem 元件

```html
<div class="flex flex-col items-center gap-2 cursor-pointer group shrink-0 snap-center">
  <div class="w-40 h-60 md:w-[200px] md:h-[300px] rounded overflow-hidden transition-transform group-hover:scale-[1.02]"
       style="border: 3px solid {{ border_color }};
              border-radius: 4px;
              {% if card.is_latest %}box-shadow: 0 0 12px 2px {{ border_color }}66;{% endif %}">
    <img src="{{ card.image_url }}" alt="{{ card.name }}" class="w-full h-full object-cover">
  </div>

  <div class="flex items-center gap-1.5">
    <span class="font-tc text-[10px] font-black" style="color: {{ border_color }}">
      Lv.{{ card.level }}
    </span>
    {% if card.is_latest %}
      <i data-lucide="star" class="w-3.5 h-3.5" style="color: {{ border_color }}"></i>
      <span class="font-tc text-[9px] font-bold" style="color: {{ border_color }}">最新</span>
    {% endif %}
  </div>

  <span class="font-tc text-[11px] font-bold text-[var(--rpg-text-primary)] text-center">
    {{ card.name }}
  </span>

  <span class="font-tc text-[9px] font-bold text-center" style="color: {{ border_color }}">
    {{ border_label }}
  </span>
</div>
```

#### 邊框色對照

```python
BORDER_COLORS = {
    "copper": "var(--rpg-border-copper, #b87333)",
    "silver": "var(--rpg-border-silver, #c0c0c0)",
    "gold":   "var(--rpg-border-gold, #ffd700)",
}
BORDER_LABELS = {
    "copper": "銅色邊框",
    "silver": "銀色邊框",
    "gold":   "金色邊框",
}
```

#### 資料欄位

```python
{
    "cards": [
        {"image_url": "...", "level": 2, "name": "矮人遊俠",   "border_style": "copper", "is_latest": False},
        {"image_url": "...", "level": 5, "name": "聖騎士",     "border_style": "silver", "is_latest": False},
        {"image_url": "...", "level": 6, "name": "獸人刺客",   "border_style": "silver", "is_latest": False},
        {"image_url": "...", "level": 8, "name": "精靈大法師", "border_style": "gold",   "is_latest": True},
    ]
}
```

---

### 3.3 角色大廳（Hero Hall）

**路由**：`GET /hall`
**模板**：`templates/hall.html`
**API**：伺服器端渲染（讀取全班最新卡牌）

#### 結構

```
header  →  icon(swords) + 「角色大廳」+ 「全班冒險者一覽」
  ├── flex-col gap-1 sm:flex-row sm:items-center sm:justify-between

description →  說明文字

heroes-grid →  英雄卡牌（同 Card Gallery 橫向滾動模式）
  ├── flex gap-4 flex-1 min-h-0 w-full
  │   overflow-x-auto scrollbar-hide snap-x snap-mandatory px-2 pb-4
  │   md:gap-5 md:overflow-x-visible md:px-0 md:pb-0 md:snap-none
```

#### HeroCard 元件

```html
<div class="flex flex-col items-center gap-1.5 shrink-0 snap-center">
  <div class="w-36 h-52 md:w-[180px] md:h-[260px] rounded overflow-hidden"
       style="border: 3px solid {{ border_color }};
              border-radius: 4px;
              {% if hero.border_style == 'gold' %}box-shadow: 0 0 8px 0 {{ border_color }}44;{% endif %}">
    <img src="{{ hero.image_url }}" alt="{{ hero.name }}" class="w-full h-full object-cover">
  </div>

  <span class="font-tc text-[9px] font-black" style="color: {{ border_color }}">
    Lv.{{ hero.level }}
  </span>
  <span class="font-tc text-[10px] font-bold text-[var(--rpg-text-primary)] text-center">
    {{ hero.name }}
  </span>
  <span class="font-tc text-[11px] text-[var(--rpg-text-secondary)] text-center">
    {{ hero.class_name }}
  </span>
</div>
```

**卡牌尺寸差異**：Hero Hall 比 Card Gallery 略小（`w-36 h-52` vs `w-40 h-60`），glow 強度也較低（`8px 0` vs `12px 2px`）。

#### 資料欄位

```python
{
    "heroes": [
        {"image_url": "...", "level": 8, "name": "同學 A", "class_name": "精靈大法師", "border_style": "gold"},
        {"image_url": "...", "level": 7, "name": "同學 B", "class_name": "聖騎士",     "border_style": "silver"},
        {"image_url": "...", "level": 6, "name": "同學 C", "class_name": "獸人戰士",   "border_style": "silver"},
        {"image_url": "...", "level": 3, "name": "同學 D", "class_name": "學徒",       "border_style": "copper"},
        {"image_url": "...", "level": 2, "name": "同學 E", "class_name": "矮人遊俠",   "border_style": "copper"},
    ]
}
```

---

### 3.4 學習歷程（Learning Progress）

**路由**：`GET /progress`
**模板**：`templates/learning/progress.html`
**API**：`GET /progress`（伺服器端渲染）；各單元分數可用 HTMX partial 動態更新

#### 結構

```
header  →  icon(scroll-text) + 「學習任務紀錄」+ 「總進度：78%」
  ├── flex-col gap-1 sm:flex-row sm:items-center sm:justify-between

unit-cards →  垂直排列的單元卡片
  ├── flex flex-col gap-3 flex-1 min-h-0 overflow-y-auto w-full
  │
  ├── ActiveUnitCard（已完成/進行中）
  │     class: flex flex-col gap-2.5 rounded w-full px-3 py-3 bg-[var(--rpg-bg-panel)]
  │            sm:px-5 sm:py-4
  │     border: 2px solid <color>
  │       已完成 → var(--rpg-gold-dark)
  │       進行中 → var(--rpg-warning)
  │
  └── LockedUnitCard（未解鎖）
        class: flex flex-col gap-2 rounded w-full px-3 py-3
               bg-[var(--rpg-bg-panel)] border-2 border-[#333]
               sm:flex-row sm:items-center sm:justify-between sm:px-5 sm:py-3.5 sm:gap-0
```

#### StatusBadge 元件

```html
<!-- 已完成 -->
<div class="flex items-center gap-1.5 rounded-[3px] px-2.5 py-1 bg-[#4ade8033]">
  <i data-lucide="check-circle-2" class="w-3 h-3 text-[var(--rpg-success)]"></i>
  <span class="font-tc text-[9px] font-bold text-[var(--rpg-success)]">已完成</span>
</div>

<!-- 進行中 -->
<div class="flex items-center gap-1.5 rounded-[3px] px-2.5 py-1 bg-[#f59e0b33]">
  <i data-lucide="clock" class="w-3 h-3 text-[var(--rpg-warning)]"></i>
  <span class="font-tc text-[9px] font-bold text-[var(--rpg-warning)]">進行中</span>
</div>

<!-- 未解鎖 -->
<div class="flex items-center gap-1.5 rounded-[3px] px-2.5 py-1 border border-[#333]">
  <span class="font-tc text-[9px] font-bold text-[var(--rpg-text-secondary)]">未解鎖</span>
</div>
```

#### ScoreBar（ActiveUnitCard 內分數行）

```html
<div class="flex flex-col gap-0.5 w-full">
  <div class="flex items-center justify-between">
    <span class="font-tc text-[10px] text-[var(--rpg-text-secondary)]">{{ score.label }}</span>
    <span class="font-tc text-[10px] font-bold text-[var(--rpg-text-primary)]">
      {% if score.label == '額外練習' %}+{{ score.value }}{% else %}{{ score.value }}%{% endif %}
    </span>
  </div>
  {% if score.label != '額外練習' and score.value is not none %}
  <div class="w-full h-2 rounded-sm bg-[var(--rpg-bg-input)] overflow-hidden">
    <div class="h-full rounded-sm bg-[var(--rpg-success)]" style="width: {{ score.value }}%"></div>
  </div>
  {% endif %}
</div>
```

Scores grid 排版：`grid grid-cols-2 gap-3 w-full md:grid-cols-4 md:gap-4`

#### 資料欄位

```python
{
    "total_progress": 78,
    "units": [
        {
            "title": "單元一：人工智慧基礎",
            "status": "completed",  # completed | in-progress | locked
            "progress": 95,
            "scores": [
                {"label": "課程完成度", "value": 95},
                {"label": "隨堂測驗",   "value": 92},
                {"label": "課後練習",   "value": 85},
                {"label": "額外練習",   "value": 3},
            ],
            "unlock_text": "已解鎖：種族（精靈）+ 性別（女性）— 可選 5 項",
        },
        # ... 共 5 個單元
    ]
}
```

---

### 3.5 管理後台（Admin）

**路由**：`GET /admin`
**模板**：`templates/admin/dashboard.html`
**權限**：teacher / admin
**API**：`GET /api/admin/dashboard`、`POST /api/admin/import`

#### 結構

```
header  →  icon(settings) + 「管理後台」+ 兩個操作按鈕
  ├── flex-col gap-3 w-full sm:flex-row sm:items-center sm:justify-between
  │
  └── buttons: flex-col gap-2 w-full sm:flex-row sm:items-center sm:gap-3 sm:w-auto
       ├── 匯入成績 CSV（普通按鈕）
       └── 全班統計報表（高亮按鈕）

stats-grid →  4 個統計卡片
  ├── grid grid-cols-2 gap-3 w-full lg:grid-cols-4 lg:gap-4

section-title →  icon(user-check) + 「學生管理」+ 低進度警告
  ├── flex-col gap-1 w-full sm:flex-row sm:items-center sm:justify-between

student-table →  表格
  ├── 外層: flex flex-col flex-1 min-h-0 rounded bg-[var(--rpg-bg-panel)]
  │         border-2 border-[var(--rpg-gold-dark)] overflow-auto scrollbar-hide
  ├── 內層: min-w-[640px]（保證最小寬度）
  ├── 表頭: flex ... px-5 py-3 bg-[var(--rpg-bg-card)]
  │         border-b border-[var(--rpg-gold-dark)] sticky top-0 z-10
  └── 表行: flex ... px-5 py-2.5 border-b border-[var(--rpg-bg-input)]
            hover:bg-[var(--rpg-bg-card)]/50
            奇數行: bg-[var(--rpg-bg-card)]/20
```

#### StatCard 元件

```html
<div class="flex flex-col gap-2 flex-1 rounded px-5 py-4
            bg-[var(--rpg-bg-panel)] border-2 border-[var(--rpg-gold-dark)]">
  <div class="flex items-center gap-2">
    <i data-lucide="{{ icon }}" class="w-4 h-4 text-[var(--rpg-gold)]"></i>
    <span class="font-tc text-[10px] font-bold text-[var(--rpg-text-secondary)]">{{ label }}</span>
  </div>
  <div class="flex items-baseline gap-2">
    <span class="font-tc text-2xl font-black text-[var(--rpg-text-primary)]">{{ value }}</span>
    <span class="font-tc text-[10px] text-[var(--rpg-text-secondary)]">{{ sub }}</span>
  </div>
</div>
```

#### 表格欄位定義

| 欄位 | 寬度 class | 對齊 |
|------|-----------|------|
| 學號 | `w-16` | left |
| 姓名 | `w-20` | left |
| Email | `flex-1 truncate` | left |
| 等級 | `w-12 text-center` | center |
| 代幣 | `w-12 justify-center` | center |
| 學習進度 | `w-28`（含進度條） | — |
| 卡牌數 | `w-14 text-center` | center |

表頭文字統一：`font-tc text-[10px] font-bold text-[var(--rpg-gold)]`
表行文字：`font-tc text-[10px]`

#### ProgressBar（表格內用）

```html
<div class="flex items-center gap-2 w-28">
  <div class="flex-1 h-2 rounded-sm bg-[var(--rpg-bg-input)] overflow-hidden">
    <div class="h-full rounded-sm {{ color_class }}" style="width: {{ value }}%"></div>
  </div>
  <span class="font-tc text-[10px] font-bold text-[var(--rpg-text-primary)] w-8 text-right">
    {{ value }}%
  </span>
</div>
```

#### 資料欄位

```python
{
    "stats": [
        {"icon": "users",            "label": "註冊學生",   "value": "48",  "sub": "/ 50 名額"},
        {"icon": "bar-chart-3",      "label": "平均進度",   "value": "68%", "sub": "全班平均"},
        {"icon": "file-spreadsheet", "label": "已生成卡牌", "value": "142", "sub": "總計"},
        {"icon": "coins",            "label": "代幣流通",   "value": "720", "sub": "已發放"},
    ],
    "students": [
        {
            "student_id": "S112001", "name": "王小明", "email": "wang@gmail.com",
            "level": 8, "tokens": 15, "progress": 95, "cards_generated": 4,
        },
        # ... 更多學生
    ],
    "low_progress_count": 2,  # 進度 < 40% 的人數
}
```

---

## 4. 響應式設計規則

### 4.1 斷點對照表

| 元素 | Mobile (<768px) | Tablet (md: 768px+) | Desktop (lg: 1024px+) |
|------|-----------------|---------------------|----------------------|
| **Sidebar** | 隱藏，hamburger 開啟 drawer | 同 Mobile | 固定可見，hamburger 隱藏 |
| **Main padding** | `px-4 py-5` | `px-6 py-6` | `px-8 py-7` |
| **Header** | 標題/副標垂直排列 | 水平排列 (`sm:flex-row`) | 同 Tablet |
| **Dashboard top** | 角色/進度垂直堆疊 | 同 Mobile | 水平並排 (`lg:flex-row`) |
| **CharacterPreview** | `w-full` | 同 Mobile | `w-80 h-full` |
| **Card/Hero images** | `w-40 h-60` / `w-36 h-52` | `w-[200px] h-[300px]` / `w-[180px] h-[260px]` | 同 Tablet |
| **Card grid** | 橫向滾動 + snap | 居中展示，無滾動 | 同 Tablet |
| **Quick actions** | 垂直堆疊 | 水平排列 (`sm:flex-row`) | 同 Tablet |
| **Score grid** | `grid-cols-2` | `grid-cols-4` | 同 Tablet |
| **Unit card 內部** | 垂直排列 | 標題/badge 水平 (`sm:flex-row`) | 同 Tablet |
| **Locked unit** | 垂直排列 | 水平 (`sm:flex-row`) | 同 Tablet |
| **Admin stats** | `grid-cols-2 gap-3` | 同 Mobile | `grid-cols-4 gap-4` |
| **Admin buttons** | 垂直堆疊 `w-full` | 水平 (`sm:flex-row sm:w-auto`) | 同 Tablet |
| **Student table** | 橫向滾動 (`min-w-[640px]`) | 同 Mobile | 同 Mobile（仍可滾動） |

### 4.2 關鍵 Tailwind 模式

```
/* 水平滾動卡牌 gallery */
.card-scroll {
  @apply flex gap-4 overflow-x-auto scrollbar-hide snap-x snap-mandatory px-2 pb-4;
  @apply md:gap-6 md:justify-center md:overflow-x-visible md:px-0 md:pb-0 md:snap-none;
}

/* 卡牌項目 */
.card-item {
  @apply shrink-0 snap-center;
}
```

> **注意**：Tailwind CDN 不支持 `@apply`，上面僅作為 class 組合說明。在 Jinja2 中直接寫完整 class list。

---

## 5. Jinja2 + HTMX 轉換指南

### 5.1 React → Jinja2 對應表

| React 概念 | Jinja2 等價 |
|-----------|------------|
| `<Route path="/" element={<DashboardPage />} />` | FastAPI `@router.get("/")` → `templates.TemplateResponse("index.html", ctx)` |
| `<NavLink to="/cards" className={({isActive}) => ...}>` | `<a href="/cards" class="{% if request.url.path == '/cards' %}active{% endif %}">` |
| `<Outlet />` | `{% block content %}{% endblock %}` |
| `{items.map(item => <Comp key={item.id} />)}` | `{% for item in items %}<div>...</div>{% endfor %}` |
| `{condition && <element>}` | `{% if condition %}<element>{% endif %}` |
| `{a ? b : c}` | `{% if a %}b{% else %}c{% endif %}` |
| `useState` (sidebar toggle) | Vanilla JS (`openSidebar()` / `closeSidebar()`) |
| `className={[...].join(' ')}` | Jinja2 `class="{% if ... %}cls-a{% else %}cls-b{% endif %}"` |
| `style={{ width: \`${value}%\` }}` | `style="width: {{ value }}%"` |

### 5.2 HTMX 互動場景

| 場景 | React 做法 | HTMX 做法 |
|------|-----------|-----------|
| 生成卡牌 | POST + 輪詢狀態 | `hx-post="/api/cards/generate" hx-target="#gen-status" hx-swap="innerHTML"` |
| 匯入 CSV | form submit | `hx-post="/api/admin/import" hx-encoding="multipart/form-data" hx-target="#import-result"` |
| 代幣花費 | POST + state update | `hx-post="/api/tokens/spend" hx-target="#token-display" hx-swap="outerHTML"` |
| 進度即時更新 | — | `hx-get="/api/progress/unit/3" hx-trigger="every 30s" hx-target="#unit-3-scores"` |

### 5.3 模板繼承結構

```
base.html              ← 全站佈局（sidebar + main shell）
├── index.html         ← 儀表板
├── cards/
│   ├── gallery.html   ← 卡牌收藏
│   └── detail.html    ← 單張卡牌詳情
├── hall.html          ← 角色大廳
├── learning/
│   ├── progress.html  ← 學習歷程總覽
│   └── unit_detail.html
├── admin/
│   ├── dashboard.html ← 管理後台
│   ├── students.html
│   └── import.html
└── errors/
    ├── register.html
    └── 404.html
```

### 5.4 靜態資源路徑

```
app/static/
├── css/style.css           ← CSS 變數 + 自訂工具類（.font-pixel, .scrollbar-hide 等）
├── js/app.js               ← HTMX 配置 + sidebar toggle + lucide init
├── fonts/                  ← 若需本地字型
└── images/
    ├── ui/                 ← UI 裝飾素材
    └── placeholder/        ← 開發用示範圖片（見第 6 節）
```

---

## 6. 示範圖片資產

### 6.1 圖片對照表

| 檔名 | 用於頁面 | 用途 | 建議放置名 |
|------|---------|------|-----------|
| `generated-1771604794683.png` | Dashboard | 角色預覽（最新卡牌） | `card-dashboard.png` |
| `generated-1771604882984.png` | Card Gallery | 卡牌 #1（銅邊, Lv.2） | `card-copper-1.png` |
| `generated-1771604891324.png` | Card Gallery | 卡牌 #2（銀邊, Lv.5） | `card-silver-1.png` |
| `generated-1771604906919.png` | Card Gallery | 卡牌 #3（銀邊, Lv.6） | `card-silver-2.png` |
| `generated-1771604915197.png` | Card Gallery | 卡牌 #4（金邊, Lv.8, 最新） | `card-gold-1.png` |
| `generated-1771604977541.png` | Hero Hall | 英雄 A（金邊, Lv.8） | `hero-gold-1.png` |
| `generated-1771604991086.png` | Hero Hall | 英雄 B（銀邊, Lv.7） | `hero-silver-1.png` |
| `generated-1771605000875.png` | Hero Hall | 英雄 C（銀邊, Lv.6） | `hero-silver-2.png` |
| `generated-1771605014507.png` | Hero Hall | 英雄 D（銅邊, Lv.3） | `hero-copper-1.png` |
| `generated-1771605023576.png` | Hero Hall | 英雄 E（銅邊, Lv.2） | `hero-copper-2.png` |

### 6.2 放置路徑

```
app/static/images/placeholder/
├── card-dashboard.png
├── card-copper-1.png
├── card-silver-1.png
├── card-silver-2.png
├── card-gold-1.png
├── hero-gold-1.png
├── hero-silver-1.png
├── hero-silver-2.png
├── hero-copper-1.png
└── hero-copper-2.png
```

在 Jinja2 模板中引用：
```html
<img src="/static/images/placeholder/card-gold-1.png" alt="精靈大法師">
```

---

## 7. API Endpoint 對照（from system-spec.md）

### 7.1 頁面路由

| 端點 | 對應模板 | 權限 |
|------|---------|------|
| `GET /` | `index.html` | 已認證 |
| `GET /cards` | `cards/gallery.html` | student |
| `GET /cards/{card_id}` | `cards/detail.html` | student (own) |
| `GET /hall` | `hall.html` | 已認證 |
| `GET /progress` | `learning/progress.html` | student |
| `GET /progress/{unit_code}` | `learning/unit_detail.html` | student |
| `GET /admin` | `admin/dashboard.html` | teacher/admin |
| `GET /admin/students` | `admin/students.html` | teacher/admin |
| `GET /admin/import` | `admin/import.html` | teacher/admin |

### 7.2 API 路由（JSON / HTMX partial）

| 端點 | 方法 | 說明 |
|------|------|------|
| `POST /api/cards/generate` | POST | 提交卡牌生成請求 |
| `GET /api/cards/{card_id}/status` | GET | 查詢卡牌生成狀態 |
| `PUT /api/config/{unit_code}` | PUT | 更新角色屬性配置 |
| `GET /api/config/{unit_code}/options` | GET | 取得可選屬性選項 |
| `POST /api/admin/import` | POST | 上傳匯入成績 CSV |
| `PUT /api/admin/students/{id}/config` | PUT | 教師調整學生配置 |
| `GET /api/admin/dashboard` | GET | 全班統計資料 |
| `POST /api/tokens/spend` | POST | 花費代幣 |
| `GET /api/tokens/history` | GET | 代幣交易紀錄 |

---

## 8. Tailwind CDN 注意事項

由於使用 Tailwind CDN（非建構工具），需注意：

1. **無法使用 `@apply`**：所有 class 必須內聯在 HTML 元素上
2. **無法自訂 `tailwind.config.js`**：但可透過 CDN script 配置：
   ```html
   <script>
     tailwind.config = {
       theme: {
         extend: {
           fontFamily: {
             pixel: ['"Press Start 2P"', 'monospace'],
             tc: ['"Noto Sans TC"', 'sans-serif'],
           },
         },
       },
     }
   </script>
   ```
3. **任意值語法可用**：`bg-[var(--rpg-bg-dark)]`、`text-[10px]` 等在 CDN 版本中完全支持
4. **CSS 變數需定義在 `style.css`**：不能放在 Tailwind 的 `@layer` 中（CDN 版不支持），直接寫 `:root {}` 即可

---

## 9. CLAUDE.md 建議段落

以下段落可直接加入 vm-web-server 專案的 CLAUDE.md：

```markdown
## UI 設計規範

本專案的前端 UI 設計規範來自 `docs/ui-design-spec.md`，這是從已驗證的 React SPA 原型自動提取的完整設計交接文件。

### 必讀文件

1. **`docs/ui-design-spec.md`**（主要參考）— 包含：
   - 完整 CSS 變數表與色彩用途
   - 5 個頁面的 HTML 結構與 Tailwind class（可直接複製到 Jinja2）
   - 響應式斷點行為對照表
   - Jinja2 + HTMX 轉換指南
   - 示範資料結構
2. **`ref/react-frontend/`**（輔助參考）— React 原始碼，用於理解細節互動邏輯
3. **`system-spec.md`**（系統規格書）— 資料模型、API 端點、評分規則

### 開發注意事項

- UI 文字使用 **繁體中文 (zh-TW)**
- 所有色彩透過 CSS 變數引用，不硬寫 hex 值
- 像素風格文字用 `.font-pixel`（Press Start 2P），中文內文用 `.font-tc`（Noto Sans TC）
- Icon 使用 **Lucide Icons**（CDN 版，`data-lucide` 屬性）
- 響應式斷點：Mobile < 768px、Tablet md: 768px+、Desktop lg: 1024px+
- 卡牌 gallery 在手機上使用橫向滾動 + CSS snap
- 由文生圖模型所生成的卡牌圖片，預設是直式長方形，寬度是880px，高度是1280px（880x1280），請注意頁面排版設計要符合與適應這個預設卡牌的尺寸大小。
```
