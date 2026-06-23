# Node Development Guide

> 适用版本：refactored engine v1.0.0+
>
> Python ≥ 3.10 · FastAPI 0.115+ · ReactFlow 12
>

---

## 1. 概述

本文档面向希望为 Multimodal Node Editor 贡献新节点的第三方工程师。节点编辑器采用「声明式定义 + Python 计算」的架构：每个节点的端口、属性、UI 行为由一个 `node.toml` 文件描述，节点的实际计算逻辑由同目录下的 `impl.py` 提供。后端启动时会自动扫描节点目录、解析 TOML、动态加载 `impl.py` 中的 `ComputeLogic` 子类，并将节点注册到全局节点注册表中。这意味着新增节点无需修改任何已有代码——只需要把节点文件夹放到正确位置然后重启后端。

本指南假设读者已经阅读过项目的 `README.md` 并对节点编辑器的基本概念（节点、端口、连线、属性面板、帧执行模型）有大致了解。我们将从最简单的「Hello World」节点开始，逐步介绍 `node.toml` 的完整 schema、`impl.py` 的编写要求、动态端口、全局模型缓存、自定义数据类型，最后给出一个完整的实战示例和常见调试技巧。读完本文档后，你应该能够独立地为节点编辑器添加任意复杂度的节点。

所有节点都遵循同一个执行模型：每一帧，执行引擎按拓扑顺序遍历所有节点，对每个就绪的节点调用其 `compute()` 方法，传入收集到的输入和属性，节点返回一个端口名到输出值的字典。引擎负责把输出值推送给下游节点的对应输入端口，并在 UI 上更新预览。理解这个执行模型对写出行为正确的节点至关重要——尤其是「帧同步」机制：默认情况下，节点只在所有已连接的输入端口都有当前帧的数据时才会执行。

---

## 2. 五分钟上手：第一个节点

我们先实现一个最简单的节点：**String Uppercase**——接收一个字符串输入，输出其大写形式。这个节点足以演示完整的最小工作流：创建目录、写 `node.toml`、写 `impl.py`、重启后端验证。

### 2.1 创建节点目录

节点必须放在后端的节点搜索路径下。最推荐的位置是 `mini-services/node-editor-server/nodes/` 目录（项目自带的其他节点也都放在这里）。每个节点是一个独立的子目录，目录名通常与节点 `definition_id` 的最后一段一致。

在 `mini-services/node-editor-server/nodes/text/process/uppercase/` 下创建两个文件：`node.toml` 和 `impl.py`。最终的目录结构如下：

```
mini-services/node-editor-server/
└── nodes/
    └── text/
        └── process/
            └── uppercase/
                ├── node.toml   ← 节点定义（端口、属性、显示名）
                └── impl.py     ← 计算逻辑（ComputeLogic 子类）
```

### 2.2 编写 node.toml

`node.toml` 是节点的声明式描述文件。它告诉后端这个节点叫什么、有哪些输入输出端口、有哪些用户可配置的属性。下面是 String Uppercase 节点的完整定义：

```toml
name = "text.process.uppercase"
version = "1.0.0"
display_name = "Uppercase"
description = "Convert input string to uppercase."
order = 10
gui = ["reactflow", "headless"]

[[ports]]
name = "text"
display_name = "Text"
data_type = "string"
direction = "in"

[[ports]]
name = "result"
display_name = "Result"
data_type = "string"
direction = "out"
```

TOML 文件中的 `name` 字段是节点的全局唯一标识（`definition_id`），采用点号分隔的层级命名约定——前缀决定节点在面板中所属的分类树，最后一段是节点名。`version` 字段允许同一节点存在多个版本，UI 会自动选择最新版本。`display_name` 是节点在面板和画布上显示的友好名称。`order` 决定同一分类下节点的排序，数字越小越靠前。

### 2.3 编写 impl.py

`impl.py` 必须定义一个 `ComputeLogic` 子类并实现 `compute()` 方法。后端通过反射查找 `ComputeLogic` 的子类——你可以给类起任何名字，只要它继承自 `ComputeLogic` 即可。下面是 String Uppercase 节点的完整实现：

