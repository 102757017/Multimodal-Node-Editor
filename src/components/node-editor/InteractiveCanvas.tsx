"use client";

import { useCallback, useRef, useState, useEffect } from "react";

// ===========================================================================
// Shared helpers for object-fit: contain coordinate math
// ===========================================================================
interface ImageBounds {
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
}

function useImageBounds() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [imageBounds, setImageBounds] = useState<ImageBounds | null>(null);

  const calculateBounds = useCallback((): ImageBounds | null => {
    const container = containerRef.current;
    const img = imgRef.current;
    if (!container || !img) return null;
    if (img.naturalWidth === 0 || img.naturalHeight === 0) return null;
    const cw = container.clientWidth;
    const ch = container.clientHeight;
    const cRatio = cw / ch;
    const iRatio = img.naturalWidth / img.naturalHeight;
    let dw: number, dh: number;
    if (iRatio > cRatio) { dw = cw; dh = cw / iRatio; }
    else { dh = ch; dw = ch * iRatio; }
    return { offsetX: (cw - dw) / 2, offsetY: (ch - dh) / 2, width: dw, height: dh };
  }, []);

  const handleImageLoad = useCallback(() => {
    const b = calculateBounds();
    if (b) setImageBounds(b);
  }, [calculateBounds]);

  const getNormalizedCoords = useCallback((e: React.MouseEvent): { x: number; y: number } => {
    const container = containerRef.current;
    if (!container) return { x: 0, y: 0 };
    const b = calculateBounds();
    if (!b) return { x: 0, y: 0 };
    const rect = container.getBoundingClientRect();
    const zoomX = rect.width / container.clientWidth;
    const zoomY = rect.height / container.clientHeight;
    const cx = (e.clientX - rect.left) / zoomX;
    const cy = (e.clientY - rect.top) / zoomY;
    const ix = (cx - b.offsetX) / b.width;
    const iy = (cy - b.offsetY) / b.height;
    return { x: Math.max(0, Math.min(1, ix)), y: Math.max(0, Math.min(1, iy)) };
  }, [calculateBounds]);

  return { containerRef, imgRef, imageBounds, handleImageLoad, getNormalizedCoords };
}

const PREVIEW_W = 240;
const PREVIEW_H = 180;

// ===========================================================================
// PerspectiveCanvas — click to set 4 corner points
// ===========================================================================
export function PerspectiveCanvas({
  src,
  points, // [{x,y} x4] normalized 0-1
  currentPoint,
  onPointChange,
  onCurrentPointChange,
}: {
  src: string | null;
  points: { x: number; y: number }[];
  currentPoint: number;
  onPointChange: (pointNum: number, x: number, y: number) => void;
  onCurrentPointChange: (pointNum: number) => void;
}) {
  const { containerRef, imgRef, imageBounds, handleImageLoad, getNormalizedCoords } = useImageBounds();

  const handleClick = useCallback((e: React.MouseEvent) => {
    const { x, y } = getNormalizedCoords(e);
    onPointChange(currentPoint, Math.round(x * 100) / 100, Math.round(y * 100) / 100);
    onCurrentPointChange(currentPoint >= 4 ? 1 : currentPoint + 1);
  }, [currentPoint, onPointChange, onCurrentPointChange, getNormalizedCoords]);

  return (
    <div
      ref={containerRef}
      className="mt-1 relative rounded border border-zinc-700 bg-zinc-950 overflow-hidden nodrag"
      style={{ width: PREVIEW_W, height: PREVIEW_H, cursor: "crosshair" }}
      onClick={handleClick}
    >
      {src ? (
        <img
          ref={imgRef}
          src={src}
          alt="preview"
          className="absolute inset-0 w-full h-full object-contain pointer-events-none"
          onLoad={handleImageLoad}
          draggable={false}
        />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-[10px] text-zinc-600">
          connect an image, then click to set 4 points
        </div>
      )}
      {src && imageBounds && (
        <svg
          className="absolute pointer-events-none"
          style={{ top: imageBounds.offsetY, left: imageBounds.offsetX, width: imageBounds.width, height: imageBounds.height }}
        >
          {points.map((p, i) => {
            const next = points[(i + 1) % 4];
            return (
              <line key={`l${i}`} x1={`${p.x * 100}%`} y1={`${p.y * 100}%`} x2={`${next.x * 100}%`} y2={`${next.y * 100}%`} stroke="#22c55e" strokeWidth="2" vectorEffect="non-scaling-stroke" />
            );
          })}
          {points.map((p, i) => {
            const isCurrent = i + 1 === currentPoint;
            return (
              <g key={i}>
                <circle cx={`${p.x * 100}%`} cy={`${p.y * 100}%`} r={isCurrent ? 6 : 4} fill="#22c55e" stroke={isCurrent ? "#fff" : "rgba(0,0,0,0.5)"} strokeWidth={isCurrent ? 2 : 1} vectorEffect="non-scaling-stroke" />
                <text x={`${p.x * 100 + 1.5}%`} y={`${p.y * 100 - 1.5}%`} fill="#22c55e" fontSize="14" fontWeight="bold" textAnchor="start" dominantBaseline="auto" stroke="#000" strokeWidth="0.5" paintOrder="stroke">{i + 1}</text>
              </g>
            );
          })}
          <rect x="5" y="5" width="120" height="20" fill="rgba(0,0,0,0.7)" rx="3" />
          <text x="10" y="18" fill="#22c55e" fontSize="12" fontWeight="bold">Click Point {currentPoint}</text>
        </svg>
      )}
    </div>
  );
}

