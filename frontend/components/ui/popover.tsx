'use client'
import * as React from 'react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import { cn } from '@/lib/utils'

export const Popover = PopoverPrimitive.Root
export const PopoverTrigger = PopoverPrimitive.Trigger
export const PopoverAnchor = PopoverPrimitive.Anchor

export const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(function PopoverContent({ className, align = 'end', sideOffset = 6, ...props }, ref) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        ref={ref}
        align={align}
        sideOffset={sideOffset}
        // collisionPadding é o motivo principal de trocar o popover à mão por este:
        // o de "Transferir" era `absolute right-0 w-60` e saía da tela em 375px.
        collisionPadding={8}
        data-otto-pop=""
        className={cn(
          'z-50 rounded-xl border border-line bg-surface-raised shadow-pop p-1',
          // Não deixa o conteúdo passar da viewport; as vars são publicadas pelo Radix.
          'max-h-[var(--radix-popover-content-available-height)] w-[var(--radix-popover-trigger-width)] min-w-[200px] overflow-y-auto',
          className
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  )
})