```python
from typing import Any, Dict
from node_editor.node_def import ComputeLogic

class UppercaseLogic(ComputeLogic):
    """Convert the input string to uppercase."""

    def compute(
        self,
        inputs: Dict[str, Any],
        properties: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        text = inputs.get("text")
        if text is None:
            return {"result": None}
        return {"result": str(text).upper()}
```

注意第 2 行的 import：`from node_editor.node_def import ComputeLogic`。这里 `node_editor` 是后端提供的 shim 包，它把真正的 `ComputeLogic` 类从 `node_def` 模块重导出为 `node_editor.node_def.ComputeLogic`。这样无论节点的 `impl.py` 放在哪个目录、被哪个模块加载，都能用同样的 import 语句——你不需要关心 `sys.path` 的设置。

### 2.4 重启后端验证

保存两个文件后重启后端。重启时观察终端输出，应该看到类似下面的发现日志，确认节点已被识别：

```
Discovering nodes in: .../mini-services/node-editor-server/nodes
→ 78 with compute, 2 stubbed, 0 failed (80 total)
Discovery complete: 78 nodes loaded with compute, 2 stubbed, 0 failed entirely.
```

打开浏览器访问 `http://localhost:3000`，在左侧节点面板的 **Text › Process** 分类下应该能看到 **Uppercase** 节点。把它拖到画布上，连接一个 **Text Input** 节点的输出到它的 `text` 输入，再连接它的 `result` 输出到一个 **Text Display** 节点。点击顶栏的 **Step Frame** 按钮，**Text Display** 节点应该显示大写后的文本。如果一切正常，恭喜你——你已经成功创建了第一个节点。

---

## 3. node.toml 完整 Schema 参考

`node.toml` 是节点的「接口契约」——它定义了节点的所有静态信息：标识、版本、显示名、端口、属性、动态端口配置。后端在启动时解析这些 TOML 文件，前端通过 `GET /api/nodes` 接口获取节点列表并用这些信息渲染节点面板和画布。本节给出每个字段的完整说明。

### 3.1 顶层字段

| 字段               | 类型 / 默认值                          | 说明                                                         |
| ------------------ | -------------------------------------- | ------------------------------------------------------------ |
| `name`             | string (必填)                          | 节点的全局唯一 `definition_id`。点号分隔的层级命名，前缀决定分类树。例如 `"image.filter.blur"` 会在面板的 **Image › Filter** 分类下显示。 |
| `version`          | string 默认 `"1.0.0"`                  | 语义化版本号。允许同一节点存在多个版本，UI 默认选择最新版本。 |
| `display_name`     | string 默认 = `name`                   | 在节点面板和画布上显示的友好名称。可以是任意 UTF-8 文本。    |
| `description`      | string 默认 `""`                       | 节点的简短描述，显示在面板悬停提示中。建议一句话说明节点的功能。 |
| `order`            | int 默认 `100`                         | 同一分类下节点的排序权重，数字越小越靠前。                   |
| `gui`              | array 默认 `["reactflow", "headless"]` | 节点在哪些 GUI 模式下可见。一般不需要修改。                  |
| `measure_time`     | bool 默认 `true`                       | 是否在执行结果中记录该节点的执行耗时。对慢节点（如 LLM 调用）设为 `true` 以便监控。 |
| `is_source_node`   | bool 可选                              | 显式声明是否为源节点。默认根据是否有输入端口自动推断。MQTT Subscriber 这类无输入但产生数据的节点需要显式设为 `true`。 |
| `run_when_stopped` | bool 默认 `false`                      | 在 UI 处于 Stop 状态时是否仍可执行。源节点（如 Image Loader）通常设为 `true`。 |

### 3.2 端口定义 `[[ports]]`

每个 `[[ports]]` 表定义一个输入或输出端口。端口是节点与其他节点交换数据的唯一通道——输入端口接收上游数据，输出端口向下游推送计算结果。端口的 `data_type` 决定了它能在 UI 上与哪些其他端口连线（类型兼容规则见后文）。

