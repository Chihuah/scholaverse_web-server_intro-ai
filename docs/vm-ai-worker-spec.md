# vm-ai-worker — AI 圖片生成服務：開發規格書

> 主機：vm-ai-worker (192.168.50.110)
> 版本：v0.1.0 (初始開發)
> 撰寫日期：2026-02-22
> 對應專案：Scholaverse — 學習歷程卡牌瀏覽平台

---

## 1. 系統總覽

### 1.1 系統定位

vm-ai-worker 是 Scholaverse 平台的 **AI 圖片生成服務**。負責接收 vm-web-server 送來的角色配置 (card_config) 與學習數據 (learning_data)，經過兩階段處理後產生角色卡牌圖片：

1. **LLM Prompt 生成**：使用 Ollama 上的語言模型，將結構化的 RPG 屬性配置翻譯為精細的文生圖 prompt
2. **文生圖生成**：使用 sd-cli + FLUX 模型，根據 prompt 產生角色卡牌圖片

### 1.2 架構位置

```
                        ┌─────────────────┐
                        │  vm-web-server  │ 192.168.50.111
                        │  FastAPI + UI   │
                        └──┬──────────┬───┘
              POST         │          │         POST callback
           /api/generate   │          │    /api/internal/generation-callback
                ┌──────────▼──┐       │
                │vm-ai-worker │       │
                │  本專案      │───────┘
                │192.168.50.110│
                │GPU: RTX 5080│
                └──────┬──────┘
                       │ POST /api/images/upload
              ┌────────▼─────────┐
              │ vm-db-storage    │
              │ 192.168.50.112   │
              │ 圖片持久化儲存    │
              └──────────────────┘
```

### 1.3 硬體規格

| 項目 | 規格 |
|------|------|
| GPU | NVIDIA RTX 5080 |
| 用途 | 文生圖推理 (sd-cli)、LLM 推理 (Ollama) |
| OS | Ubuntu (含 CUDA 驅動) |
| 環境管理 | Conda |

---

## 2. 技術棧

| 層面 | 技術 | 說明 |
|------|------|------|
| 語言 | Python 3.12+ | Conda 環境 |
| 環境管理 | Conda | GPU/CUDA 相依性較適合 conda |
| 套件管理 | pip (conda 環境內) | 安裝 FastAPI 等 Python 套件 |
| API 框架 | FastAPI + uvicorn | 與 vm-web-server 技術棧一致 |
| LLM 推理 | Ollama | 本機部署，用於 prompt 生成 |
| 文生圖 | sd-cli (本地編譯) | FLUX 模型 + LoRA |
| HTTP 客戶端 | httpx | 回調 vm-web-server、上傳圖片到 vm-db-storage |
| 任務佇列 | asyncio.Queue | GPU 序列處理，一次一張圖 |
| 測試 | pytest + pytest-asyncio | |

---

## 3. API 端點定義

### 3.1 對外 API（供 vm-web-server 呼叫）

#### `POST /api/generate` — 提交卡牌生成請求

vm-web-server 呼叫此端點送出生圖任務。服務端收到後立即回應（非同步處理）。

**Request Body：**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "card_id": 1,
  "student_number": "411234567",
  "card_config": {
    "race": "elf",
    "gender": "female",
    "class": "mage",
    "body": "slim",
    "equipment": "legendary",
    "weapon_quality": "artifact",
    "weapon_type": "staff",
    "background": "magic_tower",
    "expression": "confident",
    "pose": "battle_ready",
    "border": "gold",
    "level": 8
  },
  "learning_data": {
    "unit_scores": {
      "unit_1": {"quiz": 92, "homework": 85, "completion": 95},
      "unit_2": {"quiz": 88, "homework": 78, "completion": 90},
      "unit_3": {"quiz": 76, "homework": 80, "completion": 85},
      "unit_4": {"quiz": 82, "homework": 70, "completion": 80},
      "unit_5": {"quiz": 90, "homework": 88, "completion": 92}
    },
    "overall_completion": 88.4
  },
  "style_hint": "16-bit pixel art, fantasy RPG character card",
  "callback_url": "http://192.168.50.111/api/internal/generation-callback"
}
```

**Request Body 欄位說明：**

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| job_id | string (UUID) | ✓ | vm-web-server 產生的唯一任務 ID |
| card_id | integer | ✓ | 對應 vm-web-server 資料庫中的 Card.id |
| student_number | string | ✓ | 學生學號（純數字），同時作為 sd-cli 的固定 seed |
| card_config | object | ✓ | 角色屬性配置（詳見第 5 節映射表） |
| learning_data | object | ✓ | 學生學習數據，可供 LLM 作為 prompt 風格參考 |
| style_hint | string | ✗ | 風格提示，預設 "16-bit pixel art, fantasy RPG character card" |
| callback_url | string | ✓ | 任務完成後的回調 URL |

**card_config 欄位說明：**

| 欄位 | 型別 | 說明 | 可能的值（參見第 5 節） |
|------|------|------|------------------------|
| race | string | 種族 | elf, human, orc, dwarf, dragon, pixie, plant, slime |
| gender | string | 性別 | male, female, neutral |
| class | string | 職業 | archmage, paladin, ranger, assassin, priest, mage, warrior, archer, militia, apprentice, farmer |
| body | string | 體型 | muscular, standard, slim |
| equipment | string | 裝備品質 | legendary, fine, common, crude, broken |
| weapon_quality | string | 武器品質 | artifact, fine, common, crude, primitive |
| weapon_type | string | 武器類型 | sword, shield, staff, spellbook, bow, dagger, mace, spear, short_sword, club, wooden_stick, stone |
| background | string | 背景場景 | palace_throne, dragon_lair, sky_city, castle, magic_tower, town, market, village, wilderness, ruins |
| expression | string | 表情 | regal, passionate, confident, calm, weary |
| pose | string | 姿勢 | charging, battle_ready, standing, crouching |
| border | string | 卡牌邊框 | copper, silver, gold |
| level | integer | 卡牌等級 | 1-10 |

**Response (成功，HTTP 202 Accepted)：**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "position": 0,
  "message": "Job accepted and queued for processing."
}
```

