# Scholaverse - 學習歷程卡牌瀏覽平台 (intro-ai)

## 專案概述

教育部教學實踐研究計畫「從學習評分到角色養成：生成式 AI 支持下的遊戲化教學實踐」的配套教學平台。為輔仁大學「人工智慧概論」課程開發，將學生在 5 大學習單元的表現數據，透過評分轉換規則對應為 RPG 角色屬性（種族、職業、裝備、武器、背景），再由 vm-ai-worker 上的 LLM + 文生圖模型產生個性化角色頭像卡牌。

- 網域：app.scholaverse.cc
- 部署位置：vm-web-server (192.168.50.111)
- 完整規格書：`docs/system-spec.md`

## 架構

```
使用者 → Cloudflare Zero Trust → vm-web-server (本專案, FastAPI)
                                    ├→ vm-ai-worker (192.168.50.110) - GPU/LLM/文生圖
                                    └→ vm-db-storage (192.168.50.112) - 圖片+Metadata
```

- vm-ai-worker 和 vm-db-storage 目前尚未建立，使用 mock/stub 開發
- vm-web-server 不負責生成 prompt，而是將學習數據+角色配置送給 vm-ai-worker，由其 LLM 產生 prompt 並生圖
- 所有 VM IP 為暫定，透過環境變數配置，不寫死在程式碼中

## 技術棧

- Python 3.12+, FastAPI, uvicorn
- 套件管理：**uv**（不使用 pip）
- 模板：Jinja2 + HTMX + Tailwind CSS (CDN)
- 資料庫：SQLite + SQLAlchemy (async) + aiosqlite
- HTTP 客戶端：httpx（與其他 VM 通信）
- 測試：pytest + pytest-asyncio
- 版本控制：Git + GitHub (repo: chihuah/scholaverse-intro-ai)

## 重要規則

- 所有套件安裝一律使用 `uv add`，禁止使用 pip
- 認證由 Cloudflare Zero Trust 處理，從 header `cf-access-authenticated-user-email` 取得使用者 email
- 未註冊的使用者導向自助註冊頁面（填學號+姓名綁定）
- 前端採用 Pixel Art 像素風格 RPG 介面（參考 ref/PixelArt_UI_example.jpeg）
- 配色：深色背景 #1a1a2e、深綠面板 #2d3a1a、金色強調 #d4a847
- 字型：像素字型 (Press Start 2P) 用於標題，Noto Sans TC 用於中文內文
- main.py 中現有的 HTML 內容是舊的佔位頁面，應被完全替換，不要參考其內容

## 專案結構

```
intro-ai/
├── main.py              # FastAPI app 入口（待重構）
├── pyproject.toml       # uv 套件管理
├── app/
│   ├── config.py        # 設定管理（讀取 .env）
│   ├── database.py      # SQLAlchemy engine & session
│   ├── models/          # SQLAlchemy ORM models
│   ├── schemas/         # Pydantic schemas
│   ├── routers/         # FastAPI 路由
│   ├── services/        # 業務邏輯（auth, storage, ai_worker, scoring）
│   ├── templates/       # Jinja2 模板
│   └── static/          # CSS, JS, fonts, images
├── tests/               # pytest 測試
├── scripts/             # 工具腳本（seed data 等）
├── docs/                # 規格書
└── ref/                 # 參考文件（計畫書、UI 參考圖）
```

## 可用的 Skills

- `/ui-review [file]` — 審查前端 UI 是否符合像素 RPG 設計規範
- `/pixel-component <名稱>` — 生成符合設計規範的 HTML/CSS 元件
- `/responsive-check [file]` — 檢查響應式設計
- `/api-scaffold <名稱>` — 建立新的 FastAPI router 模組
- `/run-tests [path]` — 執行 pytest 測試

## 常用指令

```bash
uv sync                                          # 安裝依賴
uv add <package>                                 # 新增套件
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload  # 啟動開發伺服器
uv run pytest tests/ -v                          # 執行測試
```

## 資料模型

6 張核心資料表：students, units, learning_records, card_configs, cards, token_transactions
詳見 `docs/system-spec.md` 第 3 節

## 評分轉換

5 大學習單元各對應一項角色屬性：
- 單元 1 (先備知識) → 種族、性別
- 單元 2 (MLP) → 職業、體型
- 單元 3 (CNN) → 服飾裝備
- 單元 4 (RNN) → 武器
- 單元 5 (進階技術) → 背景場景
- 自主學習 → 表情、姿勢、外框

分數越高，可選選項越多、品質越高。詳見 `docs/system-spec.md` 第 4 節

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