| 字段           | 类型 / 默认值       | 说明                                                         |
| -------------- | ------------------- | ------------------------------------------------------------ |
| `name`         | string (必填)       | 端口的内部名称。用于在 `impl.py` 中通过 `inputs[name]` / `outputs[name]` 访问。动态端口的 `name` 由引擎按模板自动生成。 |
| `display_name` | string 可选         | UI 上显示的端口名。不填则使用 `name`。                       |
| `data_type`    | string 默认 `"any"` | 端口的数据类型。常见取值：`float`, `int`, `string`, `bool`, `image`, `audio`, `any`。可以是任意字符串——详见「自定义数据类型」一节。 |
| `direction`    | string (必填)       | 端口方向：`"in"` 输入 / `"out"` 输出 / `"inout"` 双向（极少用）。 |
| `preview`      | bool 默认 `true`    | 是否在 UI 上为该端口显示预览框。对 `image` 类型端口很有用；对纯数值端口通常设为 `false` 减少视觉噪音。 |

### 3.3 属性定义 `[[properties]]`

属性是用户可在右侧配置面板中调整的参数。每个属性的类型、默认值、UI 控件都由 `[[properties]]` 表描述。属性值通过 `properties` 字典传入 `compute()` 方法。

| 字段                   | 类型 / 默认值         | 说明                                                         |
| ---------------------- | --------------------- | ------------------------------------------------------------ |
| `name`                 | string (必填)         | 属性的内部名称。在 `compute()` 中通过 `properties[name]` 访问。 |
| `display_name`         | string 默认 = `name`  | UI 上显示的属性名。                                          |
| `type`                 | string 默认 `"float"` | 属性的数据类型：`int` / `float` / `string` / `bool` / `color`。 |
| `default`              | any 可选              | 默认值。节点创建时 `properties` 字典会被填充为所有属性的默认值。 |
| `widget`               | string 默认 `"input"` | UI 控件类型。详见下方的 widget 一览表。                      |
| `min` / `max` / `step` | number 可选           | 数值类型的范围与步长。UI 会据此做范围校验。                  |
| `options`              | array 可选            | `dropdown` 控件的候选选项。每项是 `{label, value}` 对象。    |
| `accept`               | string 可选           | `file_picker` 控件的文件类型过滤，如 `".jpg,.png"`。         |
| `visible_when`         | table 可选            | 条件显示。`{property: "xxx", values: ["a", "b"]}` 表示仅当 `xxx` 属性为 `a` 或 `b` 时显示本属性。 |
| `placeholder`          | string 可选           | `text_input` 控件的占位符文本。                              |
| `rows`                 | int 可选              | `text_area` 控件的显示行数。                                 |

**支持的 widget 取值一览：**

- `input` — 单行文本输入框（默认）
- `number_input` — 数字输入框（带 min/max 校验）
- `dropdown` — 下拉选择框（需配合 `options`）
- `checkbox` — 布尔复选框
- `color` — 颜色选择器（返回 `#RRGGBB` 字符串）
- `slider` — 滑块（需配合 `min`/`max`/`step`）
- `button` — 按钮（点击时通过特殊机制触发 `compute`）
- `file_picker` — 文件选择器（上传到后端，返回绝对路径）
- `text_input` — 短文本输入（与 `input` 类似，但带 `placeholder` 支持）
- `text_area` — 多行文本域（需配合 `rows`）

### 3.4 动态端口 `[dynamic_ports]`

动态端口是节点编辑器最强大的特性之一：端口可以在运行时被创建和删除，而不是固定的。典型的应用场景是「多输入求和」节点——用户可以连接 2 个、5 个或 10 个输入，节点自动适应。动态端口的配置通过 `[dynamic_ports.inputs]` 或 `[dynamic_ports.outputs]` 子表声明。

