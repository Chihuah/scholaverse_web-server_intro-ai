# Scholaverse — 學習歷程卡牌瀏覽平台

> 教育部教學實踐研究計畫配套教學平台
> 課程：輔仁大學「人工智慧概論」

## 專案簡介

本平台將學生在 5 大學習單元的表現數據，透過評分轉換規則對應為 RPG 角色屬性（種族、職業、裝備、武器、背景），再由 AI Worker 上的 LLM 與文生圖模型產生個性化角色頭像卡牌。

## 系統架構

```
使用者 → Cloudflare Zero Trust → vm-web-server（本專案）
                                    ├→ vm-ai-worker (GPU / LLM / 文生圖)
                                    └→ vm-db-storage (圖片 + Metadata)
```

- **vm-web-server**：FastAPI 應用，負責學生認證、學習數據管理、卡牌瀏覽
- **vm-ai-worker**：接收學習數據與角色配置，由 LLM 生成 prompt 並呼叫文生圖模型
- **vm-db-storage**：儲存生成的卡牌圖片與 Metadata

## 技術棧

| 類別 | 技術 |
|------|------|
| 後端框架 | Python 3.12+, FastAPI, uvicorn |
| 套件管理 | uv |
| 資料庫 | SQLite + SQLAlchemy (async) + aiosqlite |
| 模板引擎 | Jinja2 + HTMX |
| 前端樣式 | Tailwind CSS (CDN)、Pixel Art 像素風格 |
| HTTP 客戶端 | httpx |
| 認證 | Cloudflare Zero Trust（header: `cf-access-authenticated-user-email`） |
| 測試 | pytest + pytest-asyncio |

## 專案結構

```
intro-ai/
├── main.py              # FastAPI app 入口
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
└── docs/                # 規格書
```

## 快速開始

### 環境需求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### 安裝

```bash
# 安裝依賴
uv sync
```

### 設定環境變數

複製範本並填入設定：

```bash
cp .env.example .env
```

### 初始化資料庫

```bash
# 建立學習單元基本資料
uv run python scripts/seed_data.py

# 建立屬性規則
uv run python scripts/seed_attribute_rules.py

# （選用）建立訪客模式示範資料
uv run python scripts/seed_demo_data.py
```

### 啟動開發伺服器

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 執行測試

```bash
uv run pytest tests/ -v
```

## 訪客模式

設定 `GUEST_MODE=true` 後，未認證（或已認證但未註冊）的使用者可以瀏覽所有示範頁面，無需學號綁定。

| 操作 | 訪客 | 已註冊學生 |
|------|------|-----------|
| 瀏覽儀表板 / 大廳 / 卡牌 / 學習歷程 | ✅ 顯示示範資料 | ✅ |
| 選擇角色屬性、生成卡牌 | ❌ 已隱藏 / 401 | ✅ |
| 管理後台 | ❌ 需 teacher / admin | ✅（需角色） |

啟用步驟：

```bash
# 1. 在 .env 設定
GUEST_MODE=true

# 2. 匯入示範學生資料（首次需執行）
uv run python scripts/seed_demo_data.py

# 3. 重啟伺服器
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

> **正式上線前**請將 `GUEST_MODE` 改回 `false` 並重啟伺服器。

## 學習單元與角色屬性對應

| 學習單元 | 對應角色屬性 |
|----------|-------------|
| 單元 1：先備知識 | 種族、性別 |
| 單元 2：MLP | 職業、體型 |
| 單元 3：CNN | 服飾裝備 |
| 單元 4：RNN | 武器 |
| 單元 5：進階技術 | 背景場景 |
| 自主學習 | 表情、姿勢、外框 |

分數越高，可選選項越多、品質越高。

## UI 設計規範

- 像素風格 RPG 介面（Pixel Art）
- 配色：深色背景 `#1a1a2e`、深綠面板 `#2d3a1a`、金色強調 `#d4a847`
- 標題字型：Press Start 2P（像素字型）
- 中文內文：Noto Sans TC

詳細規範見 [`docs/ui-design-spec.md`](docs/ui-design-spec.md)。

## 文件

- [`docs/system-spec.md`](docs/system-spec.md) — 完整系統規格書
- [`docs/ui-design-spec.md`](docs/ui-design-spec.md) — UI 設計規範
- [`docs/vm-ai-worker-spec.md`](docs/vm-ai-worker-spec.md) — AI Worker API 規格
