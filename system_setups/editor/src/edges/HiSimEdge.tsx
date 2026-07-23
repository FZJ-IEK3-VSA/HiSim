import { getBezierPath, BaseEdge, type EdgeProps } from '@xyflow/react'

/** Custom edge that renders a glow + thicker stroke when selected. */
export function HiSimEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  selected,
}: EdgeProps) {
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const strokeColor = typeof style.stroke === 'string' ? style.stroke : '#b1b1b7'

  return (
    <BaseEdge
      path={edgePath}
      style={{
        ...style,
        strokeWidth: selected ? 4 : 2,
        filter: selected ? `drop-shadow(0 0 5px ${strokeColor})` : undefined,
      }}
    />
  )
}
