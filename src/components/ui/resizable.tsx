import { Group, Panel, Separator } from 'react-resizable-panels'
import type { GroupProps, PanelProps, SeparatorProps } from 'react-resizable-panels'
import { cn } from '@/lib/cn'

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

/* OpenCut-style: thin visible line + wide invisible ::after hit target (~10px) */
const ResizableHandle = ({
  withHandle: _withHandle,
  className,
  ...props
}: SeparatorProps & { withHandle?: boolean }) => (
  <Separator
    className={cn(
      'relative z-20 flex shrink-0 items-center justify-center bg-border/40 transition-colors',
      'hover:bg-primary/50 data-[separator=active]:bg-primary',
      /* col-resize handle (horizontal panel group) */
      'aria-[orientation=vertical]:w-px aria-[orientation=vertical]:self-stretch aria-[orientation=vertical]:cursor-col-resize',
      'aria-[orientation=vertical]:after:content-[""] aria-[orientation=vertical]:after:absolute aria-[orientation=vertical]:after:inset-y-0 aria-[orientation=vertical]:after:left-1/2 aria-[orientation=vertical]:after:w-2.5 aria-[orientation=vertical]:after:-translate-x-1/2',
      /* row-resize handle (vertical panel group) */
      'aria-[orientation=horizontal]:h-px aria-[orientation=horizontal]:w-full aria-[orientation=horizontal]:cursor-row-resize',
      'aria-[orientation=horizontal]:after:content-[""] aria-[orientation=horizontal]:after:absolute aria-[orientation=horizontal]:after:inset-x-0 aria-[orientation=horizontal]:after:top-1/2 aria-[orientation=horizontal]:after:h-2.5 aria-[orientation=horizontal]:after:w-full aria-[orientation=horizontal]:after:-translate-y-1/2 aria-[orientation=horizontal]:after:translate-x-0',
      className,
    )}
    {...props}
  />
)

export { ResizablePanelGroup, ResizablePanel, ResizableHandle }
