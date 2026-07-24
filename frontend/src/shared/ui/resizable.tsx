import { Group, Panel, Separator, useDefaultLayout } from 'react-resizable-panels'
import type { GroupProps, PanelProps, SeparatorProps } from 'react-resizable-panels'
import { cn } from '@/shared/lib/cn'

/** Maps the opencut-style `direction` prop to v4's `orientation` */
const ResizablePanelGroup = ({
  direction,
  className,
  ...props
}: Omit<GroupProps, 'orientation'> & { direction?: 'horizontal' | 'vertical' }) => (
  <Group
    orientation={direction}
    className={cn('size-full', className)}
    {...props}
  />
)

/* v4: numbers = pixels, plain strings = %. Callers pass opencut-style percentages as numbers. */
const asPct = (v: PanelProps['defaultSize']) =>
  v == null ? v : typeof v === 'number' ? String(v) : v

const ResizablePanel = ({ defaultSize, minSize, maxSize, ...props }: PanelProps) => (
  <Panel
    defaultSize={asPct(defaultSize)}
    minSize={asPct(minSize)}
    maxSize={asPct(maxSize)}
    {...props}
  />
)

/* Invisible handle: 6px transparent — dễ grab, không tốn diện tích thị giác */
const ResizableHandle = ({
  withHandle: _withHandle,
  className,
  ...props
}: SeparatorProps & { withHandle?: boolean }) => (
  <Separator
    className={cn(
      'relative z-20 shrink-0 bg-transparent transition-colors hover:bg-border/40 data-[separator=active]:bg-primary/50',
      /* horizontal group → vertical separator (col-resize) */
      'aria-[orientation=vertical]:w-0.5 aria-[orientation=vertical]:self-stretch aria-[orientation=vertical]:cursor-col-resize',
      /* vertical group → horizontal separator (row-resize) */
      'aria-[orientation=horizontal]:h-0.5 aria-[orientation=horizontal]:w-full aria-[orientation=horizontal]:cursor-row-resize',
      className,
    )}
    {...props}
  />
)

export { ResizablePanelGroup, ResizablePanel, ResizableHandle, useDefaultLayout }
