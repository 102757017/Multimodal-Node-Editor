# Multimodal Node Editor — Worklog

---
Task ID: ALL
Agent: Z.ai Code (main)
Task: Clone https://github.com/102757017/Multimodal-Node-Editor.git and build a VisionMaster-style visual node editor with refactored execution engine, dynamic ports, frame sync, cross-level data access, and sharded execution.

Work Log:
- Cloned the Multimodal-Node-Editor repo into `download/multimodal-node-editor/` to study the original architecture (models.py, core.py, node_def.py, main.py, ReactFlow frontend).
- Built a Python FastAPI backend mini-service at `mini-services/node-editor-server/` (port 3030) implementing the full refactored spec:
  - `models.py`: Port.metadata, DynamicPortConfig, Node.trigger_mode, Node.dynamic_port_configs, Node.is_source_node, input_sources stored in properties.
  - `node_def.py`: NodeDefinition with dynamic_port_configs parsing, 7 built-in nodes (source.value, source.color_image, math.add, math.multiply, math.dynamic_sum [dynamic ports], image.dynamic_concat [dynamic ports], display.number).
  - `core.py`: GraphExecutionState + NodeExecutionState (three-state model: frame_complete / idle / exhausted, no single "done" boolean), execute_step() / execute_generator(), start_frame() + set_source_data() frame model, frame-sync with 5s timeout, ALL/ANY trigger modes, _collect_inputs() with connection-first + ComboBox fallback, topological-order filtering for ComboBox candidates (no cycles), true dynamic ports (auto-expand, add/remove with re-indexing, template names, min_count enforcement), single-input constraint.
  - `main.py`: Full REST API — node CRUD, connections (with validation), dynamic port add/remove/rename, properties, trigger-mode, input-source (ComboBox), start-frame, source-data, mark-depleted, execute-step, reset-frame, status, save/load.
- Built the Next.js frontend (only `/` route visible):
  - `src/lib/node-editor/types.ts`: full type system with new fields (trigger_mode, dynamic_port_configs, Port.metadata, ComboBoxCandidate, ExecutionResult with 4 statuses).
  - `src/lib/node-editor/api.ts`: API client using `?XTransformPort=3030` gateway convention.
  - `src/components/node-editor/CustomNode.tsx`: ReactFlow node with category-colored headers, trigger-mode badge, typed port handles, dynamic-port delete-on-hover, output value chips, image preview, error display.
  - `src/components/node-editor/ConfigPanel.tsx`: right sidebar with node name, trigger-mode selector, property fields (number/dropdown/checkbox/color), input-sources ComboBox (disabled when connected, lists topologically-upstream compatible ports), dynamic-port-group management (add button, count display), delete-node.
  - `src/components/node-editor/NodePalette.tsx`: left sidebar with search + categorized node list, port-type dots, dynamic/source badges.
  - `src/app/page.tsx`: main page wiring ReactFlow + palette + config panel + execution controls (Run/Step/Reset/Save/Load) + three-state status badge + compact exec-result strip.
- Fixed a backend bug (variable shadowing in get_node_definition) and refactored the frame model to use explicit start_frame() + set_source_data() so multiple sources contribute to one frame.
- Fixed React Compiler lint errors (callback ordering, positionsRef to avoid rfNodes dependency).
- Made the exec-result strip a compact pill so it doesn't cover canvas nodes.

Stage Summary:
- Backend verified end-to-end via API: 3+4=7 (Add), 10+20+30=60 (Dynamic Sum many-to-1), 3*4=12 (Multiply via ComboBox cross-level access with no direct connection), image concat (2 color sources -> 1 image).
- Frontend verified via Agent Browser through the Caddy gateway (port 81): node palette renders 7 nodes in 4 categories; adding nodes, fitting view, executing "Step Frame" shows FRAME COMPLETE with correct values displayed on nodes (Add=7, Display=7.0000, Sum=60, Mul=12, Concat=image preview 218x109); Run mode shows RUNNING with frame increment and Stop button; trigger-mode change ALL->ANY verified in both UI badge and backend; dynamic-port Add button verified (2/8 -> 3/8); ComboBox candidates correctly list only upstream ports.
- Lint clean (0 errors, 0 warnings). Both services (Next.js :3000, FastAPI :3030) running; Caddy gateway on :81 forwards XTransformPort requests correctly.

