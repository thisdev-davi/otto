import * as React from 'react'
import { cn } from '@/lib/utils'
import { fieldBase } from '@/components/ui/input'

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={cn(fieldBase, 'resize-y leading-relaxed', className)} {...props} />
  }
)