**Response 欄位說明：**

| 欄位 | 說明 |
|------|------|
| job_id | 任務 ID（原樣回傳） |
| status | "queued"（已排入佇列）或 "processing"（正在處理） |
| position | 佇列中的位置（0 表示下一個處理） |
| message | 人可讀的訊息 |

**Error Response (HTTP 422)：**

```json
{
  "detail": [
    {
      "loc": ["body", "card_config", "race"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

#### `GET /api/jobs/{job_id}` — 查詢任務狀態

**Path Parameter：**
- `job_id` (string, UUID)：任務 ID

**Response：**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "card_id": 1,
  "status": "completed",
  "image_path": "/students/1/cards/card_001.png",
  "thumbnail_path": "/students/1/cards/card_001_thumb.png",
  "prompt": "Digital painting, epic fantasy art... (generated prompt)",
  "generated_at": "2026-02-22T12:00:00Z",
  "error": null
}
```

**status 值：**

| 狀態 | 說明 |
|------|------|
| queued | 已排入佇列，等待處理 |
| processing | 正在生成中（LLM prompt 或 sd-cli 生圖） |
| uploading | 圖片生成完成，正在上傳到 vm-db-storage |
| completed | 全部完成，callback 已送出 |
| failed | 失敗 |

---

#### `GET /api/health` — 健康檢查

**Response：**

```json
{
  "status": "ok",
  "gpu_available": true,
  "ollama_available": true,
  "sd_cli_available": true,
  "queue_size": 2,
  "current_job": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### 3.2 回調 API（vm-ai-worker → vm-web-server）

任務完成或失敗後，vm-ai-worker 主動 POST 回調 `callback_url`。

**`POST {callback_url}`**

成功時：
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "card_id": 1,
  "status": "completed",
  "image_path": "/students/1/cards/card_001.png",
  "thumbnail_path": "/students/1/cards/card_001_thumb.png",
  "generated_at": "2026-02-22T12:00:00Z"
}
```

失敗時：
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "card_id": 1,
  "status": "failed",
  "error": "sd-cli process exited with code 1: CUDA out of memory"
}
```

> **重要**：此 callback body 格式必須與 vm-web-server 的 `GenerationCallbackBody` schema 完全一致（定義於 `app/routers/internal.py`）。

---

### 3.3 與 vm-db-storage 的通信 API

#### vm-ai-worker → vm-db-storage：圖片上傳

**`POST http://192.168.50.112/api/images/upload`**

```
Content-Type: multipart/form-data

Fields:
  - file: (binary) PNG 圖片檔案
  - student_id: (integer) 學生 ID
  - card_id: (integer) 卡牌 ID
  - image_type: (string) "full" 或 "thumbnail"
  - metadata: (JSON string) 生成相關 metadata
```

**Response (HTTP 201)：**

```json
{
  "image_path": "/students/1/cards/card_001.png",
  "file_size": 524288,
  "stored_at": "2026-02-22T12:00:00Z"
}
```

#### vm-web-server → vm-db-storage：圖片讀取（已定義於 intro-ai 規格書）

| 端點 | 說明 |
|------|------|
| `GET /api/images/{image_path}` | 讀取圖片檔案 |
| `GET /api/images/list?student_id=1` | 列出某學生所有圖片 |
| `GET /api/metadata/{card_id}` | 讀取圖片 metadata |

---

## 4. 圖片生成管線

### 4.1 完整流程

```
vm-web-server                vm-ai-worker                    vm-db-storage
     │                            │                               │
     │  POST /api/generate        │                               │
     │  (card_config + learning)  │                               │
     ├───────────────────────────>│                               │
     │                            │                               │
     │  202 Accepted {queued}     │                               │
     │<───────────────────────────┤                               │
     │                            │                               │
     │                      ┌─────┴─────┐                         │
     │                      │ Job Queue │                         │
     │                      └─────┬─────┘                         │
     │                            │                               │
     │                    Step 1: LLM Prompt 生成                  │
     │                    (Ollama API)                             │
     │                            │                               │
     │                    Step 2: sd-cli 文生圖                    │
     │                    (subprocess, ~30-60s)                    │
     │                            │                               │
     │                    Step 3: 產生縮圖                         │
     │                    (Pillow resize)                          │
     │                            │                               │
     │                            │  POST /api/images/upload      │
     │                    Step 4: │──────────────────────────────>│
     │                     上傳   │  (full image + thumbnail)     │
     │                            │<──────────────────────────────┤
     │                            │  {image_path}                 │
     │                            │                               │
     │  POST callback_url         │                               │
     │  {completed, image_path}   │                               │
     │<───────────────────────────┤ Step 5: 回調                   │
     │                            │                               │
```

