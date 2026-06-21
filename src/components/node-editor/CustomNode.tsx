"use client";

import { memo, useRef, useState, useEffect, useCallback } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { X, ImageIcon, Brush, Eraser, Trash2 } from "lucide-react";
import type { GraphNode, Port as GPort } from "@/lib/node-editor/types";
import { typeColor } from "@/lib/node-editor/types";
import { PerspectiveCanvas, CropCanvas, PIPCanvas, OmnidirectionalCanvas } from "./InteractiveCanvas";

export interface CustomNodeData {
  node: GraphNode;
  category: string;
  isSelected: boolean;
  hasError: boolean;
  errorMessage?: string;
  outputValues: Record<string, unknown>; // "port_name" -> value
  /** Input values too — so an image INPUT can be previewed (e.g. Draw Mask). */
  inputValues?: Record<string, unknown>;
  displayText?: string;
  comment?: string;
  connectedInputPortIds: Set<string>;
  available?: boolean; // false when backend stubbed the compute
  onDeleteDynamicPort?: (nodeId: string, portId: string) => void;
  /** Called when the user draws on a draw-type node's preview canvas.
   *  Receives the node id and the updated draw_commands array. */
  onDrawCommand?: (nodeId: string, commands: unknown[]) => void;
  /** Called when an interactive canvas changes a property (crop, perspective, etc.) */
  onPropertyChange?: (nodeId: string, name: string, value: unknown) => void;
}

/** Node definition ids that support interactive drawing on the preview. */
const DRAW_NODE_IDS = ["image.draw.mask", "image.draw.canvas"];

/** Node definition ids that use interactive canvases (mouse click/drag). */
const PERSPECTIVE_NODE_IDS = ["image.transform.click_perspective"];
const CROP_NODE_IDS = ["image.transform.crop"];
const PIP_NODE_IDS = ["image.draw.picture_in_picture"];
const OMNIDIRECTIONAL_NODE_IDS = ["image.filter.omnidirectional_viewer"];

const PREVIEW_W = 240;
const PREVIEW_H = 180;

/**
 * PortRow — one row per port.  The row is `position: relative` so the Handle
 * (which is `position: absolute`) can be centred with `top: 50%`.  This
 * guarantees the dot always sits exactly next to the port text.
 */