| 字段          | 类型 / 默认值       | 说明                                                         |
| ------------- | ------------------- | ------------------------------------------------------------ |
| `group_name`  | string 可选         | 动态端口组的内部标识。默认由 `prefix` 和方向自动生成。       |
| `prefix`      | string 必填         | 端口名模板前缀。第 N 个动态端口的 `name` 为 `"prefix N"`。例如 `prefix="Value"` 会生成 `Value 1`, `Value 2`, ... |
| `data_type`   | string 默认 `"any"` | 动态端口的数据类型。                                         |
| `direction`   | string 默认 `"in"`  | 动态端口方向（在 `[dynamic_ports.inputs]` 下默认 `in`，在 `[dynamic_ports.outputs]` 下默认 `out`）。 |
| `min_count`   | int 默认 `1`        | 节点创建时预生成的最少动态端口数。                           |
| `max_count`   | int 默认 `16`       | 动态端口的上限。超过则拒绝继续添加。                         |
| `auto_expand` | bool 默认 `true`    | 当最后一个动态端口被连接时，是否自动创建下一个。             |
| `preview`     | bool 默认 `true`    | 动态端口是否显示预览框。                                     |

下面是一个动态求和节点的完整 `node.toml` 示例：

```toml
name = "math.dynamic_sum"
version = "1.0.0"
display_name = "Dynamic Sum"
description = "Sums every connected dynamic Value N input."
order = 30

[[ports]]
name = "result"
display_name = "Result"
data_type = "float"
direction = "out"

[dynamic_ports.inputs]
prefix = "Value"
data_type = "float"
min_count = 2
max_count = 8
auto_expand = true
```

---

## 4. impl.py 编写要求

`impl.py` 是节点的「大脑」——所有计算逻辑都在这里实现。它必须定义一个 `ComputeLogic` 子类并实现 `compute()` 方法。后端通过反射查找 `ComputeLogic` 的子类，因此类名可以自由选择，但建议遵循 `XxxLogic` 命名约定。

### 4.1 ComputeLogic 基类 API

`ComputeLogic` 是所有节点逻辑的基类，定义在 `node_editor/node_def.py` 中（通过 shim 包导出为 `node_editor.node_def.ComputeLogic`）。它提供了 `compute()` 抽象方法、`cancel`/`reset` 钩子，以及一个全局模型缓存助手。

```python
from node_editor.node_def import ComputeLogic

class MyNodeLogic(ComputeLogic):
    def compute(self, inputs, properties, context=None):
        # inputs: dict[str, Any] 端口名 -> 值
        # properties: dict[str, Any] 属性名 -> 值
        # context: dict[str, Any] | None 执行上下文（含 node_id 等）
        return {"output_port_name": result_value}
```

`compute()` 接收三个参数：`inputs` 是当前帧从上游端口收集到的输入值，键为端口 `name`；`properties` 是节点的当前属性值，键为属性 `name`；`context` 是执行上下文，目前主要包含 `node_id` 字段。返回值是一个字典，键为输出端口的 `name`，值为要推送给下游（以及 UI 预览）的输出值。

### 4.2 输入值的类型约定

不同 `data_type` 的输入值在 `compute()` 中以不同的 Python 类型出现。理解这些类型约定对于写出正确的节点至关重要。下表列出了每种内置 `data_type` 对应的 Python 类型。

| `data_type` | Python 类型                    | 说明                                                         |
| ----------- | ------------------------------ | ------------------------------------------------------------ |
| `float`     | Python `float`                 | 纯浮点数。如果上游是 `int`，引擎会自动转换为 `float`。       |
| `int`       | Python `int`                   | 整数。如果上游是 `float`，引擎会通过 `int()` 截断。          |
| `string`    | Python `str`                   | 字符串。                                                     |
| `bool`      | Python `bool`                  | 布尔值。                                                     |
| `image`     | `numpy.ndarray` (BGR, `uint8`) | OpenCV 约定的 BGR 顺序 numpy 数组。如果上游发送 base64 字符串，引擎会自动解码为 numpy 数组。 |
| `audio`     | `dict`                         | 音频字典，包含 `sample_rate`、`duration`、`delta`、`waveform` 等字段。 |
| `any`       | 任意                           | 无类型约束。用于通用节点（如条件门、动态路由）。             |

### 4.3 输出值的序列化

`compute()` 返回的输出值在内部以原始 Python 对象存储（numpy 数组保持为 numpy 数组），但在通过 API 返回给 UI 时会被自动序列化。`image` 类型的输出会被转换为 base64 JPEG data URI。其他类型（`float`, `int`, `string`, `bool`, `dict`, `list`）直接 JSON 序列化。如果你的节点返回自定义对象，需要先转换为这些可序列化类型。