---
Task ID: FIX-1
Agent: Z.ai Code (main)
Task: Fix three issues reported by user: (1) original preset nodes not loaded, (2) palette not collapsible/hierarchical, (3) CRITICAL — all connections render on the same port dot instead of each port having its own dot.

Work Log:
- Created `node_editor/` shim package (node_def.py, image_utils.py, settings.py, __init__.py) so original impl.py files (`from node_editor.node_def import ComputeLogic`) import cleanly.
- Wrote `discovery.py` that walks `download/multimodal-node-editor/src/nodes/` and:
  - Parses all 124 node.toml files ([[ports]] and legacy [[inputs]]/[[outputs]] formats)
  - Converts old-style `dynamic_ports = "Prefix"` (string) to new DynamicPortConfig, removing the static matching ports (they get seeded dynamically from min_count)
  - Parses new-style `[dynamic_ports.inputs]` tables when present
  - Builds a hierarchical category tree from category.toml files (display_name, order, default_open)
  - Dynamically imports each impl.py; uses a StubCompute fallback when deps are missing so the node still appears in the palette (marked "N/A")
  - Result: 131 nodes total (7 built-in + 124 original), 122 with real compute, 2 stubbed
- Added cancel/reset methods (is_cancelled, request_cancel, clear_cancel, reset) to ComputeLogic base class — original nodes call self.is_cancelled() during compute.
- Standardized image data format: source nodes produce numpy arrays internally; core.py converts numpy→base64 for API output and base64→numpy for image inputs (original nodes expect numpy). This lets built-in Color Image sources feed original Multi Image Concat.
- Rewrote CustomNode.tsx: each Handle now gets a computed `style.top` offset based on its port index (HEADER_HEIGHT=36, ROW_HEIGHT=22, SEPARATOR=8). Input handle i → top = 36+8+22*i+11. Output handle j → top = 36+8+22*numInputs+8+22*j+11. Each port now has its OWN distinct dot.
- Rewrote NodePalette.tsx as a collapsible tree: categories expand/collapse via chevron toggle, subcategories nest with indentation, default_open from category.toml controls initial state, search auto-expands all, "collapse" button resets. Derived initial expanded state from default_open (no setState-in-effect needed).
- Added `available` flag to NodeDefinition + API response; nodes with missing deps show "N/A" badge and dimmed styling.
- Updated main.py /api/nodes to return hierarchical category tree (CategoryTreeNode with children).
- Updated types.ts (CategoryTreeNode, available field), api.ts (NodeListResponse categories type), page.tsx (categories state + pass to palette).

Stage Summary:
- 131 preset nodes now load on startup (was 7). All 6 top-level categories (IMAGE 62, AUDIO 32, TEXT 9, OPENAI 4, MATH 18, UTILITY 3) render as collapsible accordion items with nested subcategories.
- Port handle bug FIXED: Multi Image Concat now has 4 distinct dots — 3 input dots at top=55px, 77px, 99px + 1 output dot at top=129px. Wires connect to the correct dot.
- Original Multi Image Concat executes end-to-end: 2 Color Image sources (numpy) → MIC (cv2 concat) → 218x109 base64 image rendered in the node.
- Lint clean (0 errors, 0 warnings).

---
Task ID: FIX-2
Agent: Z.ai Code (main)
Task: Fix two issues: (1) port connection dots misaligned (all shifted down, not next to port text), (2) image-type nodes missing preview box until Run is clicked (Draw Mask unusable).