function PortRow({
  port,
  side,
  connected,
  value,
  onDeleteDynamic,
  available = true,
}: {
  port: GPort;
  side: "in" | "out";
  connected: boolean;
  value?: unknown;
  onDeleteDynamic?: () => void;
  available?: boolean;
}) {
  const isDynamic = port.metadata?.is_dynamic;
  const color = typeColor(port.data_type);
  const label = port.display_name || port.name;

  return (
    <div
      className="flex items-center gap-2 text-xs group relative leading-5 py-0.5"
      style={{ minHeight: 22 }}
    >
      <Handle
        id={port.id}
        type={side === "in" ? "target" : "source"}
        position={side === "in" ? Position.Left : Position.Right}
        className="!w-3 !h-3 !border-2 !border-white/90"
        style={{
          top: "50%",
          background: color,
          // Push the handle to the NODE border (past the body's px-3 = 12px
          // padding) so the dot sits at the edge, half-outside, and never
          // overlaps the port text.
          // - Left handle: ReactFlow uses left:0; translate(-50%,-50%).
          //   We set left:-12px to reach the node border.
          // - Right handle: ReactFlow uses right:0; translate(50%,-50%).
          //   We set right:-12px to reach the node border and keep the
          //   matching translate(50%,-50%) so the dot centres on the edge.
          left: side === "in" ? "-12px" : undefined,
          right: side === "out" ? "-12px" : undefined,
          transform: side === "in" ? "translate(-50%, -50%)" : "translate(50%, -50%)",
        }}
        title={`${port.name} : ${port.data_type}${!available ? " (unavailable)" : ""}`}
      />
      {side === "in" ? (
        <>
          <span
            className="flex-1 truncate text-zinc-200 pl-1"
            title={`${port.name} (${port.data_type})`}
          >
            {label}
            {isDynamic && (
              <span className="ml-1 text-[9px] text-cyan-400/80 align-middle">dyn</span>
            )}
          </span>
          {connected && (
            <span className="px-1 py-0.5 rounded bg-emerald-600/30 text-emerald-300 text-[9px]">
              linked
            </span>
          )}
          {isDynamic && !connected && onDeleteDynamic && (
            <button
              onClick={onDeleteDynamic}
              className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-red-600/40 text-red-300"
              title="Delete dynamic port"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </>
      ) : (
        <>
          <span className="flex-1" />
          {value !== undefined && value !== null && value !== "" && (
            <span className="px-1.5 py-0.5 rounded bg-zinc-700/70 text-zinc-300 font-mono text-[10px] max-w-[80px] truncate">
              {String(value)}
            </span>
          )}
          <span
            className="truncate text-zinc-200 pr-1 text-right"
            title={`${port.name} (${port.data_type})`}
          >
            {label}
            {isDynamic && (
              <span className="ml-1 text-[9px] text-cyan-400/80 align-middle">dyn</span>
            )}
          </span>
        </>
      )}
    </div>
  );
}

/**
 * ImagePreview — always-shown preview box for image-type ports.
 *
 * For draw-type nodes (image.draw.mask / image.draw.canvas) renders an
 * interactive canvas overlay with `nodrag` so the user can draw with the
 * mouse without dragging the node.
 */
function ImagePreview({
  src,
  empty,
  isDrawNode,
  penSize,
  drawCommands,
  onDraw,
  onClear,
}: {
  src?: string | null;
  empty?: boolean;
  isDrawNode?: boolean;
  penSize?: number;
  drawCommands?: unknown[];
  onDraw?: (cmds: unknown[]) => void;
  onClear?: () => void;
}) {
  const bgRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const eraseRef = useRef(false);
  const lastPosRef = useRef<{ x: number; y: number } | null>(null);
  const [tool, setTool] = useState<"brush" | "eraser">("brush");

  // redraw the mask canvas when drawCommands change
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, c.width, c.height);
    if (!drawCommands || drawCommands.length === 0) return;
    for (const cmd of drawCommands) {
      const dc = cmd as { type: string; points?: { x: number; y: number }[]; erase?: boolean; size?: number };
      if (dc.type === "stroke" && dc.points && dc.points.length > 0) {
        ctx.globalCompositeOperation = dc.erase ? "destination-out" : "source-over";
        ctx.strokeStyle = "#22d3ee";
        ctx.lineWidth = dc.size || penSize || 10;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        ctx.moveTo(dc.points[0].x, dc.points[0].y);
        for (let i = 1; i < dc.points.length; i++) {
          ctx.lineTo(dc.points[i].x, dc.points[i].y);
        }
        ctx.stroke();
      }
    }
  }, [drawCommands, penSize]);

  const getPos = useCallback((e: React.MouseEvent) => {
    const c = canvasRef.current;
    if (!c) return { x: 0, y: 0 };
    const rect = c.getBoundingClientRect();
    // scale from display size to canvas internal resolution
    const sx = c.width / rect.width;
    const sy = c.height / rect.height;
    return {
      x: (e.clientX - rect.left) * sx,
      y: (e.clientY - rect.top) * sy,
    };
  }, []);

  const handleDown = useCallback((e: React.MouseEvent) => {
    if (!isDrawNode) return;
    e.preventDefault();
    e.stopPropagation();
    drawingRef.current = true;
    eraseRef.current = e.button === 2 || tool === "eraser";
    const pos = getPos(e);
    lastPosRef.current = pos;
    // start a new stroke
    const cmd = { type: "stroke", points: [pos], erase: eraseRef.current, size: penSize || 10 };
    const next = [...(drawCommands || []), cmd];
    onDraw?.(next);
  }, [isDrawNode, tool, penSize, drawCommands, onDraw, getPos]);

  const handleMove = useCallback((e: React.MouseEvent) => {
    if (!isDrawNode || !drawingRef.current) return;
    e.preventDefault();
    e.stopPropagation();
    const pos = getPos(e);
    const last = lastPosRef.current;
    if (last && Math.hypot(pos.x - last.x, pos.y - last.y) < 1) return;
    lastPosRef.current = pos;
    // append point to the last stroke
    const cmds = [...(drawCommands || [])];
    if (cmds.length > 0) {
      const lastCmd = cmds[cmds.length - 1] as { points: { x: number; y: number }[] };
      lastCmd.points = [...lastCmd.points, pos];
      onDraw?.(cmds);
    }
  }, [isDrawNode, drawCommands, onDraw, getPos]);

  const handleUp = useCallback((e: React.MouseEvent) => {
    if (!isDrawNode) return;
    e.preventDefault();
    e.stopPropagation();
    drawingRef.current = false;
    lastPosRef.current = null;
  }, [isDrawNode]);

  // empty placeholder (no image yet) — still allow drawing on a black canvas
  if (empty && !src) {
    if (isDrawNode) {
      return (
        <div
          className="mt-1 relative rounded border border-dashed border-zinc-700 bg-zinc-950 overflow-hidden nodrag"
          style={{ width: PREVIEW_W, height: PREVIEW_H }}
          onContextMenu={(e) => e.preventDefault()}
        >
          <div className="absolute inset-0 flex items-center justify-center gap-1.5 text-zinc-600 text-[10px] pointer-events-none">
            <ImageIcon className="w-3.5 h-3.5" />
            <span>connect an image, then draw</span>
          </div>
          <canvas
            ref={canvasRef}
            width={PREVIEW_W}
            height={PREVIEW_H}
            className="absolute inset-0 w-full h-full"
            style={{ cursor: tool === "eraser" ? "cell" : "crosshair" }}
            onMouseDown={handleDown}
            onMouseMove={handleMove}
            onMouseUp={handleUp}
            onMouseLeave={handleUp}
          />
          <DrawToolbar tool={tool} setTool={setTool} onClear={onClear} />
        </div>
      );
    }
    return (
      <div className="mt-1 rounded border border-dashed border-zinc-700 bg-zinc-950/60 flex items-center justify-center gap-1.5 text-zinc-600 text-[10px] py-6 max-w-[240px]">
        <ImageIcon className="w-3.5 h-3.5" />
        <span>image preview</span>
      </div>
    );
  }

  // image present
  return (
    <div
      className="mt-1 relative rounded border border-zinc-700 bg-zinc-950 overflow-hidden nodrag"
      style={{ width: PREVIEW_W, height: PREVIEW_H }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <img
        ref={bgRef}
        src={src || undefined}
        alt="preview"
        className="absolute inset-0 w-full h-full object-contain pointer-events-none"
      />
      {isDrawNode && (
        <>
          <canvas
            ref={canvasRef}
            width={PREVIEW_W}
            height={PREVIEW_H}
            className="absolute inset-0 w-full h-full"
            style={{ cursor: tool === "eraser" ? "cell" : "crosshair" }}
            onMouseDown={handleDown}
            onMouseMove={handleMove}
            onMouseUp={handleUp}
            onMouseLeave={handleUp}
          />
          <DrawToolbar tool={tool} setTool={setTool} onClear={onClear} />
        </>
      )}
    </div>
  );
}