如果节点执行出错，可以在返回字典中加入一个特殊的 `__error__` 键，值为错误消息字符串。引擎会把这条消息记录到当前帧的 `errors` 字典中，并在 UI 上以红色高亮显示该节点。例如：

```python
def compute(self, inputs, properties, context=None):
    file_path = properties.get("file_path", "")
    if not file_path:
        return {"image_out": None, "__error__": "file_path is required"}
    # ... 正常处理 ...
    return {"image_out": image_array}
```

类似地，`__display_text__` 键可以用来设置节点在画布上显示的简短状态文本（如 `"conf=0.92"`），`__frame_count__` 键用于源节点声明本帧产生了几帧数据。

### 4.4 状态保持与 reset()

`ComputeLogic` 子类的实例在节点创建时被实例化一次，之后在整个图的生命周期内复用——每一帧调用的都是同一个实例的 `compute()` 方法。这意味着你可以在 `__init__` 中初始化实例属性来跨帧保持状态。典型的用例包括：缓存上一帧的结果、维护环形缓冲区、跟踪序列号等。

但跨帧状态也带来了风险：当图被重新加载时，旧的状态可能不再适用于新的图结构。为此 `ComputeLogic` 提供了 `reset()` 钩子——重写它可以在图重置时清理状态。引擎在调用 `reset_frame_state()` 时会遍历所有节点的 `ComputeLogic` 实例并调用 `reset()`。

```python
class BufferLogic(ComputeLogic):
    def __init__(self):
        self._buffer = []  # 跨帧保持的缓冲区

    def reset(self):
        """图重置时清空缓冲区。"""
        self._buffer.clear()

    def compute(self, inputs, properties, context=None):
        value = inputs.get("value")
        if value is not None:
            self._buffer.append(value)
            if len(self._buffer) > 100:  # 限制缓冲区大小
                self._buffer.pop(0)
        return {"count": len(self._buffer), "latest": value}
```

---

## 5. 接入全局模型缓存

图中多个节点经常需要加载同一个大型模型（CLIP、LLM、ONNX 推理会话、MediaPipe 模型等）。如果每个节点各自加载一份，内存占用会成倍增长，很快就会 OOM。为解决这个问题，节点编辑器提供了一个进程级的全局模型注册表（`ModelRegistry`），通过 `_get_cached_model()` 助手方法暴露给所有 `ComputeLogic` 子类。

注册表的核心保证是：对于同一个 `key`，`loader` 函数只会被调用一次，之后所有请求这个 `key` 的节点都会拿到同一个实例。注册表内部使用 per-key 锁来保证线程安全，错误也会被缓存（加载失败后不会每帧重试），并且有 LRU 淘汰机制（默认超过 16 个条目或 8 GB 总大小时淘汰最久未用的模型）。

### 5.1 改造现有节点

把节点中的模型加载代码迁移到全局缓存只需改 3 行代码。下面是一个典型的「改造前 vs 改造后」对比：

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

`key` 的设计很重要——它必须包含所有影响模型实例的因素。例如一个 ONNX 模型，如果你支持 GPU 和 CPU 两种 provider，`key` 就应该包含 provider 信息，否则同一模型在 GPU 和 CPU 之间切换时会拿到错误的实例。`label` 用于在 UI 的模型面板中显示，建议使用简短的类型标识（如 `"onnx"`、`"clip"`、`"llm"`）。

`est_bytes` 参数是可选的，用于估算模型占用的内存字节数。注册表会用这个值来决定何时触发 LRU 淘汰。如果你不知道准确的字节数，传 `0` 也可以——注册表会按条目数（默认 16）淘汰。`_get_cached_model` 返回一个元组 `(model, error)`：成功时 `error` 为 `None`，失败时 `model` 为 `None` 且 `error` 是错误消息字符串。

---

## 6. 添加自定义数据类型

