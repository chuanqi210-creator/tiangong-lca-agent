"use client";

/**
 * 工作区外壳：左侧 AppSidebar + 右侧主区。
 * ≥768px 固定两栏；<768px 侧栏隐藏，经 useAppShell().openSidebar()
 * （顶栏汉堡按钮）唤出 overlay 抽屉。断点策略参考 palot sidebar-layout。
 */
import { usePathname } from "next/navigation";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { cn } from "@/lib/utils";
import { ToastProvider } from "@/components/ui/toast";
import { AppSidebar } from "./AppSidebar";

interface AppShellContextValue {
  openSidebar: () => void;
}

const AppShellContext = createContext<AppShellContextValue>({
  openSidebar: () => {},
});

export function useAppShell(): AppShellContextValue {
  return useContext(AppShellContext);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const pathname = usePathname();

  const contextValue = useMemo<AppShellContextValue>(
    () => ({ openSidebar: () => setDrawerOpen(true) }),
    [],
  );

  // 路由变化后自动收起抽屉（渲染期间调整状态，避免级联 effect）
  const [lastPathname, setLastPathname] = useState(pathname);
  if (lastPathname !== pathname) {
    setLastPathname(pathname);
    setDrawerOpen(false);
  }

  // Esc 关闭抽屉
  useEffect(() => {
    if (!drawerOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

  return (
    <AppShellContext.Provider value={contextValue}>
      <ToastProvider>
        <div className="flex h-dvh bg-background text-foreground">
        {/* 桌面侧栏 */}
        <aside className="hidden w-64 shrink-0 border-r border-sidebar-border md:block">
          <AppSidebar />
        </aside>

        {/* 主区：自身滚动，子页面用 h-dvh 撑满 */}
        <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>

        {/* 移动端抽屉 */}
        <div
          className={cn(
            "fixed inset-0 z-50 md:hidden",
            !drawerOpen && "pointer-events-none",
          )}
          aria-hidden={!drawerOpen}
        >
          <div
            className={cn(
              "absolute inset-0 bg-black/50 transition-opacity duration-200",
              drawerOpen ? "opacity-100" : "opacity-0",
            )}
            onClick={() => setDrawerOpen(false)}
          />
          <aside
            className={cn(
              "absolute inset-y-0 left-0 w-72 max-w-[85vw] border-r border-sidebar-border transition-transform duration-200 ease-out",
              drawerOpen ? "translate-x-0" : "-translate-x-full",
            )}
          >
            <AppSidebar />
          </aside>
        </div>
        </div>
      </ToastProvider>
    </AppShellContext.Provider>
  );
}
