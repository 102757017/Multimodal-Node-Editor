# Python 多模态节点编辑器客户端

一个 Python 客户端库，允许外部 Python 进程将图像推入后端计算图中指定的图像节点，并读取任意节点的输出。

## 同时支持两种后端

Python 客户端**同时支持** GUI 后端和无头后端：

| 后端                                | 启动方式                                      | 最佳客户端传输方式             |
| ----------------------------------- | --------------------------------------------- | ------------------------------ |
| **GUI 后端**（FastAPI，浏览器界面） | `python run_gui.py`                           | `HttpClient`（HTTP，base64）   |
| **无头后端**（无浏览器）            | `python run_headless.py graph.json --server`  | `SharedMemoryClient`（零拷贝） |
| **进程内**（作为库导入）            | `from run_headless import HeadlessController` | `DirectClient`（零开销）       |

**相同的 Python 客户端 API** 适用于以上三种场景 —— 只需选择与后端匹配的传输方式即可。

## 三种传输模式

| 传输方式     | 类                   | 图像传输方式             | 适用场景                                    |
| ------------ | -------------------- | ------------------------ | ------------------------------------------- |
| **直接**     | `DirectClient`       | 进程内（numpy 引用传递） | 外部脚本将后端作为库导入 —— **零开销**      |
| **共享内存** | `SharedMemoryClient` | 共享内存（零拷贝）       | 跨进程，无头后端 —— **无 base64，无序列化** |
| **HTTP**     | `HttpClient`         | Base64 编码              | **GUI 后端**（浏览器打开时）或跨机器        |

### 我应该使用哪种传输方式？

- **GUI 后端正在运行**（你想在浏览器中查看图，同时用脚本喂图）：使用 `HttpClient` → `http://localhost:3030`
- **无头后端**（生产环境，无浏览器）：使用 `SharedMemoryClient` → `/tmp/mne_headless.sock`（零拷贝，最高效）
- **同一进程**（插件、Jupyter、脚本）：使用 `DirectClient`（零开销）
- **跨机器**：使用 `HttpClient`（唯一支持跨机器的方案）

## 安装

同步客户端仅使用 Python 标准库。如需异步客户端，请安装 `aiohttp`：

```bash
pip install aiohttp  # 仅用于 AsyncMultimodalClient
```

图像处理需要安装 `opencv-python` 和 `numpy`：

```bash
pip install opencv-python numpy
```

## 快速开始 —— DirectClient（进程内，最高效）

```python
import sys
sys.path.insert(0, "/path/to/mini-services/node-editor-server")

from multimodal_client import DirectClient
import cv2

# 创建进程内客户端 —— 直接加载图
client = DirectClient(
    backend_dir="/path/to/mini-services/node-editor-server",
    graph_path="my_graph.json",
)

# 发现图拓扑
info = client.graph_info()
img_node = info.find_node_by_name("Image")

# 推送原始 numpy 数组 —— 零拷贝，无 base64！
image = cv2.imread("photo.jpg")
result = client.run(
    image_node_id=img_node.id,
    image_array=image,          # <-- 原始 numpy，引用传递
    output_node_id=img_node.id,
    output_port_name="image_out",
)

# result.output 是原始 numpy 数组 —— 无需 base64 解码
cv2.imwrite("output.jpg", result.output)
```

## 快速开始 —— SharedMemoryClient（跨进程，零拷贝）

```bash
# 终端 1：启动无头服务器
python run_headless.py my_graph.json --server
# 输出：Shared-memory server listening on: /tmp/mne_headless.sock
```

```python
# 终端 2：从另一个进程连接
from multimodal_client import SharedMemoryClient
import cv2

client = SharedMemoryClient("/tmp/mne_headless.sock")

info = client.graph_info()
img_node = info.find_node_by_name("Image")

# 推送原始 numpy 数组 —— 通过共享内存传输（零拷贝）
image = cv2.imread("photo.jpg")
result = client.run(
    image_node_id=img_node.id,
    image_array=image,          # <-- 通过共享内存零拷贝传输
    output_node_id=img_node.id,
    output_port_name="image_out",
)

# result.output 是原始 numpy 数组
cv2.imwrite("output.jpg", result.output)
```

## 快速开始 —— HttpClient（GUI 后端，HTTP）

当 GUI 后端运行时（`python run_gui.py`）使用此方式，你可以在浏览器中查看图的同时通过脚本喂图。

```bash
# 终端 1：启动 GUI 后端
python run_gui.py
# → FastAPI 运行在 http://localhost:3030，浏览器打开 http://localhost:3000
```

```python
# 终端 2：通过脚本喂图
from multimodal_client import HttpClient
import cv2

client = HttpClient("http://localhost:3030")

# 检查连接的是哪个后端
print(client.ping())  # {'ok': True, 'mode': 'gui-http'}

info = client.graph_info()
img_node = info.find_node_by_name("Image")

# 推送图像 —— 通过 HTTP 进行 base64 编码
image = cv2.imread("photo.jpg")
result = client.run(
    image_node_id=img_node.id,
    image_array=image,          # <-- 内部编码为 base64
    output_node_id=img_node.id,
    output_port_name="image_out",
)

# result.output 是 base64 data URI（使用 result.decode_image() 解码）
output = result.decode_image()  # numpy 数组
cv2.imwrite("output.jpg", output)
```

