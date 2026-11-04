'use client'
import * as React from 'react'
import * as MenuPrimitive from '@radix-ui/react-dropdown-menu'
import { cn } from '@/lib/utils'

// Diferente do Popover: navegação por setas, Home/End, busca por digitação e
// roles de menu — o que uma lista de AÇÕES precisa ter.
export const DropdownMenu = MenuPrimitive.Root
export const DropdownMenuTrigger = MenuPrimitive.Trigger

export const DropdownMenuContent = React.forwardRef<
  React.ElementRef<typeof MenuPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof MenuPrimitive.Content>
>(function DropdownMenuContent({ className, align = 'end', sideOffset = 6, ...props }, ref) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        ref={ref}
        align={align}
        sideOffset={sideOffset}
        collisionPadding={8}
        data-otto-pop=""
        className={cn(
          'z-50 min-w-[200px] max-h-[var(--radix-dropdown-menu-content-available-height)] overflow-y-auto',
          'rounded-xl border border-line bg-surface-raised shadow-pop p-1',
          className
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  )
})

export const DropdownMenuItem = React.forwardRef<
  React.ElementRef<typeof MenuPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof MenuPrimitive.Item> & { destructive?: boolean }
>(function DropdownMenuItem({ className, destructive, ...props }, ref) {
  return (
    <MenuPrimitive.Item
      ref={ref}
      className={cn(
        'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] cursor-pointer outline-none transition-colors',
        // data-highlighted cobre mouse E teclado; :hover sozinho deixaria a
        // navegação por setas sem retorno visual.
        destructive
          ? 'text-danger-fg data-[highlighted]:bg-danger-soft'
          : 'text-ink data-[highlighted]:bg-accent-soft data-[highlighted]:text-accent-fg',
        'data-[disabled]:opacity-40 data-[disabled]:cursor-not-allowed',
        className
      )}
      {...props}
    />
  )
})

export function DropdownMenuLabel({ className, ...props }: React.ComponentPropsWithoutRef<typeof MenuPrimitive.Label>) {
  return (
    <MenuPrimitive.Label
      className={cn('px-2.5 py-1.5 text-[10.5px] font-bold uppercase tracking-wide text-faint', className)}
      {...props}
    />
  )
}

export function DropdownMenuSeparator({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof MenuPrimitive.Separator>) {
  return <MenuPrimitive.Separator className={cn('my-1 h-px bg-line', className)} {...props} />
}