function DrawToolbar({
  tool,
  setTool,
  onClear,
}: {
  tool: "brush" | "eraser";
  setTool: (t: "brush" | "eraser") => void;
  onClear?: () => void;
}) {
  return (
    <div className="absolute top-1 right-1 flex items-center gap-1 bg-zinc-900/80 rounded p-0.5 border border-zinc-700">
      <button
        onClick={(e) => { e.stopPropagation(); setTool("brush"); }}
        className={`p-1 rounded ${tool === "brush" ? "bg-cyan-600/40 text-cyan-300" : "text-zinc-400 hover:bg-zinc-700"}`}
        title="Brush (left drag)"
      >
        <Brush className="w-3 h-3" />
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); setTool("eraser"); }}
        className={`p-1 rounded ${tool === "eraser" ? "bg-cyan-600/40 text-cyan-300" : "text-zinc-400 hover:bg-zinc-700"}`}
        title="Eraser"
      >
        <Eraser className="w-3 h-3" />
      </button>
      {onClear && (
        <button
          onClick={(e) => { e.stopPropagation(); onClear(); }}
          className="p-1 rounded text-red-400 hover:bg-red-950/50"
          title="Clear drawing"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

function CustomNodeImpl(props: NodeProps) {
  const { data } = props as unknown as { data: CustomNodeData };
  const {
    node,
    category,
    isSelected,
    hasError,
    errorMessage,
    outputValues,
    inputValues,
    displayText,
    comment,
    connectedInputPortIds,
    available = true,
    onDeleteDynamicPort,
    onDrawCommand,
    onPropertyChange,
  } = data;

  const categoryColor = CATEGORY_COLORS[category.split(".")[0]] || "#64748b";
  const triggerBadge =
    node.trigger_mode === "ANY"
      ? { label: "ANY", cls: "bg-amber-500/20 text-amber-300 border-amber-500/40" }
      : { label: "ALL", cls: "bg-sky-500/20 text-sky-300 border-sky-500/40" };

  const numInputs = node.inputs.length;
  const isDrawNode = DRAW_NODE_IDS.includes(node.definition_id);
  const isPerspectiveNode = PERSPECTIVE_NODE_IDS.includes(node.definition_id);
  const isCropNode = CROP_NODE_IDS.includes(node.definition_id);
  const isPIPNode = PIP_NODE_IDS.includes(node.definition_id);
  const isOmnidirectionalNode = OMNIDIRECTIONAL_NODE_IDS.includes(node.definition_id);
  const isInteractiveNode = isPerspectiveNode || isCropNode || isPIPNode || isOmnidirectionalNode;

  const hasImageOutput = node.outputs.some((p) => p.data_type === "image");
  const hasImageInput = node.inputs.some((p) => p.data_type === "image");
  const showPreview = hasImageOutput || hasImageInput;

  // preview source.
  // For interactive nodes (perspective/crop/PIP/draw) the preview MUST be the
  // input image — the anchor points / selection rectangle are relative to the
  // input image, so showing the output image would put them in the wrong place.
  // For non-interactive image-output nodes (sources, concat, etc.) show the
  // output image.
  const previewSrc = (() => {
    if (isInteractiveNode || isDrawNode) {
      // input image first
      if (inputValues) {
        for (const ip of node.inputs) {
          if (ip.data_type === "image" && inputValues[ip.name]) {
            return String(inputValues[ip.name]);
          }
        }
      }
      // fall back to output if no input connected
      for (const op of node.outputs) {
        if (op.data_type === "image" && outputValues[op.name]) {
          return String(outputValues[op.name]);
        }
      }
      return null;
    }
    // non-interactive: output first, then input
    for (const op of node.outputs) {
      if (op.data_type === "image" && outputValues[op.name]) {
        return String(outputValues[op.name]);
      }
    }
    if (inputValues) {
      for (const ip of node.inputs) {
        if (ip.data_type === "image" && inputValues[ip.name]) {
          return String(inputValues[ip.name]);
        }
      }
    }
    return null;
  })();

  const penSize = typeof node.properties.pen_size === "number" ? node.properties.pen_size : 10;
  const drawCommands = (node.properties.draw_commands as unknown[]) || [];

  const handleDraw = useCallback((cmds: unknown[]) => {
    onDrawCommand?.(node.id, cmds);
  }, [node.id, onDrawCommand]);

  const handleClear = useCallback(() => {
    onDrawCommand?.(node.id, []);
  }, [node.id, onDrawCommand]);

  return (
    <div
      className={`relative rounded-lg border bg-zinc-900/95 shadow-lg backdrop-blur-sm transition-all min-w-[200px] ${
        isSelected
          ? "border-cyan-400 ring-2 ring-cyan-400/40"
          : hasError
          ? "border-red-500/70"
          : !available
          ? "border-zinc-700 opacity-60"
          : "border-zinc-700"
      }`}
    >
      {/* header */}
      <div
        className="flex items-center gap-1.5 px-3 rounded-t-lg"
        style={{
          background: `linear-gradient(90deg, ${categoryColor}33, transparent)`,
        }}
      >
        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: categoryColor }} />
        <span className="text-xs font-semibold text-zinc-100 truncate flex-1 py-2" title={node.name}>
          {node.name}
        </span>
        {!available && (
          <span className="text-[9px] px-1 py-0.5 rounded bg-zinc-700 text-zinc-400" title="Dependencies unavailable">
            N/A
          </span>
        )}
        <span className={`text-[9px] px-1.5 py-0.5 rounded border shrink-0 ${triggerBadge.cls}`}>
          {triggerBadge.label}
        </span>
      </div>

      {/* body */}
      <div className="px-3 pb-2 pt-0">
        {numInputs > 0 && (
          <div>
            {node.inputs.map((p) => (
              <PortRow
                key={p.id}
                port={p}
                side="in"
                connected={connectedInputPortIds.has(p.id)}
                available={available}
                onDeleteDynamic={
                  onDeleteDynamicPort ? () => onDeleteDynamicPort(node.id, p.id) : undefined
                }
              />
            ))}
          </div>
        )}

        {numInputs > 0 && node.outputs.length > 0 && (
          <div className="h-px bg-zinc-700/60 my-1" />
        )}

        {node.outputs.length > 0 && (
          <div>
            {node.outputs.map((p) => (
              <PortRow
                key={p.id}
                port={p}
                side="out"
                value={outputValues[p.name]}
                available={available}
              />
            ))}
          </div>
        )}

        {showPreview && !isInteractiveNode && (
          <ImagePreview
            src={previewSrc}
            empty={!previewSrc}
            isDrawNode={isDrawNode}
            penSize={penSize}
            drawCommands={drawCommands}
            onDraw={handleDraw}
            onClear={handleClear}
          />
        )}

        {/* Interactive canvases for click/drag nodes */}
        {isPerspectiveNode && (
          <PerspectiveCanvas
            src={previewSrc}
            points={[
              { x: Number(node.properties.x1 ?? 0.2), y: Number(node.properties.y1 ?? 0.2) },
              { x: Number(node.properties.x2 ?? 0.8), y: Number(node.properties.y2 ?? 0.2) },
              { x: Number(node.properties.x3 ?? 0.8), y: Number(node.properties.y3 ?? 0.8) },
              { x: Number(node.properties.x4 ?? 0.2), y: Number(node.properties.y4 ?? 0.8) },
            ]}
            currentPoint={Number(node.properties.current_point ?? 1)}
            onPointChange={(pt, x, y) => {
              onPropertyChange?.(node.id, `x${pt}`, x);
              onPropertyChange?.(node.id, `y${pt}`, y);
            }}
            onCurrentPointChange={(pt) => onPropertyChange?.(node.id, "current_point", pt)}
          />
        )}

        {isCropNode && (
          <CropCanvas
            src={previewSrc}
            minX={Number(node.properties.min_x ?? 0)}
            minY={Number(node.properties.min_y ?? 0)}
            maxX={Number(node.properties.max_x ?? 1)}
            maxY={Number(node.properties.max_y ?? 1)}
            onCropChange={(minX, minY, maxX, maxY) => {
              onPropertyChange?.(node.id, "min_x", minX);
              onPropertyChange?.(node.id, "min_y", minY);
              onPropertyChange?.(node.id, "max_x", maxX);
              onPropertyChange?.(node.id, "max_y", maxY);
            }}
          />
        )}

        {isPIPNode && (
          <PIPCanvas
            src={previewSrc}
            minX={Number(node.properties.min_x ?? 0.1)}
            minY={Number(node.properties.min_y ?? 0.1)}
            maxX={Number(node.properties.max_x ?? 0.5)}
            maxY={Number(node.properties.max_y ?? 0.5)}
            onRegionChange={(minX, minY, maxX, maxY) => {
              onPropertyChange?.(node.id, "min_x", minX);
              onPropertyChange?.(node.id, "min_y", minY);
              onPropertyChange?.(node.id, "max_x", maxX);
              onPropertyChange?.(node.id, "max_y", maxY);
            }}
          />
        )}

        {isOmnidirectionalNode && (
          <OmnidirectionalCanvas
            src={previewSrc}
            pitch={Number(node.properties.pitch ?? 0)}
            yaw={Number(node.properties.yaw ?? 0)}
            roll={Number(node.properties.roll ?? 0)}
            onPitchChange={(v) => onPropertyChange?.(node.id, "pitch", v)}
            onYawChange={(v) => onPropertyChange?.(node.id, "yaw", v)}
            onRollChange={(v) => onPropertyChange?.(node.id, "roll", v)}
          />
        )}

        {displayText && (
          <div className="mt-1 px-2 py-1 rounded bg-zinc-800/80 text-cyan-300 font-mono text-xs text-center truncate">
            {displayText}
          </div>
        )}

        {comment && (
          <div className="mt-1 px-2 py-1 rounded bg-amber-950/40 border border-amber-700/50 text-amber-200 text-[10px] break-words whitespace-pre-wrap">
            {comment}
          </div>
        )}

        {hasError && errorMessage && (
          <div className="mt-1 px-2 py-1 rounded bg-red-950/60 border border-red-700/50 text-red-300 text-[10px] break-words">
            {errorMessage}
          </div>
        )}
      </div>
    </div>
  );
}

export const CustomNode = memo(CustomNodeImpl);

const CATEGORY_COLORS: Record<string, string> = {
  source: "#22c55e",
  math: "#3b82f6",
  image: "#ec4899",
  audio: "#06b6d4",
  display: "#a855f7",
  text: "#f59e0b",
  openai: "#10b981",
  utility: "#64748b",
  general: "#64748b",
};