### 4.2 各步驟說明

#### Step 1：LLM Prompt 生成（Ollama）

呼叫本機 Ollama API，將 card_config 翻譯為英文文生圖 prompt。

```python
# Ollama API 呼叫
POST http://localhost:11434/api/generate
{
  "model": "llama3.1:8b",    # 或其他已部署的模型
  "prompt": system_prompt + user_message,
  "stream": false
}
```

**System Prompt 範例：**

```
You are a professional text-to-image prompt engineer specializing in fantasy RPG character card art.

Given a structured RPG character configuration, generate a detailed, vivid English prompt for an AI image generator.

Output ONLY the prompt text, no explanations.

Rules:
- The character should be the central focus, shown from waist up or full body
- Include specific details about race features, clothing, weapon, pose, expression, and background
- The overall mood should match the character's level and equipment quality
- Do NOT include any LoRA triggers, style prefixes, or technical tags — only output the character/scene description
- Output a single paragraph prompt, no line breaks
```

**User Message 組裝範例：**

```
Character Configuration:
- Race: Elf (精靈)
- Gender: Female
- Class: Mage (法師)
- Body Type: Slim (纖細)
- Equipment Quality: Legendary (傳說級 - 華麗精緻)
- Weapon: Staff (法杖), Quality: Artifact (神器級)
- Background: Magic Tower (魔法塔)
- Expression: Confident (自信)
- Pose: Battle Ready (持武器備戰)
- Card Border: Gold
- Character Level: 8/10

Learning Performance Context:
- Overall Completion: 88.4%
- Strongest Unit: Unit 1 (Quiz: 92)
- This is a high-achieving student. The character should look powerful and accomplished.
```

#### Step 2：sd-cli 文生圖

使用 subprocess 呼叫已編譯的 sd-cli。由 `sd_runner.py` 負責自動組裝以下三項，**不依賴 Ollama 輸出**：

1. **LoRA 觸發詞**：`<lora:moode_fantasy_Impressions:0.5>`
2. **風格前綴**：`Digital painting, epic fantasy art, painterly texture, ...`
3. **Seed**：`int(student_number)`（學號轉整數）

```python
# sd_runner.py 內部邏輯
PROMPT_PREFIX = "<lora:moode_fantasy_Impressions:0.5> Digital painting, epic fantasy art, painterly texture, majestic and awe-inspiring atmosphere, high detail."

# ollama_prompt = Ollama 產出的純角色描述（不含 LoRA / 前綴）
# student_number = 來自 request body 的學號
final_prompt = f"{PROMPT_PREFIX} {ollama_prompt}"
seed = int(student_number)

cmd = [
    "./build/bin/sd-cli",
    "--diffusion-model", "models/z-image-turbo-Q8_0.gguf",
    "--vae", "models/FLUX_ae.safetensors",
    "--llm", "models/Qwen3-4b-Z-Image-Engineer-V4-Q8_0.gguf",
    "--cfg-scale", "1.0",
    "--steps", "10",
    "--diffusion-fa",
    "-H", "1280",
    "-W", "880",
    "-o", output_path,
    "-s", str(seed),
    "--lora-model-dir", "models/lora",
    "-p", final_prompt
]
```

**生圖參數（預設 preset）：**

| 參數 | 值 | 說明 |
|------|-----|------|
| 解析度 | 880×1280 | 直式卡牌比例 |
| steps | 10 | 擴散步數 |
| cfg_scale | 1.0 | FLUX 模型推薦值 |
| seed | int(student_number) | 固定使用學生學號作為 seed，確保同一學生重複生成時基底一致 |
| LoRA | moode_fantasy_Impressions:0.5 | 奇幻繪畫風格（由 sd_runner.py 自動加入 prompt 前綴） |

> **注意**：sd-cli 內建的 `--llm` 參數（Qwen3-4b）會對 prompt 再做一層潤飾。因此 Ollama 產生的 prompt 已經是精細的描述，sd-cli 的內建 LLM 會在此基礎上進一步最佳化。

#### Step 3：縮圖生成

```python
from PIL import Image

img = Image.open(output_path)
thumb = img.resize((220, 320), Image.LANCZOS)
thumb.save(thumbnail_path, "PNG")
```

#### Step 4：上傳至 vm-db-storage

將完整圖片和縮圖上傳至 vm-db-storage。

#### Step 5：回調 vm-web-server

```python
async with httpx.AsyncClient(timeout=15.0) as client:
    await client.post(callback_url, json={
        "job_id": job_id,
        "card_id": card_id,
        "status": "completed",
        "image_path": image_path,
        "thumbnail_path": thumbnail_path,
        "generated_at": datetime.now(timezone.utc).isoformat()
    })
```

---

## 5. Prompt 建構：RPG 屬性映射表

### 5.1 種族 (race)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| elf | 精靈 | an Elf with pointed ears and ethereal features |
| human | 人類 | a Human with balanced features |
| orc | 獸人 | an Orc with tusks and muscular green-tinted skin |
| dwarf | 矮人 | a Dwarf with a stout build and thick beard |
| dragon | 龍族 | a Dragonborn with scaled skin and draconic features |
| pixie | 小精靈 | a tiny Pixie with delicate wings and glowing aura |
| plant | 植物 | a Plant creature with bark-like skin and leaf hair |
| slime | 史萊姆 | a Slime humanoid with translucent gelatinous body |

