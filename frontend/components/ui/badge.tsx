import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

// As variantes são 1:1 com os tokens semânticos, e sempre o par soft+fg — é o
// que já era feito à mão (`bg-[#EFF4FE] text-[#1D4FC4]`) em cada chip do app.
const badgeVariants = cva(
  'inline-flex items-center gap-1 h-5 rounded-md px-1.5 text-xs font-medium whitespace-nowrap tabular-nums',
  {
    variants: {
      variant: {
        neutral: 'bg-subtle text-muted',
        accent: 'bg-accent-soft text-accent-fg',
        success: 'bg-success-soft text-success-fg',
        danger: 'bg-danger-soft text-danger-fg',
        warning: 'bg-warning-soft text-warning-fg',
        ai: 'bg-ai-soft text-ai-fg',
      },
    },
    defaultVariants: { variant: 'neutral' },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { badgeVariants }