节点编辑器中的 `data_type` 是纯字符串，没有中央注册表。这意味着你可以直接在 `node.toml` 中使用任何字符串作为 `data_type`，无需注册。引擎的类型兼容规则只对内置类型有特殊处理（`int` ↔ `float` 自动转换、`any` 兼容所有），其他自定义类型之间默认只与自身兼容。

### 6.1 三步添加新类型

添加一个全新的数据类型通常需要三步：在 `node.toml` 中使用新类型名、（可选）在前端 `types.ts` 中给新类型指定 UI 颜色、（可选）在后端 `core.py` 中扩展类型兼容规则。如果新类型需要特殊的序列化逻辑，还需要在 `core.py` 中添加转换代码。

**第 1 步**：在 `node.toml` 中使用新类型名。

```toml
# 例：自定义 point_cloud 类型
[[ports]]
name = "cloud"
data_type = "point_cloud"
direction = "out"
```

**第 2 步**：（可选）在前端 `src/lib/node-editor/types.ts` 中给新类型指定 UI 颜色。

```typescript
export const TYPE_COLORS: Record<string, string> = {
  float: "#10b981",
  int: "#22c55e",
  // ... 现有类型 ...
  point_cloud: "#8b5cf6",  // 新增
};
```

**第 3 步**：（可选）让新类型与现有类型兼容。编辑 `core.py` 的 `_types_compatible()` 函数：

```python
def _types_compatible(src_type, dst_type) -> bool:
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

**第 4 步**：（仅当新类型不能直接 JSON 序列化时）在 `core.py` 的 `_serialize_output()` 和 `_decode_image_input()` 中添加转换逻辑。参考 `image` 类型的处理：numpy 数组在内部传递，但通过 API 返回时被转换为 base64 JPEG data URI。如果你的新类型也是 numpy 数组或其他不可 JSON 序列化的对象，需要类似的处理。

---

## 7. 完整实战示例：图像阈值化节点

本节通过一个完整的实战示例——**图像阈值化节点（Image Threshold）**——把前面所有概念串联起来。这个节点接收一张图像、一个阈值参数、一个阈值模式（二值化/反二值化/截断/超阈值置零），输出处理后的图像。它演示了：`image` 类型输入输出、属性下拉框、错误处理、OpenCV 调用。

### 7.1 node.toml

```toml
name = "image.filter.threshold"
version = "1.0.0"
display_name = "Threshold"
description = "Apply fixed-level thresholding to a grayscale image."
order = 60

[[ports]]
name = "image"
display_name = "Image"
data_type = "image"
direction = "in"

[[ports]]
name = "image_out"
display_name = "Image"
data_type = "image"
direction = "out"

[[properties]]
name = "threshold"
display_name = "Threshold"
type = "int"
default = 128
widget = "slider"
min = 0
max = 255
step = 1

[[properties]]
name = "max_value"
display_name = "Max Value"
type = "int"
default = 255
widget = "number_input"
min = 0
max = 255

[[properties]]
name = "threshold_type"
display_name = "Type"
type = "string"
default = "binary"
widget = "dropdown"
options = [
  { label = "Binary", value = "binary" },
  { label = "Binary Inverted", value = "binary_inv" },
  { label = "Trunc", value = "trunc" },
  { label = "To Zero", value = "tozero" },
  { label = "To Zero Inverted", value = "tozero_inv" },
]
```

### 7.2 impl.py

```python
from typing import Any, Dict
from node_editor.node_def import ComputeLogic

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    cv2 = None
    np = None

# OpenCV 阈值类型常量映射
_THRESH_TYPE_MAP = {
    "binary": 0,        # cv2.THRESH_BINARY
    "binary_inv": 1,    # cv2.THRESH_BINARY_INV
    "trunc": 2,         # cv2.THRESH_TRUNC
    "tozero": 3,        # cv2.THRESH_TOZERO
    "tozero_inv": 4,    # cv2.THRESH_TOZERO_INV
}