### 5.2 性別 (gender)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| male | 男性 | male |
| female | 女性 | female |
| neutral | 中性 | androgynous |

### 5.3 職業 (class)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| archmage | 大法師 | an Archmage radiating powerful arcane energy |
| paladin | 聖騎士 | a Paladin in shining holy armor |
| ranger | 遊俠 | a Ranger in woodland attire |
| assassin | 刺客 | an Assassin cloaked in shadows |
| priest | 牧師 | a Priest with divine aura |
| mage | 法師 | a Mage in enchanted robes |
| warrior | 戰士 | a Warrior in battle armor |
| archer | 弓箭手 | an Archer in light leather armor |
| militia | 民兵 | a Militia member in simple padded armor |
| apprentice | 學徒 | an Apprentice in plain robes |
| farmer | 農夫 | a Farmer in humble peasant clothes |

### 5.4 體型 (body)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| muscular | 結實精壯 | muscular and well-built physique |
| standard | 標準 | average athletic build |
| slim | 纖細瘦弱 | slender and lean frame |

### 5.5 裝備品質 (equipment)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| legendary | 傳說級 | wearing legendary ornate armor with intricate golden engravings and gemstones |
| fine | 精良級 | wearing well-crafted polished armor with decorative elements |
| common | 普通級 | wearing standard functional armor in decent condition |
| crude | 粗糙級 | wearing crudely made armor with visible rough patches |
| broken | 破損級 | wearing tattered and broken armor held together with rope |

### 5.6 武器品質 (weapon_quality)

| 代碼 | 中文 | Prompt 修飾詞 |
|------|------|--------------|
| artifact | 神器級 | legendary glowing artifact-tier |
| fine | 精良級 | finely crafted |
| common | 普通級 | standard |
| crude | 粗糙級 | crude and worn |
| primitive | 原始 | primitive makeshift |

### 5.7 武器類型 (weapon_type)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| sword | 長劍 | longsword |
| shield | 盾牌 | kite shield |
| staff | 法杖 | magical staff with glowing crystal |
| spellbook | 魔法書 | ancient spellbook with arcane symbols |
| bow | 弓 | longbow |
| dagger | 匕首 | twin daggers |
| mace | 錘 | war mace |
| spear | 長槍 | battle spear |
| short_sword | 短劍 | short sword |
| club | 棍棒 | wooden club |
| wooden_stick | 木棍 | simple wooden stick |
| stone | 石頭 | crude stone weapon |

### 5.8 背景場景 (background)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| palace_throne | 皇宮王座 | inside a grand palace throne room with golden pillars and red carpet |
| dragon_lair | 龍巢 | in a dragon's lair surrounded by treasure and glowing crystals |
| sky_city | 天空之城 | on a floating sky city with clouds and celestial architecture |
| castle | 城堡 | in a medieval stone castle with banners and torches |
| magic_tower | 魔法塔 | atop a wizard's tower with arcane circles and floating books |
| town | 城鎮 | in a bustling medieval town square |
| market | 市集 | at a lively market with merchant stalls |
| village | 小村落 | in a quiet rural village with thatched-roof cottages |
| wilderness | 荒野 | in an open wilderness with windswept grass |
| ruins | 破敗廢墟 | among crumbling ancient ruins overgrown with weeds |

### 5.9 表情 (expression)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| regal | 王者風範 | with a regal commanding gaze |
| passionate | 激昂 | with an intense passionate expression |
| confident | 自信 | with a confident determined look |
| calm | 平靜 | with a calm serene expression |
| weary | 疲憊 | with a weary exhausted expression |

### 5.10 姿勢 (pose)

| 代碼 | 中文 | Prompt 描述 |
|------|------|-------------|
| charging | 衝鋒陷陣 | charging forward in a dynamic action pose |
| battle_ready | 持武器備戰 | standing battle-ready with weapon drawn |
| standing | 站立 | standing upright in a neutral pose |
| crouching | 蹲坐 | crouching low with a guarded stance |

### 5.11 卡牌邊框 (border) 與等級 (level)

邊框和等級主要影響後續 UI 渲染，但可選擇性地在 prompt 中加入氛圍提示：

| border | 氛圍加成 |
|--------|---------|
| copper | muted earthy tones, humble atmosphere |
| silver | cool silver moonlit tones, refined atmosphere |
| gold | warm golden radiance, majestic epic atmosphere |

| level 區間 | 氛圍加成 |
|-----------|---------|
| 1-3 | beginner adventurer, simple and modest |
| 4-6 | seasoned adventurer, growing in power |
| 7-9 | powerful hero, impressive and formidable |
| 10 | legendary champion, awe-inspiring and magnificent |

### 5.12 Prompt 組裝範例

**輸入 card_config：**
```json
{
  "race": "elf", "gender": "female", "class": "mage",
  "body": "slim", "equipment": "legendary",
  "weapon_quality": "artifact", "weapon_type": "staff",
  "background": "magic_tower", "expression": "confident",
  "pose": "battle_ready", "border": "gold", "level": 8
}
```

**Step A — 組裝結構化描述（送入 Ollama）：**