Work Log:
- Root cause of handle misalignment: the previous approach computed a fixed `top` offset (HEADER_HEIGHT + BODY_PT + ROW_HEIGHT*index) and set it on the Handle via `style.top`. This broke because the actual rendered header/body heights didn't match the assumed constants (text line-height, padding, badge height all differed), and the offset became stale when layout changed (e.g. image preview loaded).
- Fix: made each PortRow `position: relative` and positioned its Handle with `top: 50%` + `transform: translate(-50%, -50%)`. Since the Handle's absolute positioning context is now the row itself (not the node root), the dot is always centred on the row regardless of header height, padding, or dynamic layout changes. No measurement/useEffect needed.
- Verified: all handles now have delta=0px from their row centre (was 49-141px off before).
- Image preview fix: previously the preview `<img>` only rendered when `outputValues.image` was truthy (i.e. after execution). Now:
  - `showPreview = hasImageOutput || hasImageInput` — any node with an image port (input OR output) gets a preview box.
  - `ImagePreview` component shows: the first image output value if available, else the first image input value (resolved from connections/ComboBox), else an empty dashed placeholder box with "image preview" text.
  - Added `inputValues` to CustomNodeData, populated in syncFromGraph by resolving each input port's connected/ComboBox upstream output value from the execution results.
  - Draw Mask (image input only, no image output) now shows a preview box at all times — empty placeholder before run, connected upstream image after run.

Stage Summary:
- Port dots perfectly aligned: all handles delta=0px from their port text row (verified on Red, Multi Image Concat with 3 handles, Draw Mask with 2 handles).
- Image preview boxes always visible: Red (1 img), Multi Image Concat (1 img), Draw Mask (1 img) all show previews. Before execution, dashed placeholder; after, actual image.
- Draw Mask is now usable — its preview box is visible on canvas immediately after drag-in.
- Lint clean (0 errors, 0 warnings).

---
Task ID: FIX-3
Agent: Z.ai Code (main)
Task: Fix two issues: (1) output port handle still covering text (input fixed previously but output not), (2) Draw Mask preview should allow drawing mask with left-mouse-drag, but currently drags the node instead.

