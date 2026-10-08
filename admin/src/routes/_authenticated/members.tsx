import { createFileRoute } from '@tanstack/react-router'
import { Members } from '@/features/members'

export const Route = createFileRoute('/_authenticated/members')({
  component: Members,
})