// ===========================================================================
// CropCanvas — drag to select a rectangle
// ===========================================================================
export function CropCanvas({
  src,
  minX, minY, maxX, maxY,
  onCropChange,
}: {
  src: string | null;
  minX: number; minY: number; maxX: number; maxY: number;
  onCropChange: (minX: number, minY: number, maxX: number, maxY: number) => void;
}) {
  const { containerRef, imgRef, imageBounds, handleImageLoad, getNormalizedCoords } = useImageBounds();
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragEnd, setDragEnd] = useState<{ x: number; y: number } | null>(null);

  const handleDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const c = getNormalizedCoords(e);
    setDragging(true); setDragStart(c); setDragEnd(c);
  }, [getNormalizedCoords]);

  const handleMove = useCallback((e: React.MouseEvent) => {
    if (!dragging) return;
    e.preventDefault(); e.stopPropagation();
    setDragEnd(getNormalizedCoords(e));
  }, [dragging, getNormalizedCoords]);

  const handleUp = useCallback((e: React.MouseEvent) => {
    if (!dragging || !dragStart || !dragEnd) return;
    e.preventDefault(); e.stopPropagation();
    setDragging(false);
    onCropChange(
      Math.round(Math.min(dragStart.x, dragEnd.x) * 100) / 100,
      Math.round(Math.min(dragStart.y, dragEnd.y) * 100) / 100,
      Math.round(Math.max(dragStart.x, dragEnd.x) * 100) / 100,
      Math.round(Math.max(dragStart.y, dragEnd.y) * 100) / 100,
    );
    setDragStart(null); setDragEnd(null);
  }, [dragging, dragStart, dragEnd, onCropChange]);

  const curMinX = dragging && dragStart && dragEnd ? Math.min(dragStart.x, dragEnd.x) : minX;
  const curMinY = dragging && dragStart && dragEnd ? Math.min(dragStart.y, dragEnd.y) : minY;
  const curMaxX = dragging && dragStart && dragEnd ? Math.max(dragStart.x, dragEnd.x) : maxX;
  const curMaxY = dragging && dragStart && dragEnd ? Math.max(dragStart.y, dragEnd.y) : maxY;
  const isFull = minX === 0 && minY === 0 && maxX === 1 && maxY === 1;
  const showSel = src && imageBounds && (dragging || !isFull);

  return (
    <div
      ref={containerRef}
      className="mt-1 relative rounded border border-zinc-700 bg-zinc-950 overflow-hidden nodrag"
      style={{ width: PREVIEW_W, height: PREVIEW_H, cursor: "crosshair" }}
      onMouseDown={handleDown}
      onMouseMove={handleMove}
      onMouseUp={handleUp}
      onMouseLeave={handleUp}
    >
      {src ? (
        <img ref={imgRef} src={src} alt="preview" className="absolute inset-0 w-full h-full object-contain pointer-events-none" onLoad={handleImageLoad} draggable={false} />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-[10px] text-zinc-600">connect an image, then drag to crop</div>
      )}
      {showSel && imageBounds && (
        <div
          className="absolute pointer-events-none"
          style={{
            left: imageBounds.offsetX + curMinX * imageBounds.width,
            top: imageBounds.offsetY + curMinY * imageBounds.height,
            width: (curMaxX - curMinX) * imageBounds.width,
            height: (curMaxY - curMinY) * imageBounds.height,
            border: "2px dashed #22c55e",
            backgroundColor: "rgba(34, 197, 94, 0.1)",
            boxSizing: "border-box",
          }}
        />
      )}
    </div>
  );
}

// ===========================================================================
// PIPCanvas — drag to select a region (picture-in-picture)
// ===========================================================================
export function PIPCanvas({
  src,
  minX, minY, maxX, maxY,
  onRegionChange,
}: {
  src: string | null;
  minX: number; minY: number; maxX: number; maxY: number;
  onRegionChange: (minX: number, minY: number, maxX: number, maxY: number) => void;
}) {
  // same interaction as crop
  return (
    <CropCanvas
      src={src}
      minX={minX} minY={minY} maxX={maxX} maxY={maxY}
      onCropChange={onRegionChange}
    />
  );
}

// ===========================================================================
// OmnidirectionalCanvas — drag to set pitch/yaw/roll (simplified: 3 sliders)
// ===========================================================================
export function OmnidirectionalCanvas({
  src,
  pitch, yaw, roll,
  onPitchChange, onYawChange, onRollChange,
}: {
  src: string | null;
  pitch: number; yaw: number; roll: number;
  onPitchChange: (v: number) => void;
  onYawChange: (v: number) => void;
  onRollChange: (v: number) => void;
}) {
  return (
    <div className="mt-1 space-y-1.5 nodrag">
      {src && (
        <div className="rounded border border-zinc-700 bg-zinc-950 overflow-hidden" style={{ width: PREVIEW_W, height: PREVIEW_H }}>
          <img src={src} alt="preview" className="w-full h-full object-contain" draggable={false} />
        </div>
      )}
      <div className="space-y-1">
        <label className="text-[10px] text-zinc-400 flex justify-between"><span>Pitch</span><span className="font-mono">{pitch.toFixed(0)}°</span></label>
        <input type="range" min={-90} max={90} value={pitch} onChange={(e) => onPitchChange(parseFloat(e.target.value))} className="w-full" />
        <label className="text-[10px] text-zinc-400 flex justify-between"><span>Yaw</span><span className="font-mono">{yaw.toFixed(0)}°</span></label>
        <input type="range" min={-180} max={180} value={yaw} onChange={(e) => onYawChange(parseFloat(e.target.value))} className="w-full" />
        <label className="text-[10px] text-zinc-400 flex justify-between"><span>Roll</span><span className="font-mono">{roll.toFixed(0)}°</span></label>
        <input type="range" min={-180} max={180} value={roll} onChange={(e) => onRollChange(parseFloat(e.target.value))} className="w-full" />
      </div>
    </div>
  );
}