依照第 5.1-5.11 節的映射表，將各屬性代碼替換為對應的 Prompt 描述，組合為 User Message 送入 Ollama LLM。

**Step B — Ollama 產出的 prompt（純角色描述，不含技術標籤）：**

```
A female Elf Mage with pointed ears and ethereal features, slender lean frame, wearing legendary ornate robes with intricate golden engravings and gemstones, standing battle-ready atop a wizard's tower with arcane circles and floating books, wielding a legendary glowing artifact-tier magical staff with glowing crystal, confident determined look, warm golden radiance, powerful hero, impressive and formidable, fantasy RPG character card portrait
```

> Ollama **只負責**產生角色/場景的自然語言描述。不包含 LoRA 觸發詞、風格前綴或任何技術標籤。

**Step C — sd_runner.py 自動組裝最終 prompt + seed（送入 sd-cli）：**

```python
# sd_runner.py 自動加上前綴
PROMPT_PREFIX = "<lora:moode_fantasy_Impressions:0.5> Digital painting, epic fantasy art, painterly texture, majestic and awe-inspiring atmosphere, high detail."

final_prompt = f"{PROMPT_PREFIX} {ollama_prompt}"
seed = int(student_number)   # 例如 "411234567" → 411234567
```

最終送入 sd-cli 的完整 prompt：

```
<lora:moode_fantasy_Impressions:0.5> Digital painting, epic fantasy art, painterly texture, majestic and awe-inspiring atmosphere, high detail. A female Elf Mage with pointed ears and ethereal features, slender lean frame, wearing legendary ornate robes with intricate golden engravings and gemstones, standing battle-ready atop a wizard's tower with arcane circles and floating books, wielding a legendary glowing artifact-tier magical staff with glowing crystal, confident determined look, warm golden radiance, powerful hero, impressive and formidable, fantasy RPG character card portrait
```

> **重要**：LoRA 觸發詞 `<lora:moode_fantasy_Impressions:0.5>`、風格前綴、seed 三者皆由 `sd_runner.py` 自動加上，不由 Ollama 產生。此設計確保 LoRA/前綴可透過環境變數 (`DEFAULT_PROMPT_PREFIX`) 統一管理，而 seed 固定為學號以確保同一學生的生成基底一致。

---

## 6. 任務佇列設計

### 6.1 設計原則

GPU 一次只能處理一張圖片。使用 `asyncio.Queue` 實現簡單的先進先出任務佇列。

### 6.2 架構

```python
import asyncio
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class GenerationJob:
    job_id: str
    card_id: int
    student_number: str
    card_config: dict
    learning_data: dict
    style_hint: str
    callback_url: str
    status: str = "queued"           # queued → processing → uploading → completed / failed
    prompt: str | None = None        # Ollama 產生的 prompt
    image_path: str | None = None
    thumbnail_path: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now())
    generated_at: datetime | None = None

class JobQueue:
    def __init__(self):
        self._queue: asyncio.Queue[GenerationJob] = asyncio.Queue()
        self._jobs: dict[str, GenerationJob] = {}  # job_id → job
        self._current_job: str | None = None

    async def enqueue(self, job: GenerationJob) -> int:
        """加入佇列，回傳佇列位置"""
        self._jobs[job.job_id] = job
        await self._queue.put(job)
        return self._queue.qsize() - 1

    def get_job(self, job_id: str) -> GenerationJob | None:
        return self._jobs.get(job_id)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def current_job_id(self) -> str | None:
        return self._current_job
```

### 6.3 Worker 處理迴圈

```python
async def worker_loop(queue: JobQueue):
    """持續從佇列取出任務並處理，一次一張。"""
    while True:
        job = await queue._queue.get()
        queue._current_job = job.job_id
        try:
            job.status = "processing"

            # Step 1: LLM prompt generation
            prompt = await generate_prompt_with_ollama(job.card_config, job.learning_data)
            job.prompt = prompt

            # Step 2: sd-cli image generation (seed = student_number, auto-prepend LoRA + prefix)
            output_path = await run_sd_cli(prompt, job.job_id, job.student_number)

            # Step 3: Generate thumbnail
            thumbnail_path = await create_thumbnail(output_path)

            # Step 4: Upload to vm-db-storage
            job.status = "uploading"
            image_path = await upload_to_storage(output_path, thumbnail_path, job.card_id)
            job.image_path = image_path["full"]
            job.thumbnail_path = image_path["thumbnail"]

            # Step 5: Callback
            job.status = "completed"
            job.generated_at = datetime.now(timezone.utc)
            await send_callback(job, status="completed")

        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            await send_callback(job, status="failed")
            logger.error("Job %s failed: %s", job.job_id, e)
        finally:
            queue._current_job = None
            queue._queue.task_done()
```

### 6.4 生命週期

- FastAPI 啟動時，在 `lifespan` 中建立 `JobQueue` 實例，並用 `asyncio.create_task` 啟動 `worker_loop`
- 關閉時，等待目前任務完成後退出

---

## 7. Mock / Stub 策略

### 7.1 vm-db-storage Mock

由於 vm-db-storage 尚未建立，圖片上傳使用 mock 實作：