Work Log:
- Root cause of output handle overlap: ReactFlow's `.react-flow__handle-right` CSS uses `right: 0; transform: translate(50%, -50%)`. My previous fix set `right: -12px` but kept `transform: translate(-50%, -50%)` which moved the dot left (back over the text) instead of right (to the node border). Fixed by using the matching `translate(50%, -50%)` for right handles so the dot centres exactly on the node border.
- Verified: all handles now overlap=false (Red: right handle OK; Multi Image Concat: 2 left + 1 right OK; Draw Mask: 1 left + 1 right OK).
- Draw Mask drawing fix: the image preview `<img>` was a passive element that let ReactFlow's node-drag handler capture mousedown. Fix:
  - Added a `<canvas>` overlay on top of the image for draw-type nodes (image.draw.mask, image.draw.canvas) with the `nodrag` class on the container (ReactFlow convention: elements with .nodrag don't trigger node dragging).
  - Canvas captures onMouseDown/onMouseMove/onMouseUp, calls preventDefault+stopPropagation, and accumulates stroke points into a `draw_commands` array persisted to the backend via api.setProperties.
  - Added a DrawToolbar (brush/eraser/clear buttons) overlay.
  - Canvas renders even in empty state (no image connected) so the user can draw immediately.
  - The pen_size property controls stroke width; draw_commands redraw on property change.
- Wired onDrawCommand callback in page.tsx → handleDrawCommand → api.setProperties(nodeId, {draw_commands}). Updates local graph state immediately for responsive canvas redraw.
- Verified: node does NOT move during draw (moved: false); draw stroke persists to backend (draw_commands count: 1).

Stage Summary:
- Output handles no longer overlap text: all handles overlap=false (left handles at node left border, right handles at node right border, both half-outside).
- Draw Mask is now drawable: canvas overlay (240x180) on top of image preview, mouse-drag draws cyan strokes, node stays put, strokes persist to backend draw_commands property. Brush/eraser/clear toolbar works.
- Lint clean (0 errors, 0 warnings).

---
Task ID: RESEARCH-1
Agent: Z.ai Code (Explore sub-agent)
Task: Research how original preset nodes in `download/multimodal-node-editor/src/nodes/` load large models (CLIP/LLM/classification/speech_enhancement/object_detection/etc.) — identify the loading pattern (init vs lazy vs compute-time), whether models are cached on `self`, which libraries are used, and whether any model-sharing mechanism exists. Make recommendations for how a global `ModelRegistry` should integrate with the existing `ComputeLogic` pattern.

## Files Examined (8 representative impl.py + 2 base classes)

1. `image/deep_learning/classification/impl.py` (MobileNetV3 / EfficientNetLite4 / MNIST — onnxruntime)
2. `image/deep_learning/object_detection/impl.py` (DEIMv2 / DEIMv2Wholebody34 — onnxruntime via custom DEIMv2 wrapper class)
3. `image/deep_learning/low_light_image_enhancement/impl.py` (MobileIE / TBEFN / CPGA-Net — onnxruntime)
4. `image/deep_learning/monocular_depth_estimation/impl.py` (Lite-Mono / Depth-Anything-V2 — onnxruntime)
5. `image/deep_learning/face_detection/impl.py` (BlazeFace / FaceLandmarker / YuNet — mediapipe + cv2.FaceDetectorYN)
6. `image/deep_learning/semantic_segmentation/impl.py` (MediaPipe selfie/hair/multiclass + PP-LiteSeg + Road Seg — mediapipe + onnxruntime, two separate caches)
7. `image/deep_learning/ocr/impl.py` (PaddleOCR v3/v5 — onnxruntime via PaddleOCREngine / PaddleOCRv3Engine wrappers)
8. `audio/deep_learning/speech_enhancement/impl.py` (GTCRN / FastEnhancer — onnxruntime, STATEFUL wrapper)
9. `audio/deep_learning/classification/impl.py` (MediaPipe YamNet — mediapipe.tasks.python.audio)
10. `text/deep_learning/language_classification/impl.py` (MediaPipe Language Detector — mediapipe.tasks.python.text)
11. `openai/llm`, `openai/vlm`, `openai/realtime_stt`, `openai/image_generation` (openai SDK + websocket-client)
12. `download/multimodal-node-editor/src/node_editor/node_def.py` (original `ComputeLogic` base + `NodeDefinition.node_instances` per-node-id factory)
13. `mini-services/node-editor-server/node_def.py` (refactored shim — single `compute_logic` instance per NodeDefinition, no per-node-id factory)

## Findings

### 1. Where do nodes load models? (init vs lazy vs compute-time)

**100% LAZY / COMPUTE-TIME.** No node loads a model inside `__init__`. `__init__` only stores metadata: `_last_model_index = -1`, `_last_result_json`, paths resolved from `Path(__file__).parent`, and per-instance buffers. The actual model load always happens inside `compute()` (directly, or via a private `_load_model(...)` helper called from compute). This is the universal pattern across every node examined.

### 2. Do nodes cache the model on `self`?

**Two distinct sub-patterns exist:**

**Pattern A — CLASS-level cache (most common; image deep_learning + text + OCR):**
A `_model_cache: Dict[str, Any]` and `_model_errors: Dict[str, str]` are declared as **class attributes** on the ComputeLogic subclass, shared across all instances. `compute()` calls `_load_model(model_name, use_gpu)` which:
  - computes `cache_key = f"{model_name}_gpu={use_gpu and CUDA_AVAILABLE}"` (or by `model_index`, or by absolute model path)
  - returns cached entry if present
  - otherwise constructs the model (e.g. `onnxruntime.InferenceSession(...)` or `vision.FaceDetector.create_from_options(...)`), stores it in `_model_cache[cache_key]`, and also stashes any error in `_model_errors[cache_key]` so a failed load isn't retried.

Used by: `classification`, `object_detection`, `low_light_image_enhancement`, `monocular_depth_estimation`, `face_detection`, `semantic_segmentation` (which has TWO class caches — `_mediapipe_cache` + `_onnx_cache`), `hand_pose_estimation`, `ocr`, `language_classification`.

**Pattern B — INSTANCE-level cache (audio deep_learning):**
`self._model` / `self._classifier` is stored on the instance, recreated only when `model_index` or `sample_rate` changes. Used by `speech_enhancement` (GTCRNModel/FastEnhancerModel wrappers holding STFT buffers, overlap-add buffers, recurrent conv caches) and `audio/classification` (YamNet classifier + WaveformBuffer + AudioClassificationBuffer). **This is intentional** — these models are STATEFUL streaming models; their internal caches cannot be shared across node instances because each node instance has its own audio stream timeline.

**Pattern C — No cache (OpenAI nodes):** `openai/llm`, `vlm`, `image_generation`, `realtime_stt` create a fresh `openai.OpenAI(api_key=...)` client inside a worker thread on every trigger; the "model" is remote. The state they keep on `self` is for trigger edge-detection (`_last_button_value`) and for accumulating streamed `_content`/`_result`/`_error` between compute() calls.

### 3. What libraries are used?

| Library | Where |
|---|---|
| `onnxruntime` | image classification, object_detection (via DEIMv2 wrapper), low_light, monocular_depth, ocr (PaddleOCREngine), speech_enhancement, semantic_segmentation (PP-LiteSeg/Road), classification |
| `mediapipe.tasks.python.vision` | face_detection (BlazeFace/FaceLandmarker), semantic_segmentation (selfie/hair/multiclass), pose_estimation, hand_pose_estimation |
| `mediapipe.tasks.python.audio` | audio/classification (YamNet) |
| `mediapipe.tasks.python.text` | language_classification |
| `cv2.FaceDetectorYN` | face_detection YuNet variant (OpenCV DNN) |
| `sahi` (slicing + NMS) | face_detection + object_detection (optional SAHI inference) |
| `motpy` | face_detection + object_detection (optional multi-object tracking) |
| `openai` SDK | llm, vlm, image_generation |
| `websocket-client` | realtime_stt |
| `numpy` / `cv2` / `PIL` | universal preprocessing/postprocessing |

**No `torch` and no `transformers` are used anywhere in the surveyed nodes** — the original authors chose ONNX + MediaPipe + OpenCV for portability and CPU-friendliness. CUDA is opt-in via `properties.use_gpu` and only selects the `CUDAExecutionProvider` for onnxruntime.

### 4. Representative code snippets

**Pattern A (class cache, the most common form) — `image/deep_learning/classification/impl.py`:**
```python
class ClassificationLogic(ComputeLogic):
    # クラス共有のモデルキャッシュ
    _model_cache: Dict[str, Any] = {}
    _model_errors: Dict[str, str] = {}

    def __init__(self):
        self._last_model_index: int = -1
        self._last_result_json: str = json.dumps({"classifications": []})

    def _load_model(self, model_name, use_gpu=False) -> Tuple[Optional[Any], Optional[str]]:
        cache_key = f"{model_name}_gpu={use_gpu and CUDA_AVAILABLE}"
        if cache_key in self._model_cache:
            return self._model_cache[cache_key], None
        if cache_key in self._model_errors:
            return None, self._model_errors[cache_key]
        model_path = self._get_model_path(model_name)
        if not os.path.exists(model_path):
            error_msg = f"Model file not found: {model_path}"
            self._model_errors[cache_key] = error_msg
            return None, error_msg
        try:
            import onnxruntime
            session = onnxruntime.InferenceSession(model_path, providers=self._get_providers(use_gpu))
            model_info = {"session": session, "input_name": session.get_inputs()[0].name, "input_size": MODEL_INPUT_SIZES.get(model_name, (224, 224))}
            self._model_cache[cache_key] = model_info
            return model_info, None
        except Exception as e:
            self._model_errors[cache_key] = f"Failed to load model: {e}"
            return None, ...

    def compute(self, inputs, properties, context=None):
        ...
        model_info, load_error = self._load_model(model_name, use_gpu)  # lazy load on first call
        if model_info is None:
            raise RuntimeError(load_error)
        ...
```

**Pattern B (instance cache, stateful audio) — `audio/deep_learning/speech_enhancement/impl.py`:**
```python
class SpeechEnhancementLogic(ComputeLogic):
    def __init__(self):
        self._buffer: WaveformBuffer | None = None
        self._model: GTCRNModel | FastEnhancerModel | None = None
        self._last_model_index: int = -1
        self._last_sample_rate: int = -1
        current_dir = Path(__file__).parent
        self._gtcrn_model_path = str(current_dir / "gtcrn" / "model" / "gtcrn_simple.onnx")
        ...

    def compute(self, inputs, properties, context=None):
        ...
        # Recreate the wrapper only when model or sample_rate changes
        if self._model is None or self._last_model_index != model_index or self._last_sample_rate != sample_rate:
            model_path, model_error = self._get_model_path(model_index)
            if model_error: return {"audio": None, "__error__": model_error}
            if model_index == self.MODEL_GTCRN:
                self._model = GTCRNModel(model_path, sample_rate)
            else:
                self._model = FastEnhancerModel(model_path, sample_rate)
            self._last_model_index = model_index
            self._last_sample_rate = sample_rate
        enhanced = self._model.process(delta_array)
        ...
```

**Pattern C (no model, API call) — `openai/llm/impl.py`:**
```python
class LLMLogic(ComputeLogic):
    def __init__(self):
        self._last_button_value: bool = False
        self._is_executing: bool = False
        self._lock = threading.Lock()
        self._result: str = ""
        self._content: str = ""

    def _call_openai_api_stream(self, api_key, model, system_prompt, user_prompt, temperature):
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)        # fresh client every call
            stream = client.chat.completions.create(model=model, messages=[...], stream=True)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    self._content += chunk.choices[0].delta.content
                    self._result = self._content
        finally:
            with self._lock:
                self._is_executing = False
```

### 5. Does any sharing mechanism already exist?

**Yes — Pattern A is already a process-global cache**, but with three important limitations:

1. **Per-class, not global.** Each `ComputeLogic` subclass has its OWN `_model_cache` dict. `ClassificationLogic._model_cache` and `ObjectDetectionLogic._model_cache` are completely separate, even though both ultimately create `onnxruntime.InferenceSession` objects. Two node types that happen to load the same underlying ONNX file (e.g. `object_detection` DEIMv2 vs `object_detection` DEIMv2Wholebody34 — they share the `DEIMv2` Python wrapper class but each `ObjectDetectionLogic` instance still queries the same `_model_cache` since it's the same ComputeLogic class) DO share within a class, but cross-class sharing does not happen.

2. **Single shared ComputeLogic instance.** Both the original `node_editor/node_def.py` (`discover_nodes` at line ~559: `compute_logic_instance = obj()`) and the refactored `mini-services/node-editor-server/node_def.py` register exactly ONE `ComputeLogic` instance per NodeDefinition. The original codebase's `NodeDefinition.get_or_create_instance(node_id)` (lines 139–149) gives per-node-id instances, but the refactored server dropped this and uses a single shared instance per node type. Either way, Pattern A's class-attribute cache makes the cache global across all node instances of that type.

3. **No ref-counting / unload.** Once a model is in `_model_cache` it stays until the process exits. There's no eviction. For the surveyed node types this is fine because the total number of distinct ONNX files is small (~30), but a global registry would let us add LRU/refcount eviction in one place.

4. **Audio streaming models cannot share.** GTCRNModel / FastEnhancerModel / AudioClassificationBuffer all hold streaming state on `self`. The underlying ONNX `InferenceSession` they wrap IS shareable, but the wrapper is not.

### 6. Recommendations: how a global `ModelRegistry` should integrate

**Goal:** Add a process-global `ModelRegistry` that original nodes can opt into with minimal code changes, while leaving Pattern B (stateful audio) and Pattern C (API) untouched.

**(a) New module: `mini-services/node-editor-server/model_registry.py`**
```python
import threading
from typing import Any, Callable, Dict, Optional, Tuple

class _Entry:
    __slots__ = ("model", "error", "lock")
    def __init__(self):
        self.model: Any = None
        self.error: Optional[str] = None
        self.lock = threading.Lock()

class ModelRegistry:
    def __init__(self):
        self._entries: Dict[str, _Entry] = {}
        self._global_lock = threading.Lock()

    def get(self, key: str, loader: Callable[[], Any]) -> Tuple[Optional[Any], Optional[str]]:
        """Return (model, error). Calls loader() exactly once per key."""
        with self._global_lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry()
                self._entries[key] = entry
        with entry.lock:
            if entry.model is not None:
                return entry.model, None
            if entry.error is not None:
                return None, entry.error
            try:
                entry.model = loader()
                return entry.model, None
            except Exception as e:
                entry.error = f"Failed to load model ({key}): {e}"
                return None, entry.error

    def clear(self):
        with self._global_lock:
            self._entries.clear()

registry = ModelRegistry()
```

**(b) Add one helper to the `ComputeLogic` base class** (in `mini-services/node-editor-server/node_def.py`):
```python
class ComputeLogic:
    ...
    def _get_cached_model(self, key: str, loader):
        """Opt-in global model cache. Returns (model, error)."""
        from node_editor.model_registry import registry
        return registry.get(key, loader)
```
Original nodes that import `from node_editor.node_def import ComputeLogic` automatically get this helper — no import changes needed.

**(c) Cache-key convention.** Use a `{library}:{absolute_model_path}:{flags}` scheme so that two different ComputeLogic subclasses loading the same ONNX file actually share a session:
- ONNX: `f"onnx:{abs_model_path}:gpu={use_gpu_and_cuda}"`
- MediaPipe vision/audio/text: `f"mediapipe:{abs_model_path}"` (MediaPipe detectors are NOT guaranteed thread-safe — the registry's per-key lock handles this; for nodes that need concurrent inference, registry can later expose a `get_with_lock()` variant)
- PaddleOCR: `f"paddle:{det_path}|{rec_path}|{dict_path}:gpu={...}"` (composite key)
- DEIMv2 wrapper: `f"deimv2:{abs_model_path}:{wrapper_class_name}:gpu={...}"`

**(d) Minimal migration for original nodes (3-line diff per `_load_model`):**
```python
# Before (Pattern A):
if cache_key in self._model_cache:
    return self._model_cache[cache_key], None
...
session = onnxruntime.InferenceSession(model_path, providers=providers)
self._model_cache[cache_key] = session
return session, None

# After:
return self._get_cached_model(
    f"onnx:{model_path}:gpu={use_gpu and CUDA_AVAILABLE}",
    lambda: onnxruntime.InferenceSession(model_path, providers=providers),
)
```
The class-level `_model_cache`/`_model_errors` dicts can be left in place (no-op) or deleted — both work. This makes the change purely additive: nodes that haven't been migrated keep using their per-class cache.

**(e) Don't touch Pattern B (stateful audio).** Speech enhancement / audio classification wrappers must stay per-instance. But the underlying ONNX `InferenceSession` they create can be pulled from the registry:
```python
# In GTCRNModel.__init__:
session, err = registry.get(f"onnx:{model_path}:gpu=False",
                            lambda: onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"]))
self.session = session  # shared; buffers below remain on self
self.conv_cache = np.zeros(...)
```
This lets two `SpeechEnhancementLogic` instances on the canvas share the same ONNX session while keeping their own streaming state.

**(f) Don't touch Pattern C (OpenAI).** Remote API models are not cacheable at the model layer; only the `openai.OpenAI` client could be cached (negligible benefit). Leave as-is.

**(g) Optional: stats endpoint.** Add `/api/models/cache` to `main.py` returning `{key, loaded, error, type}` for debugging — useful when running with 30+ ONNX files.

### 7. Summary table

| Pattern | Where used | Model loaded in | Stored on | Shareable across nodes? |
|---|---|---|---|---|
| A: class-level dict cache | image DL (6 nodes), OCR, text language_classification | `compute()` via `_load_model()` | `cls._model_cache` (class attr) | Yes — within same ComputeLogic class only |
| B: instance-level cache | audio DL (speech_enhancement, classification) | `compute()` after index/sample-rate change | `self._model` / `self._classifier` | No — wrappers hold streaming state. Underlying ONNX session IS shareable. |
| C: no cache (remote API) | openai/* (4 nodes) | inside worker thread, every call | nothing | N/A — remote model |

**Next action:** Implement `model_registry.py` + add `_get_cached_model` helper to the refactored `ComputeLogic`, then migrate `classification`, `object_detection`, `low_light_image_enhancement`, `monocular_depth_estimation`, `face_detection`, `semantic_segmentation`, `ocr`, `language_classification`, and the underlying ONNX session of `speech_enhancement` / `audio/classification` (without touching their per-instance streaming state). This is a purely additive refactor that can be done one node at a time.

---
Task ID: FIX-4
Agent: Z.ai Code (main)
Task: Implement a global model instance manager so that multiple nodes using the same large model (CLIP, LLM, ONNX session, etc.) share ONE copy instead of each loading their own — preventing memory overflow.

Work Log:
- Researched original node model-loading patterns (via subagent RESEARCH-1): all nodes load lazily in compute(), most use a class-attribute `_model_cache` dict (shared within one node type but NOT across types). No cross-node-type sharing, no ref-counting, no eviction.
- Created `mini-services/node-editor-server/model_registry.py`:
  - `ModelRegistry` class: process-global singleton with `get(key, loader, est_bytes, label)`.
  - Thread-safe per-key locking so `loader()` is called exactly once per key; concurrent callers block on the per-key lock and get the cached result.
  - Error caching: if `loader()` raises, the error is cached so the load isn't retried on every frame.
  - LRU eviction: evicts least-recently-used entries when count exceeds `max_entries` (16) or bytes exceed `max_bytes` (8 GB).
  - `snapshot()` returns a JSON view for the UI (key, label, loaded, error, load_count, hit_count, est_mb, last_used_at).
  - `unload(key)` / `clear()` for manual cleanup with best-effort model release (cpu(), release(), del).
  - `_estimate_model_bytes()` handles PyTorch params, HF get_memory_footprint, ONNX/MediaPipe nominal defaults.
- Created `node_editor/model_registry.py` shim so original nodes can `from node_editor.model_registry import registry`.
- Added `_get_cached_model(key, loader, est_bytes, label)` to the `ComputeLogic` base class — every node (original + built-in) inherits it. Original nodes opt in with a 3-line diff in their `_load_model`.
- Added a demo node `ai.shared_model_inference` that uses `_get_cached_model` to load a mock 50 MB model. Drop 2+ with the same `model_name` → they share one instance.
- Added API endpoints: `GET /api/models`, `DELETE /api/models/{key}`, `DELETE /api/models`.
- Built `ModelsPanel.tsx`: shows loaded models with key/label/size/hits/loads/last-used, memory bar (red >80%, amber >50%), per-model unload button, unload-all button, refresh, auto-poll every 2s when active.
- Integrated ModelsPanel into the right sidebar as a tab (Config / Models).
- Fixed a Python bug: `register_node(...)` was accidentally indented inside the `SharedModelInferenceCompute` class body; moved it to module level.

Stage Summary:
- Verified end-to-end: 2 "Shared Model Inference" nodes with same model_name → both return identical `model_id` (140461220325872), registry shows 1 entry (not 2), 50 MB total, loads=1 hits=1.
- Models panel: shows 1/16 entries, 50.0/8192 MB, memory bar green. Unload All and per-model Unload both verified working.
- Lint clean (0 errors, 0 warnings). Both services running.
- The mechanism is additive: original nodes need only replace their load branch with `self._get_cached_model(key, loader)` to join the global cache.
