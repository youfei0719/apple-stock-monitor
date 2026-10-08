import {
  LayoutDashboard,
  LineChart,
  Users,
  CreditCard,
  ServerCog,
  ShieldCheck,
  Command,
} from 'lucide-react'
import { type SidebarData } from '../types'

/**
 * Apple 库存监控 · 后台导航（5 个页面，对齐 /api/admin/* 契约）
 */
export const sidebarData: SidebarData = {
  user: {
    name: '管理员',
    email: 'admin@glint.red',
    avatar: '/avatars/shadcn.jpg',
  },
  teams: [
    {
      name: '库存监控后台',
      logo: Command,
      plan: 'Admin',
    },
  ],
  navGroups: [
    {
      title: '运营',
      items: [
        {
          title: '总览',
          url: '/',
          icon: LayoutDashboard,
        },
        {
          title: '流量',
          url: '/traffic',
          icon: LineChart,
        },
        {
          title: '会员',
          url: '/members',
          icon: Users,
        },
        {
          title: '付费',
          url: '/payments',
          icon: CreditCard,
        },
        {
          title: '系统状态',
          url: '/system',
          icon: ServerCog,
        },
        {
          title: '安全设置',
          url: '/totp-setup',
          icon: ShieldCheck,
        },
      ],
    },
  ],
}