```python
class MockStorageUploader:
    """Mock: 圖片存在 vm-ai-worker 本機 outputs/ 目錄"""

    async def upload(self, file_path, student_id, card_id, image_type):
        # 不實際上傳，回傳本機路徑作為 image_path
        relative_path = f"/students/{student_id}/cards/card_{card_id:03d}.png"
        return {"image_path": relative_path, "stored_at": datetime.now().isoformat()}

class RealStorageUploader:
    """真實上傳到 vm-db-storage"""

    async def upload(self, file_path, student_id, card_id, image_type):
        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    f"{DB_STORAGE_BASE_URL}/api/images/upload",
                    files={"file": f},
                    data={
                        "student_id": student_id,
                        "card_id": card_id,
                        "image_type": image_type,
                    }
                )
                resp.raise_for_status()
                return resp.json()
```

### 7.2 圖片直接提供（Mock 模式下）

Mock 模式下 vm-web-server 無法從 vm-db-storage 取得圖片，因此 vm-ai-worker 需要額外提供一個靜態檔案服務端點：

```
GET /api/images/{image_path:path}
```

vm-web-server 可在 mock 模式下改為從 `http://192.168.50.110/api/images/...` 讀取圖片。

### 7.3 環境切換

透過環境變數控制：
```
USE_MOCK_STORAGE=true   # true = 存本機, false = 上傳 vm-db-storage
```

---

## 8. 專案目錄結構

API 專案路徑：`/home/chihuah/ai-worker`

```
ai-worker/                       # /home/chihuah/ai-worker
├── main.py                      # FastAPI app 入口
├── requirements.txt             # pip 依賴清單
├── .env                         # 環境變數（不入版控）
├── .env.example                 # 環境變數範本
├── CLAUDE.md                    # Claude Code 開發指引（本規格書的精簡版）
├── app/
│   ├── __init__.py
│   ├── config.py                # 設定管理（讀取 .env）
│   ├── schemas.py               # Pydantic request/response 模型
│   ├── queue.py                 # JobQueue + GenerationJob
│   ├── worker.py                # worker_loop 主處理邏輯
│   ├── prompt_builder.py        # card_config → 結構化 prompt 組裝
│   ├── llm_service.py           # Ollama API 呼叫封裝
│   ├── sd_runner.py             # sd-cli subprocess 封裝（自動加入 LoRA/前綴/seed）
│   ├── storage_uploader.py      # vm-db-storage 上傳（含 mock）
│   ├── callback.py              # vm-web-server 回調邏輯
│   └── routers/
│       ├── __init__.py
│       ├── generate.py          # POST /api/generate
│       ├── jobs.py              # GET /api/jobs/{job_id}
│       └── health.py            # GET /api/health
├── outputs/                     # 生成的圖片暫存目錄
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_prompt_builder.py
│   ├── test_schemas.py
│   └── test_queue.py
└── scripts/
    └── test_generate.py         # 手動測試腳本
```

sd-cli 執行檔與 AI 模型檔案位於**獨立目錄**，不在 ai-worker 專案內：

```
stable-diffusion.cpp/            # /home/chihuah/stable-diffusion.cpp
├── build/
│   └── bin/
│       └── sd-cli               # 已編譯的 sd-cli 執行檔
└── models/                      # AI 模型檔案
    ├── z-image-turbo-Q8_0.gguf
    ├── FLUX_ae.safetensors
    ├── Qwen3-4b-Z-Image-Engineer-V4-Q8_0.gguf
    └── lora/
        └── moode_fantasy_Impressions.safetensors
```

> ai-worker 透過 `.env` 中的 `SD_CLI_PATH`、`MODEL_PATH`、`VAE_PATH`、`LLM_MODEL_PATH`、`LORA_DIR` 環境變數指向 stable-diffusion.cpp 目錄下的對應檔案。兩個目錄各自獨立管理。

---

## 9. Pydantic Schemas

```python
from pydantic import BaseModel, Field
from datetime import datetime

# === Request ===

class CardConfig(BaseModel):
    race: str
    gender: str
    class_: str = Field(alias="class")  # "class" 是 Python 保留字
    body: str
    equipment: str
    weapon_quality: str | None = None
    weapon_type: str | None = None
    background: str
    expression: str
    pose: str
    border: str
    level: int = Field(ge=1, le=10)

class UnitScore(BaseModel):
    quiz: float | None = None
    homework: float | None = None
    completion: float | None = None

class LearningData(BaseModel):
    unit_scores: dict[str, UnitScore]
    overall_completion: float

class GenerateRequest(BaseModel):
    job_id: str
    card_id: int
    student_number: str             # 學號（純數字），作為 sd-cli seed
    card_config: CardConfig
    learning_data: LearningData
    style_hint: str = "16-bit pixel art, fantasy RPG character card"
    callback_url: str

# === Response ===

class GenerateResponse(BaseModel):
    job_id: str
    status: str
    position: int
    message: str

class JobStatusResponse(BaseModel):
    job_id: str
    card_id: int
    status: str   # queued / processing / uploading / completed / failed
    image_path: str | None = None
    thumbnail_path: str | None = None
    prompt: str | None = None
    generated_at: str | None = None
    error: str | None = None

class HealthResponse(BaseModel):
    status: str
    gpu_available: bool
    ollama_available: bool
    sd_cli_available: bool
    queue_size: int
    current_job: str | None = None

# === Callback (送回 vm-web-server) ===

class GenerationCallback(BaseModel):
    job_id: str
    card_id: int
    status: str   # "completed" or "failed"
    image_path: str | None = None
    thumbnail_path: str | None = None
    generated_at: str | None = None
    error: str | None = None
```

