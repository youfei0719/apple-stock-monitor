import { createFileRoute } from '@tanstack/react-router'
import { Traffic } from '@/features/traffic'

export const Route = createFileRoute('/_authenticated/traffic')({
  component: Traffic,
})
