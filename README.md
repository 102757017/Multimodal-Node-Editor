# 多模态节点编辑器（重构版）

基于 [Multimodal-Node-Editor](https://github.com/102757017/Multimodal-Node-Editor) 项目重构的可视化节点编辑器，类似 VisionMaster。核心执行引擎已**完全重写**，支持复杂的帧同步、跨层级数据访问、动态端口、分片执行和全局模型注册表。

![Next.js](https://img.shields.io/badge/Next.js-16-black) ![Python](https://img.shields.io/badge/Python-3.10+-blue) ![ReactFlow](https://img.shields.io/badge/ReactFlow-12-cyan) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green)

---

## 目录

- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [环境要求](#环境要求)
- [安装步骤](#安装步骤)
- [快速启动](#快速启动)
  - [1. GUI 模式（run_gui.py）](#1-gui-模式run_guipy)
  - [2. 无头模式（run_headless.py）](#2-无头模式run_headlesspy)
- [界面使用指南](#界面使用指南)
- [原始节点使用说明](#原始节点使用说明)
- [全局模型注册表](#全局模型注册表)
- [添加自定义数据类型](#添加自定义数据类型)
- [配置文件格式](#配置文件格式)
- [API 接口参考](#api-接口参考)
- [项目结构](#项目结构)
- [常见问题](#常见问题)

---

## 核心特性

### 重构的执行引擎

| 特性 | 说明 |
|---|---|
| **三状态模型** | `frame_complete`（帧完成）/ `idle`（空闲）/ `exhausted`（输入耗尽），不使用单一"完成"布尔值，清晰区分"帧结束"与"无更多数据"。 |
| **分片执行** | `execute_step()` 每次处理所有就绪节点；`execute_generator()` 生成器模式逐步执行至帧完成。支持批处理和流式两种模式。 |
| **帧同步** | 汇总模式：等待所有已连接输入端口在本帧都有数据。可配置超时（默认 5 秒），超时后跳过该节点继续执行，不中断整个图。帧缓存保留最近 N 帧数据。 |
| **触发模式** | `ALL`（所有输入就绪才执行，每帧最多一次）或 `ANY`（任意输入更新即执行，同帧可多次触发）。每节点独立配置 `trigger_mode` 字段。 |
| **跨层级数据访问** | 节点输入不仅可来自直连，还可通过配置面板的 ComboBox 选择任意上游节点的匹配类型输出端口。连线优先；有连线时 ComboBox 自动禁用。基于拓扑排序过滤，防止环。 |
| **动态端口** | 真正在运行时创建/删除端口（非隐藏）。连接到最后一个端口时自动创建新端口。删除后重新编号。模板命名 `{前缀} {序号}`，可重命名显示名。至少保留 `min_count` 个。 |
| **源节点识别** | 无输入端口的节点默认为源节点；可在 node.toml 中用 `is_source_node` 显式覆盖。`source_depleted` 标志驱动 `exhausted` 状态。 |

### 全局模型注册表

图中多个节点使用同一个大型模型（CLIP、LLM、ONNX 会话、MediaPipe）时，通过进程级全局注册表共享**同一个**实例，避免内存溢出：
- 线程安全的 per-key 锁（加载器只调用一次）
- 错误缓存（加载失败后不会每帧重试）
- LRU 淘汰（按数量和字节数）
- 实时快照 API 供 UI 监控

### 自动加载 131 个预设节点

原始项目 `src/nodes/` 下的全部 124 个节点在启动时自动发现并注册（122 个有实际计算逻辑，2 个因缺少 `mediapipe` 等可选依赖而 stub）。另含 7 个内置演示节点。**新增节点只需把文件夹放入 `src/nodes/`，重启后端即可自动出现在面板中，无需改代码。**

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│  浏览器（Next.js，端口 3000）                              │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ 节点面板     │  │  ReactFlow   │  │ 配置/模型 面板  │  │
│  │ (可折叠树)   │  │   画布       │  │   （标签页）    │  │
│  └─────────────┘  └──────┬───────┘  └────────────────┘  │
│                          │ fetch /api/*?XTransformPort=3030
└──────────────────────────┼──────────────────────────────┘
                           │  Caddy 网关（端口 81）
┌──────────────────────────┼──────────────────────────────┐
│  FastAPI 后端（端口 3030）                                 │
│  ┌────────────┐  ┌───────────────┐  ┌────────────────┐  │
│  │  main.py   │  │   core.py     │  │model_registry  │  │
│  │ (REST API) │→ │ (三状态执行)   │  │   .py (共享    │  │
│  │            │  │  execute_step │  │   模型缓存)    │  │
│  └────────────┘  └───────┬───────┘  └────────────────┘  │
│                          │                               │
│  ┌───────────────────────┴──────────────────────────┐   │
│  │  node_def.py + discovery.py（131 个节点）          │   │
│  │  • 7 个内置演示节点                               │   │
│  │  • 124 个原始节点（自动从 src/nodes/ 加载）        │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

---

## 环境要求

| 依赖 | 版本 |
|---|---|
| Python | 3.10+（3.10 需安装 `tomli`，3.11+ 自带 `tomllib`） |
| Node.js 或 Bun | Node 18+ / Bun 1.0+ |
| 操作系统 | Linux、macOS、Windows |

---

## 安装步骤

### 第 1 步：安装前端依赖

```bash
# 在项目根目录执行
bun install
# 或
npm install
```

### 第 2 步：安装后端依赖

```bash
cd mini-services/node-editor-server

# 核心依赖（必需）
pip install fastapi "uvicorn[standard]" pydantic pillow numpy tomli

# 可选依赖——原始图像/音频深度学习节点需要
pip install opencv-python onnxruntime mediapipe
```

> **Python 3.10 注意**：标准库不含 `tomllib`，必须安装 `tomli`（`pip install tomli`）。代码已自动兼容：优先用 `tomllib`，找不到时回退到 `tomli`。

### 第 3 步：放置节点文件

后端启动时自动从以下目录搜索节点（按优先级）：

1. **`mini-services/node-editor-server/nodes/`** ← 本地节点目录（推荐，用户放置节点的地方）
2. `download/multimodal-node-editor/src/nodes/` ← 原始克隆仓库（沙箱环境用）

把原始项目的 `src/nodes/` 文件夹复制到 `mini-services/node-editor-server/nodes/` 即可：

```
mini-services/node-editor-server/
├── nodes/                     ← 把原始 src/nodes/ 复制到这里
│   ├── audio/
│   │   ├── analysis/
│   │   ├── input/
│   │   └── ...
│   ├── image/
│   │   ├── draw/
│   │   ├── deep_learning/
│   │   └── ...
│   ├── math/
│   └── text/
├── discovery.py
├── main.py
└── ...
```

无需修改任何 `node.toml` 或 `impl.py`——发现模块自动处理所有原始格式。

### 第 4 步：确认前后端通信

前端通过 Next.js rewrite 代理 `/api/*` 到后端 `http://localhost:3030`，**无需 Caddy 网关**。直接访问 `http://localhost:3000` 即可。如果你把后端跑在其他端口，设置环境变量 `BACKEND_URL` 后重启前端：

```bash
# Windows PowerShell
$env:BACKEND_URL="http://localhost:8000"; bun run dev

# Linux/macOS
BACKEND_URL=http://localhost:8000 bun run dev
```

---

## 快速启动

### 1. GUI 模式（`run_gui.py`）

同时启动后端（端口 3030）和前端（端口 3000），然后自动打开浏览器。

```bash
# 在项目根目录执行
python run_gui.py
```

**参数选项：**

```bash
python run_gui.py --no-browser        # 不自动打开浏览器
python run_gui.py --backend-only       # 仅启动后端
python run_gui.py --frontend-only      # 仅启动前端（后端需已运行）
python run_gui.py --backend-port 8000  # 自定义后端端口
python run_gui.py --frontend-port 8080 # 自定义前端端口
```

启动后在浏览器访问 `http://localhost:3000`。

> **Windows 用户**：脚本通过 `shutil.which()` 查找 `npm`/`npx`/`bun`，能正确识别 `npm.CMD` 等批处理文件。如果仍报找不到，请确认 `npm` 在系统 PATH 中（`where npm` 能找到）。

### 2. 无头模式（`run_headless.py`）

无需 UI，直接运行已保存的图——最高性能，无 HTTP 开销，numpy 数组在节点间直接传递。

```bash
# 在项目根目录执行
python run_headless.py <graph.json> [选项]

# 或在后端目录执行
cd mini-services/node-editor-server
python run_headless.py <graph.json> [选项]
```

**示例：**

```bash
# 列出所有 131 个可用节点
python run_headless.py --list-nodes

# 永久运行，用 cv2 窗口显示图像
python run_headless.py my_graph.json

# 以 100ms 间隔运行 10 帧
python run_headless.py my_graph.json --count 10 --interval 100

# 无显示（服务器环境），将终端输出图像保存到 out/ 目录
python run_headless.py my_graph.json --no-display --output-dir out/

# 流式模式——源节点逐帧推送数据，耗尽后自动停止
python run_headless.py my_graph.json --stream

# 执行后显示全局模型注册表状态
python run_headless.py my_graph.json --count 5 --show-models
```

**如何获取 graph.json**：在 GUI 中构建图，点击顶栏的 **Save** 按钮，文件会保存到 `mini-services/node-editor-server/saves/` 目录。

---

## 界面使用指南

### 顶栏

| 按钮 | 功能 |
|---|---|
| **Run** | 流式模式：反复"开始新帧 + 执行"直到源节点耗尽 |
| **Step Frame** | 执行一帧（start_frame → execute_step 至 frame_complete） |
| **Reset** | 清除所有执行状态 |
| **Save** | 将当前图保存为 `saves/graph-<时间戳>.json` |
| **Load** | 重新加载上次保存的图 |

状态徽章显示：`READY` / `RUNNING` / `FRAME COMPLETE` / `IDLE` / `EXHAUSTED`。

### 左侧面板——节点面板

- 可折叠的分类树（IMAGE / AUDIO / MATH / TEXT / OPENAI / UTILITY / AI / SOURCE / DISPLAY）
- 搜索框过滤全部 131 个节点
- 每个节点显示输入/输出端口类型色点和 `[src]`/`[dyn]`/`N/A` 徽章
- 点击节点即添加到画布

### 中间——画布

- 从输出端口（右侧彩色圆点）拖到输入端口（左侧）建立连线
- 每个端口有**独立的**圆点——连线连接到正确的端口
- 点击节点选中（青色高亮环），在右侧面板编辑
- **动态端口**：鼠标悬停在未连接的动态输入端口上会显示删除（×）按钮
- 连接到动态端口组的最后一个端口时自动创建新端口
- 图像节点始终显示预览框（执行前为空占位框，执行后显示实际图像）

### 右侧面板——配置 / 模型 标签页

**配置标签页**（选中节点时显示）：
- 节点名称、触发模式（ALL/ANY）
- 属性（数字 / 下拉 / 复选框 / 颜色 / 滑块 / 文件选择 / 文本域——与 node.toml schema 匹配）
- **输入源**——每个输入端口的 ComboBox；列出所有拓扑上游的兼容输出端口。端口有连线时禁用。
- **动态端口组**——添加按钮、数量显示（如 `2/8`）

**模型标签页**：
- 内存使用条（绿/黄/红）
- 已加载模型列表：key、标签、大小、加载次数、命中次数、最后使用时间
- 单个模型卸载按钮 + "全部卸载"按钮

### Draw Mask / Draw Canvas 节点的绘制

- 预览框上有 canvas 覆盖层，带 `nodrag` 类（鼠标拖动是绘制，不是拖动节点）
- 工具栏：画笔 / 橡皮擦 / 清除
- 笔画持久化到后端 `draw_commands` 属性
- 即使未连接图像也可绘制（在黑色画布上画）

---

## 原始节点使用说明

**可以——原始节点可以直接复制使用，无需任何修改。** 发现模块已处理所有原始 `node.toml` 格式：

| 格式 | 支持 | 说明 |
|---|---|---|
| `[[ports]]` + `direction` | ✅ 完全支持 | 124 个原始节点全部使用此格式 |
| `[[inputs]]` / `[[outputs]]`（旧格式） | ✅ 完全支持 | 当前仓库无此格式，但已支持 |
| `dynamic_ports = "Image"`（字符串） | ✅ 自动转换为 DynamicPortConfig | 如 `image/draw/multi_image_concat` |
| `[dynamic_ports.inputs]` 表格 | ✅ 完全支持 | 新式写法 |
| `visible_when` 内联表 | ✅ 已解析 | 如 `face_detection` 有 7 个此类属性 |
| `options_source`、`requires_api_key`、`requires_gpu` | ✅ 已解析 | 多个节点使用 |
| `widget = "slider"` / `"button"` / `"file_picker"` / `"text_area"` | ✅ 已解析 | 多个节点使用 |

### 添加新节点的方法

1. 在 `mini-services/node-editor-server/nodes/<分类>/<节点名>/` 下创建文件夹
2. 添加 `node.toml` 和 `impl.py`（impl.py 必须定义 `ComputeLogic` 子类）
3. 重启后端——节点自动出现在面板中

### impl.py 编写要求

```python
from node_editor.node_def import ComputeLogic  # 通过 shim 包导入，无需改路径

class MyNodeLogic(ComputeLogic):
    def compute(self, inputs, properties, context=None):
        # inputs: 端口名 -> 值 的字典
        # properties: 属性名 -> 值 的字典
        # context: 包含 'node_id' 等信息的字典
        return {"输出端口名": 结果值}
```

### 接入全局模型缓存（重型模型推荐）

将节点中的模型加载代码改为 3 行即可加入全局缓存：

```python
# 改造前——每个节点各自加载一份
session = onnxruntime.InferenceSession(model_path, providers=providers)
self._model_cache[key] = session

# 改造后——全局共享
model, err = self._get_cached_model(
    f"onnx:{model_path}:gpu={use_gpu}",
    lambda: onnxruntime.InferenceSession(model_path, providers=providers),
    label="onnx",
)
if err:
    return {"__error__": err}
# 使用 model ...
```

`_get_cached_model` 从 `ComputeLogic` 基类继承，无需额外 import。

### 依赖缺失时的行为

如果 `impl.py` 导入失败（如未安装 `mediapipe`），节点会以 **stub** 形式注册，compute 返回错误信息。节点仍出现在面板中，带 `N/A` 徽章，提示需要安装依赖。

---

## 全局模型注册表

解决图中多个节点各自加载同一大型模型导致内存溢出的问题。

### 工作原理

```python
from node_editor.model_registry import registry
# 或在 ComputeLogic 子类中：
model, err = self._get_cached_model(key, loader, est_bytes=..., label=...)
```

- `loader()` 每个 `key` **只调用一次**（线程安全，per-key 锁）
- 相同 key 的后续调用返回缓存实例
- 加载错误会被缓存（不会每帧重试）
- 超过 16 个条目或超过 8 GB 时 LRU 淘汰最久未用的模型
- `unload(key)` / `clear()` 手动释放

### 监控方式

右侧面板的 **模型** 标签页显示：
- 每个已加载模型：key、标签、估算大小、加载次数、命中次数、最后使用时间
- 总内存使用量，带颜色进度条
- 卸载单个模型 / 全部卸载按钮

API 接口：
- `GET /api/models` — 注册表快照
- `DELETE /api/models/{key}` — 卸载单个模型
- `DELETE /api/models` — 卸载所有模型

---

## 添加自定义数据类型

节点编辑器中的数据类型（data_type）是**纯字符串**，没有中央注册表。这意味着你可以直接在 `node.toml` 中使用任何字符串作为 `data_type`，无需注册。

### 内置数据类型

| data_type | 说明 | 颜色（UI 端口圆点） |
|---|---|---|
| `float` | 浮点数 | 绿色 |
| `int` | 整数 | 亮绿色 |
| `string` | 字符串 | 琥珀色 |
| `bool` | 布尔值 | 紫色 |
| `image` | 图像（numpy 数组 / base64） | 粉色 |
| `audio` | 音频 | 青色 |
| `any` | 任意类型（兼容所有） | 灰色 |

### 类型兼容规则

连线时，类型兼容性由 `core.py` 的 `_types_compatible()` 决定：
- `any` 兼容所有类型（双向）
- 相同类型兼容
- `int` ↔ `float` 自动转换（int 输入接 float 会转 int，float 输入接 int 会转 float）
- 其他组合不兼容（连线会被拒绝）

### 添加新数据类型

**1. 在 node.toml 中使用新类型名**

```toml
# 例：自定义 "point_cloud" 类型
[[ports]]
name = "cloud"
data_type = "point_cloud"
direction = "out"
```

**2. （可选）给新类型指定 UI 颜色**

编辑 `src/lib/node-editor/types.ts` 的 `TYPE_COLORS`：

```typescript
export const TYPE_COLORS: Record<string, string> = {
  float: "#10b981",
  int: "#22c55e",
  // ... 现有类型
  point_cloud: "#8b5cf6",  // 新增
};
```

**3. （可选）让新类型与现有类型兼容**

编辑 `mini-services/node-editor-server/core.py` 的 `_types_compatible()`：

```python
def _types_compatible(src_type: Any, dst_type: Any) -> bool:
    if src_type == "any" or dst_type == "any":
        return True
    if src_type == dst_type:
        return True
    if {src_type, dst_type} <= {"int", "float"}:
        return True
    # 新增：point_cloud 兼容 image（点云可渲染为图像）
    if {src_type, dst_type} <= {"point_cloud", "image"}:
        return True
    return False
```

**4. 在 impl.py 中处理新类型的序列化**

如果新类型不能直接 JSON 序列化，需要在 `core.py` 的 `_serialize_output()` 和 `_decode_image_input()` 中添加转换逻辑（参考 image 类型的处理）。

### 完整示例：添加 "tensor" 类型

```toml
# nodes/custom/tensor_source/node.toml
[[ports]]
name = "tensor"
data_type = "tensor"
direction = "out"
```

```python
# nodes/custom/tensor_source/impl.py
from node_editor.node_def import ComputeLogic
import numpy as np

class TensorSourceLogic(ComputeLogic):
    def compute(self, inputs, properties, context=None):
        return {"tensor": np.zeros((3, 224, 224), dtype=np.float32)}
```

```typescript
// src/lib/node-editor/types.ts — 添加颜色
export const TYPE_COLORS: Record<string, string> = {
  // ...
  tensor: "#f97316",  // 橙色
};
```

```python
# core.py — 让 tensor 兼容 image（可可视化）
if {src_type, dst_type} <= {"tensor", "image"}:
    return True
```

### ComboBox 跨层级访问

新类型自动支持 ComboBox 跨层级访问——只要上游节点的输出端口类型与当前节点的输入端口类型兼容，就会出现在 ComboBox 候选列表中（前提是有连线建立了上游 pipeline）。

---

## 配置文件格式

保存的图（`saves/graph-*.json`）结构如下：

```json
{
  "id": "graph-abc123",
  "graph_format_version": "1.0.0",
  "nodes": [
    {
      "id": "node-abc12345",
      "definition_id": "math.add",
      "definition_version": "1.0.0",
      "name": "Add",
      "inputs": [
        {"id": "port-...", "name": "a", "display_name": "A", "data_type": "float", "direction": "in", "preview": false, "metadata": {}}
      ],
      "outputs": [
        {"id": "port-...", "name": "result", "display_name": "Result", "data_type": "float", "direction": "out", "preview": false, "metadata": {}}
      ],
      "properties": {"value": 3.0, "input_sources": {"a": "node-xxx.value"}},
      "position": {"x": 300, "y": 75},
      "trigger_mode": "ALL",
      "dynamic_port_configs": {},
      "is_source_node": null
    }
  ],
  "connections": [
    {"id": "conn-...", "from_node_id": "node-...", "from_port_id": "port-...", "to_node_id": "node-...", "to_port_id": "port-..."}
  ]
}
```

相比原始格式新增的字段：`trigger_mode`、`dynamic_port_configs`、`is_source_node`、`input_sources`（在 `properties` 内）、以及每个端口的 `metadata`（用于动态端口标记）。

---

## API 接口参考

基础地址：`http://localhost:3030`（直连）或通过 Caddy 网关加 `?XTransformPort=3030`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/nodes` | 列出所有节点定义 + 分类树 |
| GET | `/api/graph` | 获取当前图 |
| POST | `/api/graph/nodes` | 添加节点 `{definition_id, position, name}` |
| DELETE | `/api/graph/nodes/{id}` | 删除节点 |
| POST | `/api/graph/connections` | 添加连线 `{from_node_id, from_port_id, to_node_id, to_port_id}` |
| DELETE | `/api/graph/connections/{id}` | 删除连线 |
| PUT | `/api/graph/nodes/{id}/properties` | 更新属性 `{properties: {...}}` |
| PUT | `/api/graph/nodes/{id}/trigger-mode` | 设置 ALL/ANY `{mode: "ALL"}` |
| PUT | `/api/graph/nodes/{id}/input-source` | 设置 ComboBox 数据源 `{port_name, source}` |
| GET | `/api/graph/nodes/{id}/combobox/{port_name}` | 列出端口的上游候选 |
| POST | `/api/graph/nodes/{id}/dynamic-port` | 添加动态端口 `{group_name}` |
| DELETE | `/api/graph/nodes/{id}/dynamic-port/{port_id}` | 删除动态端口 |
| PUT | `/api/graph/nodes/{id}/port/{port_id}/rename` | 重命名端口显示名 |
| POST | `/api/graph/start-frame` | 开始新帧 |
| POST | `/api/graph/source-data/{id}` | 推送源数据 `{data: {port_name: value}}` |
| POST | `/api/graph/mark-depleted/{id}` | 标记源节点已耗尽 |
| POST | `/api/graph/execute-step` | 执行一步 `{context: {...}}` → `{status, outputs, errors, ...}` |
| POST | `/api/graph/reset-frame` | 重置执行状态 |
| GET | `/api/graph/status` | 获取帧 id、耗尽状态、同步超时 |
| POST | `/api/graph/save` | 保存到文件 `{path?}` |
| POST | `/api/graph/load` | 从文件加载 `{data? 或 path?}` |
| GET | `/api/models` | 模型注册表快照 |
| DELETE | `/api/models/{key}` | 卸载单个模型 |
| DELETE | `/api/models` | 卸载所有模型 |

---

## 项目结构

```
.
├── run_gui.py                          # ← 快速启动：后端 + 前端
├── run_headless.py                     # ← 快速启动：无头执行
├── README.md                           # ← 本文件
├── package.json                        # Next.js 前端
├── src/                                # 前端（Next.js 16 + ReactFlow）
│   ├── app/
│   │   ├── page.tsx                    # 主编辑器页面
│   │   └── layout.tsx
│   ├── components/
│   │   ├── node-editor/
│   │   │   ├── CustomNode.tsx          # ReactFlow 节点（端口、预览、绘制）
│   │   │   ├── NodePalette.tsx         # 可折叠分类树
│   │   │   ├── ConfigPanel.tsx         # 属性 + ComboBox + 动态端口
│   │   │   └── ModelsPanel.tsx         # 全局模型注册表视图
│   │   └── ui/                         # shadcn/ui 组件
│   └── lib/
│       └── node-editor/
│           ├── types.ts                # TypeScript 类型
│           └── api.ts                  # API 客户端
│
├── mini-services/
│   └── node-editor-server/             # 后端（FastAPI，端口 3030）
│       ├── main.py                     # REST API
│       ├── core.py                     # 重构的三状态执行引擎
│       ├── models.py                   # Pydantic 模型
│       ├── node_def.py                 # NodeDefinition + 7 个内置演示节点
│       ├── discovery.py                # 自动加载 124 个原始节点
│       ├── model_registry.py           # 全局共享模型缓存
│       ├── run_headless.py             # 无头运行器（项目根目录也有副本）
│       └── node_editor/                # shim 包，使 impl.py 的 import 正常工作
│           ├── __init__.py
│           ├── node_def.py
│           ├── model_registry.py
│           ├── image_utils.py
│           └── settings.py
│
├── download/
│   └── multimodal-node-editor/         # 原始克隆仓库（提供 src/nodes/）
│       └── src/nodes/                  # 124 个预设节点（自动发现）
│
├── Caddyfile                           # 网关配置（端口 81 → 3000，XTransformPort）
└── worklog.md                          # 开发日志
```

---

## 常见问题

### 后端无法启动

```bash
# 确认 Python 版本（需 3.10+）
python3 --version

# 安装依赖
cd mini-services/node-editor-server
pip install fastapi "uvicorn[standard]" pydantic pillow numpy tomli
```

### Python 3.10 报 `No module named 'tomllib'`

Python 3.10 标准库不含 `tomllib`，需安装 backport：

```bash
pip install tomli
```

代码已自动兼容：优先用 `tomllib`（3.11+），找不到时回退到 `tomli`。

### 节点显示 "N/A" 徽章

节点的 `impl.py` 导入失败——通常是缺少可选依赖。查看后端日志中的 `[stub]` 警告：

```
[stub] image.deep_learning.classification: impl.py load failed — ModuleNotFoundError: No module named 'mediapipe'
```

安装缺失的包（如 `pip install mediapipe onnxruntime opencv-python`）后重启。

### Windows 下 run_gui.py 报 "Neither bun nor npm found"

脚本通过 `shutil.which()` 查找 `npm`/`npx`/`bun`，能识别 Windows 的 `.CMD`/`.BAT` 扩展名。如仍报错：

1. 确认 npm 在 PATH 中：在命令行执行 `where npm`，应返回类似 `C:\Program Files\nodejs\npm.CMD`
2. 如果 `where npm` 找不到，需重新安装 Node.js 并勾选"Add to PATH"
3. 重启终端后重试

### UI 显示 "Loading nodes…" 且右上角报 404

这表示前端无法连接后端。原因和解决方案：

1. **后端未启动** — 确认 `http://localhost:3030/api/nodes` 能返回 JSON：
   ```bash
   curl http://localhost:3030/api/nodes
   ```
   如果连接失败，启动后端：`cd mini-services/node-editor-server && python -m uvicorn main:app --port 3030 --reload`

2. **后端端口不是 3030** — 设置 `BACKEND_URL` 环境变量后重启前端：
   ```bash
   # Windows PowerShell
   $env:BACKEND_URL="http://localhost:8000"; bun run dev
   ```

3. **Next.js rewrite 未生效** — `next.config.ts` 中应有 `rewrites()` 把 `/api/*` 代理到后端。如果修改了 next.config.ts 需重启前端。

### 节点放进了 nodes/ 目录但面板里看不到

1. 确认路径正确：节点应在 `mini-services/node-editor-server/nodes/<分类>/<节点名>/node.toml`
2. 查看后端启动日志，应有 `Discovering nodes in: .../nodes` 和节点计数
3. 确认 `node.toml` 中有 `name = "xxx.yyy"` 字段（definition_id）
4. 如果 `impl.py` 导入失败，节点会以 stub 形式出现，带 `N/A` 徽章——检查后端日志的 `[stub]` 警告

### 前端无法连接后端（旧）

前端通过 Next.js rewrite 代理 `/api/*` 到后端 `http://localhost:3030`，**无需 Caddy 网关**。直接访问 `http://localhost:3000` 即可。如果你把后端跑在其他端口，设置环境变量 `BACKEND_URL` 后重启前端。

### 端口圆点与端口文字不对齐

每个 Handle 通过 `top: 50%` 相对于端口行（`position: relative`）定位。如自定义节点 CSS，请确保：
- 端口行保持 `position: relative`
- 左侧 Handle 的 `transform` 用 `translate(-50%, -50%)`，右侧用 `translate(50%, -50%)`
- body padding 为 `px-3`（12px）—— Handle 偏移 `-12px` 到节点边框

### Draw Mask 上绘制时拖动了节点

canvas 覆盖层需带 `nodrag` 类。如替换了组件，确保 `<canvas>` 及其容器有 `className="... nodrag"`。

### 启用全局缓存后内存仍高

注册表只共享通过 `_get_cached_model` 加载的模型。未迁移的原始节点仍用各自的 `_model_cache`。迁移方法见[原始节点使用说明](#接入全局模型缓存重型模型推荐)。

---

## 许可证

本项目基于 [Multimodal-Node-Editor](https://github.com/102757017/Multimodal-Node-Editor)（MIT 许可证）构建。详见原始仓库的 `LICENSE`。
