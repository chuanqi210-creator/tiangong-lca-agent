"use client";

/**
 * 最简 toast：context + reducer，固定右上角堆叠，4s 自动消失。
 * 不持久化——刷新后 toast 状态丢失（瞬态反馈，不是历史记录）。
 * 与消息流 NoticeCard 双通道并存：NoticeCard 留在对话里，toast 做即时提醒。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useReducer,
  useRef,
} from "react";
import { cn } from "@/lib/utils";

export type ToastTone = "info" | "error" | "success";

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

type ToastAction =
  | { type: "push"; item: ToastItem }
  | { type: "dismiss"; id: number };

const MAX_TOASTS = 4;
const AUTO_DISMISS_MS = 4000;

function reducer(state: ToastItem[], action: ToastAction): ToastItem[] {
  switch (action.type) {
    case "push":
      return [...state, action.item].slice(-MAX_TOASTS);
    case "dismiss":
      return state.filter((t) => t.id !== action.id);
  }
}

interface ToastContextValue {
  toast: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue>({ toast: () => {} });

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}

const toneClass: Record<ToastTone, string> = {
  info: "border-border text-foreground",
  error: "border-destructive/60 text-destructive",
  success: "border-ring/60 text-ring",
};

function ToastView({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto w-72 rounded-md border bg-popover px-3 py-2 text-sm shadow-lg",
        toneClass[item.tone],
      )}
      onClick={onDismiss}
    >
      {item.message}
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, dispatch] = useReducer(reducer, []);
  const nextIdRef = useRef(0);

  const toast = useCallback((message: string, tone: ToastTone = "info") => {
    nextIdRef.current += 1;
    dispatch({ type: "push", item: { id: nextIdRef.current, message, tone } });
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="pointer-events-none fixed top-4 right-4 z-[100] flex flex-col items-end gap-2">
        {toasts.map((item) => (
          <ToastView
            key={item.id}
            item={item}
            onDismiss={() => dispatch({ type: "dismiss", id: item.id })}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