你也可以通过 GUI（浏览器）构建/加载图，然后从脚本驱动它 —— 脚本和浏览器共享同一个后端图。

## 异步用法

三种客户端均支持异步提交：

```python
from multimodal_client import SharedMemoryClient

client = SharedMemoryClient("/tmp/mne_headless.sock")

# 提交（立即返回任务 ID）
task_id = client.submit(
    image_node_id="node-abc12345",
    image_array=cv2.imread("image1.jpg"),
    output_node_id="node-def67890",
    output_port_name="result",
)

# ... 执行其他工作 ...

# 等待结果（阻塞直至完成或超时）
result = client.wait_for_result(task_id, timeout=60.0)
print(result)
```

## 命令行工具

```bash
# 健康检查 + 检测后端模式（GUI vs 无头）
python -m multimodal_client ping --transport http --base-url http://localhost:3030
# → Backend: reachable
#   mode: gui-http
#   url: http://localhost:3030

python -m multimodal_client ping --transport shm --address /tmp/mne_headless.sock
# → Backend: reachable
#   mode: headless-shm
#   address: /tmp/mne_headless.sock

# 显示图信息（GUI 后端，HTTP）
python -m multimodal_client info --transport http --base-url http://localhost:3030

# 同步运行（GUI 后端，HTTP）
python -m multimodal_client run \
    --transport http \
    --base-url http://localhost:3030 \
    --image-node node-abc12345 \
    --image /path/to/image.jpg \
    --output-node node-def67890 \
    --output-port result \
    --save output.jpg

# 同步运行（无头后端，共享内存 —— 最高效）
python -m multimodal_client run \
    --transport shm \
    --address /tmp/mne_headless.sock \
    --image-node node-abc12345 \
    --image /path/to/image.jpg \
    --output-node node-def67890 \
    --output-port result \
    --save output.jpg

# 进程内模式（零开销）
python -m multimodal_client run \
    --transport direct \
    --backend-dir /path/to/mini-services/node-editor-server \
    --graph my_graph.json \
    --image-node node-abc12345 \
    --image /path/to/image.jpg \
    --output-node node-def67890 \
    --output-port result \
    --save output.jpg
```

## API 参考

三种客户端共享相同的 API：

- `graph_info() -> GraphInfo` —— 列出节点和端口
- `find_node_by_name(name) -> NodeInfo` —— 便捷查找
- `ping() -> dict` —— 健康检查；返回 `{"ok": true, "mode": "gui-http"}` 或 `{"mode": "headless-shm"}`
- `run(*, image_node_id, output_node_id, output_port_name, image_array=..., image_path=..., ...) -> RunResult` —— 同步运行
- `submit(...) -> str` —— 异步提交，返回任务 ID
- `get_result(task_id) -> TaskStatus` —— 单次轮询
- `wait_for_result(task_id, *, timeout=120.0) -> RunResult` —— 阻塞直至完成
- `cancel_task(task_id) -> bool`
- `list_tasks() -> list[dict]`

### RunResult

- `.status` —— `"frame_complete"`、`"idle"` 或 `"exhausted"`
- `.output` —— 输出值（DirectClient/SharedMemoryClient 为原始 numpy 数组，HttpClient 为 base64 data URI）
- `.is_image` —— 如果输出是图像则为 True
- `.save_output(path)` —— 保存到磁盘
- `.decode_image()` —— 解码为 numpy 数组（适用于所有传输方式）
- `.errors` —— `{node_id: error_message}`
- `.elapsed_ms` —— 执行耗时（毫秒）

## 架构

```
┌──────────────────────────────────────────────────────────────────────┐
│  外部 Python 进程                                                   │
│                                                                    │
│  ┌─────────────────┐  ┌────────────────────┐  ┌──────────────┐    │
│  │  DirectClient   │  │ SharedMemoryClient │  │  HttpClient  │    │
│  │  (进程内)       │  │ (跨进程)           │  │ (跨机器)     │    │
│  └────────┬────────┘  └──────────┬─────────┘  └──────┬───────┘    │
│           │                      │                    │            │
│           │ numpy 引用传递       │ 共享内存           │ HTTP(base64)│
└───────────┼──────────────────────┼────────────────────┼────────────┘
            │                      │                    │
┌───────────▼──────────────────────▼────────────────────▼────────────┐
│  后端进程                                                          │
│                                                                    │
│  ┌─────────────────┐  ┌────────────────────┐  ┌──────────────┐    │
│  │ HeadlessControl │  │ SharedMemoryServer │  │   FastAPI    │    │
│  │   (直接)        │  │   (--server)       │  │  (main.py)   │    │
│  └─────────────────┘  └────────────────────┘  └──────────────┘    │
│           │                      │                    │            │
│           └──────────────────────┼────────────────────┘            │
│                                  ▼                                 │
│                        ┌─────────────────┐                         │
│                        │   图引擎        │  (core.py)              │
│                        └─────────────────┘                         │
└──────────────────────────────────────────────────────────────────────┘
```