---

## 10. 環境設定

### 10.1 環境變數 (.env.example)

```env
# === 應用設定 ===
APP_ENV=development
APP_DEBUG=true
APP_HOST=0.0.0.0
APP_PORT=8000

# === 路徑設定（指向 stable-diffusion.cpp 目錄下的檔案）===
SD_CLI_PATH=/home/chihuah/stable-diffusion.cpp/build/bin/sd-cli
MODEL_PATH=/home/chihuah/stable-diffusion.cpp/models/z-image-turbo-Q8_0.gguf
VAE_PATH=/home/chihuah/stable-diffusion.cpp/models/FLUX_ae.safetensors
LLM_MODEL_PATH=/home/chihuah/stable-diffusion.cpp/models/Qwen3-4b-Z-Image-Engineer-V4-Q8_0.gguf
LORA_DIR=/home/chihuah/stable-diffusion.cpp/models/lora
OUTPUT_DIR=./outputs

# === sd-cli 預設參數 ===
DEFAULT_HEIGHT=1280
DEFAULT_WIDTH=880
DEFAULT_STEPS=10
DEFAULT_CFG=1.0
DEFAULT_PROMPT_PREFIX=<lora:moode_fantasy_Impressions:0.5> Digital painting, epic fantasy art, painterly texture, majestic and awe-inspiring atmosphere, high detail.
# 注意：seed 不在此設定，固定使用 request body 中的 student_number（學號）

# === Ollama 設定 ===
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# === 外部 VM 服務 ===
WEB_SERVER_BASE_URL=http://192.168.50.111
DB_STORAGE_BASE_URL=http://192.168.50.112
USE_MOCK_STORAGE=true

# === 佇列設定 ===
MAX_QUEUE_SIZE=50
JOB_TIMEOUT=300
```

### 10.2 Conda 環境設定

```bash
# 啟用 conda 環境
conda activate sd-env

# 安裝 Python 依賴（在 sd-env 環境中）
pip install fastapi "uvicorn[standard]" httpx pydantic python-dotenv Pillow pytest pytest-asyncio

# 確認 Ollama 已安裝並啟動
ollama serve &
ollama pull llama3.1:8b    # 或其他選定的模型
```

> **Conda 環境資訊**
> - 環境名稱：`sd-env`
> - Python 路徑：`/home/chihuah/miniconda3/envs/sd-env/bin/python`
> - uvicorn 路徑：`/home/chihuah/miniconda3/envs/sd-env/bin/uvicorn`

### 10.3 systemd 服務（VM 重啟自動啟動）

> **原理**：systemd 直接使用 conda 環境中 uvicorn 的**絕對路徑**來啟動服務，不需要 `conda activate`。只要指定正確的可執行檔路徑，systemd 就能找到 conda 環境中的所有 Python 套件。

#### ai-worker.service（FastAPI API 服務）

```ini
# /etc/systemd/system/ai-worker.service
[Unit]
Description=Scholaverse AI Worker (FastAPI)
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=simple
User=chihuah
WorkingDirectory=/home/chihuah/ai-worker
Environment="PATH=/home/chihuah/miniconda3/envs/sd-env/bin:/usr/local/bin:/usr/bin"
ExecStart=/home/chihuah/miniconda3/envs/sd-env/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### ollama.service（LLM 推理服務）

Ollama 官方安裝腳本通常會自動建立 systemd service。可用以下指令確認：

```bash
systemctl status ollama
```

若未自動建立，手動建立：

```ini
# /etc/systemd/system/ollama.service
[Unit]
Description=Ollama LLM Service
After=network.target

[Service]
Type=simple
User=chihuah
ExecStart=/usr/local/bin/ollama serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### 啟用與管理

```bash
# 重新載入 systemd 設定
sudo systemctl daemon-reload

# 設為開機自動啟動
sudo systemctl enable ollama
sudo systemctl enable ai-worker

# 立即啟動
sudo systemctl start ollama
sudo systemctl start ai-worker

# 查看狀態
sudo systemctl status ai-worker
sudo systemctl status ollama

# 查看即時 log
sudo journalctl -u ai-worker -f
sudo journalctl -u ollama -f

# 重啟服務
sudo systemctl restart ai-worker
```

> **啟動順序**：ai-worker.service 設定了 `After=ollama.service` 和 `Wants=ollama.service`，確保 Ollama 先啟動，AI Worker API 再啟動。

### 10.4 常用指令

```bash
# ===== 開發模式 =====

# 啟動開發伺服器（需先進入 conda env）
conda activate sd-env
cd /home/chihuah/ai-worker
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 執行測試
conda activate sd-env
cd /home/chihuah/ai-worker
pytest tests/ -v

# 手動測試生圖
python scripts/test_generate.py

# ===== 健康檢查 =====

# 檢查 API 服務
curl http://localhost:8000/api/health

# 檢查 Ollama
curl http://localhost:11434/api/tags

# ===== systemd 服務管理 =====

# 查看服務狀態
sudo systemctl status ai-worker
sudo systemctl status ollama

# 重啟服務
sudo systemctl restart ai-worker

# 查看即時 log（除錯用）
sudo journalctl -u ai-worker -f --since "5 min ago"

# 查看 Ollama log
sudo journalctl -u ollama -f
```

