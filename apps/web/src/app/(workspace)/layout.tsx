import { AppShell } from "@/components/layout/AppShell";

/**
 * 工作区路由组：/projects 与 /research/[researchId] 共用
 * 侧栏 + 主区外壳；落地页 / 不在此组内，保持全屏独立。
 */
export default function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