class ThresholdLogic(ComputeLogic):
    """Apply fixed-level thresholding to a grayscale image."""

    def compute(
        self,
        inputs: Dict[str, Any],
        properties: Dict[str, Any],
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        if not CV2_AVAILABLE:
            return {
                "image_out": None,
                "__error__": "opencv-python is not installed",
            }

        image = inputs.get("image")
        if image is None:
            return {"image_out": None}

        threshold = int(properties.get("threshold", 128))
        max_value = int(properties.get("max_value", 255))
        type_name = str(properties.get("threshold_type", "binary"))
        thresh_type = _THRESH_TYPE_MAP.get(type_name, 0)

        try:
            # 输入是 BGR numpy 数组；先转灰度
            if len(image.shape) == 3:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            else:
                gray = image

            _, result = cv2.threshold(gray, threshold, max_value, thresh_type)

            # 把单通道结果转回 3 通道 BGR，方便下游节点统一处理
            result_bgr = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
            return {"image_out": result_bgr}

        except Exception as e:
            return {
                "image_out": None,
                "__error__": f"Threshold failed: {e}",
            }
```

### 7.3 关键设计决策解读

这个示例有几个值得学习的设计决策。首先，`try/except` 包裹了 opencv 的导入——这样即使目标环境没安装 opencv，节点也能以 stub 形式注册（带 N/A 徽章），而不是导致整个后端启动失败。其次，`compute()` 方法对每个可能的失败点都做了防御性检查：依赖缺失、输入为空、阈值计算异常都有对应的错误返回。最后，输出统一为 3 通道 BGR 数组——这是一个良好的约定，让下游节点不需要分别处理单通道和多通道图像。

---

## 8. 调试与常见问题

### 8.1 节点不出现在面板中

这是新手最常遇到的问题。请按以下顺序排查：

- 确认 `node.toml` 在正确路径下：`mini-services/node-editor-server/nodes/<分类>/<节点名>/node.toml`。注意分类可以是多层嵌套的，例如 `image/filter/blur/blur/node.toml`。
- 确认 `node.toml` 中有 `name` 字段且值唯一。`name` 是节点的全局标识，重复会导致后注册的覆盖先注册的。
- 查看后端启动日志，搜索 `"Discovering nodes in"`。如果节点所在的目录不在搜索路径中，日志会显示哪些路径被扫描了。
- 如果日志中有 `"[error] failed to parse <file>:"` 行，说明 `node.toml` 有语法错误。TOML 对引号、缩进、表头格式很敏感，建议用 TOML linter 检查。

### 8.2 节点显示 N/A 徽章

节点出现在面板中但带 N/A 徽章，意味着 `impl.py` 加载失败——通常是缺少第三方依赖。查看后端启动日志中的 `[stub]` 警告行，会显示具体的失败原因。常见原因包括：

- 缺少 opencv-python：`pip install opencv-python`
- 缺少 onnxruntime：`pip install onnxruntime`
- 缺少 mediapipe：`pip install mediapipe`
- 缺少 paho-mqtt（MQTT 节点）：`pip install paho-mqtt`

安装缺失的依赖后重启后端，节点应该会从 stub 变为可用。注意：即使节点是 stub 状态，它仍然会出现在面板中并可以添加到画布——只是执行时会返回 `__error__`。这是为了让用户在缺少可选依赖时仍能浏览所有节点。

### 8.3 节点执行结果不对

如果节点能正常执行但输出结果不符合预期，按以下顺序排查：

- 在 `compute()` 方法开头加 `print(inputs, properties)` 临时调试。后端终端会打印每一帧的输入和属性。完成后记得删除。
- 检查输入端口的数据类型是否符合预期。`image` 类型的输入是 BGR numpy 数组（不是 RGB！），这是 OpenCV 的约定。如果你按 RGB 处理颜色会反。
- 检查 `properties` 中数值的范围。UI 会做范围校验，但通过 API 直接设置属性可能绕过校验。建议在 `compute()` 中再次 clamp。
- 如果节点有时执行有时不执行，检查 `trigger_mode`。ALL 模式下，节点只在所有已连接输入都有当前帧数据时才执行；如果上游节点的 `frame-sync` 超时了，本节点也不会执行。

### 8.4 添加 print 调试的最佳实践

由于后端是长驻进程，`print` 输出会出现在启动后端的终端窗口中。建议给每条调试日志加节点标识前缀，方便过滤。例如：

```python
def compute(self, inputs, properties, context=None):
    node_id = (context or {}).get("node_id", "?")
    print(f"[my_node {node_id}] inputs={list(inputs.keys())}")
    # ... 处理逻辑 ...
```

调试完成后务必删除 `print` 语句——节点可能每秒执行几十次，过多的日志会拖慢整个图并淹没真正重要的信息。

---

## 9. 节点发现机制原理（选读）

理解节点发现机制有助于你在出问题时快速定位。本节是选读内容，对日常开发不是必需的。

节点发现由 `mini-services/node-editor-server/discovery.py` 实现。它在模块导入时自动执行（文件末尾有 `discover_all_nodes()` 调用），扫描两个搜索路径：本地 `nodes/` 目录（用户放置节点的地方）和原始克隆仓库的 `src/nodes/` 目录（沙箱环境用）。两个路径按优先级顺序扫描，后注册的同 `definition_id` 节点会覆盖先注册的——这意味着你可以把原始节点复制到本地 `nodes/` 目录下修改，重启后你的修改版本会生效。

对每个 `node.toml` 文件，发现模块会：解析 TOML、提取端口和属性定义、解析动态端口配置、尝试加载同目录下的 `impl.py`。`impl.py` 的加载使用 `importlib.util.spec_from_file_location` 动态创建模块，通过 `inspect.getmembers` 查找 `ComputeLogic` 的子类，然后实例化它。如果加载过程抛出任何异常（`ImportError`、`SyntaxError`、实例化错误等），节点会以 stub 形式注册——使用一个返回错误消息的 `StubCompute` 实例作为 `compute_logic`。

分类树由目录结构自动推导。每个包含 `category.toml` 的目录会成为一个分类节点，`category.toml` 中的 `display_name`、`order`、`default_open` 字段控制分类在面板中的显示。没有 `category.toml` 的目录也会成为分类（使用目录名作为 `display_name`），但无法自定义顺序和默认展开状态。`definition_id` 的点号分隔前缀必须与目录路径对应——例如 `"image.filter.blur"` 的 `node.toml` 必须在 `image/filter/blur/` 目录下。

---

## 10. API 速查表

本节列出节点开发中最常用的 API，方便快速查阅。所有 API 都通过 `ComputeLogic` 子类访问。

### 10.1 ComputeLogic 方法

| 方法                                                     | 说明                                                         |
| -------------------------------------------------------- | ------------------------------------------------------------ |
| `compute(inputs, properties, context)`                   | **必须重写**。节点的核心计算逻辑。返回 `{port_name: value}` 字典。 |
| `reset()`                                                | 可选重写。图重置时调用。用于清理跨帧状态。默认实现为空。     |
| `request_cancel()` / `is_cancelled()` / `clear_cancel()` | 可选。取消支持。用于长任务节点响应 UI 的取消请求。           |
| `_get_cached_model(key, loader, *, est_bytes, label)`    | 建议使用。从全局模型注册表获取共享模型实例。返回 `(model, error)` 元组。 |

### 10.2 compute() 返回值特殊键

| 键                 | 类型             | 说明                                                         |
| ------------------ | ---------------- | ------------------------------------------------------------ |
| `<port_name>`      | 对应输出端口的值 | 正常输出。引擎会序列化后推送给下游并更新 UI 预览。           |
| `__error__`        | `string`         | 错误消息。引擎会记录到 `errors` 字典并在 UI 上以红色高亮显示。 |
| `__display_text__` | `string`         | 节点在画布上显示的简短状态文本（如 `"conf=0.92"`）。         |
| `__frame_count__`  | `int`            | 源节点声明本帧产生了几帧数据。用于流式源节点的帧计数。       |

### 10.3 context 字典字段

| 字段      | 类型         | 说明                                                         |
| --------- | ------------ | ------------------------------------------------------------ |
| `node_id` | `string`     | 当前节点的 ID。用于在日志中标识节点。                        |
| `_graph`  | `Graph` 对象 | 对当前图对象的引用。高级用法——允许节点直接访问图拓扑（如查询上游节点）。慎用。 |

