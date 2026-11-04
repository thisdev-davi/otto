import * as React from 'react'
import { cn } from '@/lib/utils'
import { fieldBase } from '@/components/ui/input'

export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement>

// <select> nativo de propósito: no mobile ele abre o seletor do sistema, que é
// melhor que qualquer dropdown custom. Quem faz a lista seguir o tema escuro é
// o `color-scheme` declarado em globals.css, não CSS nosso.
export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  function Select({ className, ...props }, ref) {
    return <select ref={ref} className={cn(fieldBase, 'cursor-pointer', className)} {...props} />
  }
)