---

## 11. 錯誤處理

### 11.1 錯誤場景與處理策略

| 場景 | 處理方式 |
|------|---------|
| Ollama 無回應 / 超時 | 標記 job 為 failed，error = "LLM service unavailable"，送 callback |
| sd-cli 程序異常退出 | 標記 job 為 failed，error = 包含 stderr 訊息，送 callback |
| sd-cli 超時 (>5 分鐘) | 終止 subprocess，標記 failed |
| 圖片上傳 vm-db-storage 失敗 | 重試 2 次，仍失敗則 fallback 存本機，callback 中帶本機路徑 |
| callback POST 失敗 | 重試 3 次（間隔 2, 5, 10 秒），記錄 log |
| 佇列已滿 (>50 tasks) | 回傳 HTTP 503 Service Unavailable |
| card_config 缺少必要欄位 | 回傳 HTTP 422 Validation Error（Pydantic 自動處理） |

### 11.2 Timeout 設定

| 操作 | Timeout |
|------|---------|
| Ollama prompt 生成 | 60 秒 |
| sd-cli 文生圖 | 300 秒 (5 分鐘) |
| 上傳 vm-db-storage | 60 秒 |
| Callback POST | 15 秒 |

---

## 12. vm-db-storage API 規格（供日後開發參考）

### 12.1 系統定位

vm-db-storage (192.168.50.112) 負責持久化儲存 AI 產生的圖片檔案與 metadata。兩個消費者：

- **vm-ai-worker**：上傳圖片（寫入）
- **vm-web-server**：讀取圖片與 metadata（唯讀）

### 12.2 API 端點

#### 寫入端（供 vm-ai-worker 呼叫）

| 端點 | 方法 | 說明 |
|------|------|------|
| `POST /api/images/upload` | POST | 上傳圖片 |
| `DELETE /api/images/{image_path}` | DELETE | 刪除圖片（管理用） |

**`POST /api/images/upload`**

```
Content-Type: multipart/form-data

Fields:
  file: (binary) 圖片檔案
  student_id: (int) 學生 ID
  card_id: (int) 卡牌 ID
  image_type: (string) "full" | "thumbnail"
  metadata: (JSON string, optional) {
    "prompt": "...",
    "model": "flux-turbo",
    "seed": 12345,
    "steps": 10,
    "dimensions": {"width": 880, "height": 1280}
  }
```

**Response (201 Created)：**
```json
{
  "image_path": "/students/1/cards/card_001.png",
  "file_size": 524288,
  "stored_at": "2026-02-22T12:00:00Z"
}
```

#### 讀取端（供 vm-web-server 呼叫）

| 端點 | 方法 | 說明 |
|------|------|------|
| `GET /api/images/{image_path:path}` | GET | 讀取圖片檔案（回傳 binary） |
| `GET /api/images/list` | GET | 列出某學生的所有圖片 |
| `GET /api/metadata/{card_id}` | GET | 讀取圖片 metadata |

**`GET /api/images/list?student_id=1`**

```json
[
  {
    "image_path": "/students/1/cards/card_001.png",
    "thumbnail_path": "/students/1/cards/card_001_thumb.png",
    "card_id": 1,
    "created_at": "2026-02-10T10:00:00Z"
  }
]
```

**`GET /api/metadata/{card_id}`**

```json
{
  "card_id": 1,
  "prompt": "Digital painting, epic fantasy art...",
  "model": "flux-turbo",
  "dimensions": {"width": 880, "height": 1280},
  "file_size_bytes": 524288,
  "generated_at": "2026-02-22T12:00:00Z"
}
```

#### 健康檢查

| 端點 | 方法 | 說明 |
|------|------|------|
| `GET /api/health` | GET | 健康檢查 |

```json
{
  "status": "ok",
  "storage_used_bytes": 1073741824,
  "storage_available_bytes": 53687091200,
  "image_count": 156
}
```

### 12.3 儲存路徑結構

```
/data/images/
├── students/
│   ├── 1/
│   │   └── cards/
│   │       ├── card_001.png
│   │       ├── card_001_thumb.png
│   │       ├── card_002.png
│   │       └── card_002_thumb.png
│   ├── 2/
│   │   └── cards/
│   │       └── ...
│   └── ...
└── metadata/
    ├── card_001.json
    ├── card_002.json
    └── ...
```

### 12.4 技術建議

- 框架：FastAPI（與其他 VM 一致）
- 儲存：本機檔案系統（簡單可靠）
- 檔案命名：`card_{card_id:03d}.png` / `card_{card_id:03d}_thumb.png`
- 環境管理：uv 或 pip（無 GPU 需求，無需 conda）

---

## 13. 待確認事項

| # | 項目 | 狀態 |
|---|------|------|
| 1 | Ollama 上要部署哪一個模型（llama3.1:8b? 其他?） | 待確認 |
| 2 | Ollama prompt 工程的 system prompt 精調 | 開發中逐步優化 |
| 3 | sd-cli 是否有新增的 preset 需求（不同尺寸/風格） | 待確認 |
| 4 | vm-db-storage 確切的上線時程 | 待確認 |
| 5 | 圖片是否需要後處理（浮水印、品質壓縮等） | 待確認 |
| 6 | 是否需要支援批次生成（一次多張） | 目前不需要，保留擴展空間 |
