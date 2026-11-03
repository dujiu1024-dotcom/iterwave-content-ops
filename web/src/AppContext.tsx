import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import { api, AuthStatus, SystemStatus, Platform } from "./api";

interface Toast { id: number; text: string; type?: "info" | "success" | "error"; }

// 长耗时操作的中文名：超过 4 秒仍未完成时，右下角会显示“正在做什么 + 已用时长”，
// 避免用户面对一个无响应的按钮干等。未登记的 key 统一显示“正在处理”。
const PENDING_LABELS: Record<string, string> = {
  "regen-cards": "AI 重新生成卡片",
  "media:generate-for-content": "AI 生成内容卡片",
  "media:upload": "上传素材",
  "tenant:backup": "备份租户数据",
  "tenant:restore": "恢复租户备份",
  "autogrow:run": "执行自增长策略",
  "diag": "运行系统诊断",
  "plan-suggest": "生成母计划建议",
  "cfg:kb": "同步选题库",
  "delete-content": "删除内容",
  "delete-round": "删除轮次",
};

const SLOW_THRESHOLD_SECONDS = 4;

interface AppContextType {
  status: SystemStatus | null;
  refreshStatus: () => Promise<void>;
  /** 访问保护状态：null = 检测中（首屏不闪「未登录」） */
  auth: AuthStatus | null;
  refreshAuth: () => Promise<void>;
  toasts: Toast[];
  showToast: (text: string, type?: Toast["type"]) => void;
  /** 手动关闭某个 toast */
  dismissToast: (id: number) => void;
  /** G-11：进行中的操作集合（key 由调用方定义，如 "publish:12" / "run:3"） */
  pending: Record<string, boolean>;
  /** G-11：包一层 pending 标记 + 防重复触发，返回 fn 的返回值；同一 key 执行中再次调用直接返回 undefined */
  withPending: <T>(key: string, fn: () => Promise<T>) => Promise<T | undefined>;
  isPending: (key: string) => boolean;
}

const AppContext = createContext<AppContextType | null>(null);

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("AppContext missing");
  return ctx;
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  /** 超过阈值仍未完成的操作用绝对计时展示，而不是一直转圈 */
  const [slowPending, setSlowPending] = useState<{ key: string; label: string; seconds: number } | null>(null);
  const pendingSince = useRef<Record<string, number>>({});

  const refreshAuth = useCallback(async () => {
    try {
      setAuth(await api.authStatus());
    } catch {
      // Authentication state is represented in the UI; do not dump raw errors to the browser console.
      setAuth({ configured: true, authenticated: false, needs_login: true });
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await api.status();
      setStatus(s);
    } catch {
      // Keep the last known status without exposing raw response details in the console.
    }
  }, []);

  const showToast = useCallback((text: string, type: Toast["type"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, text, type }]);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // 用 ref 做同步的"是否已在执行"判断：setState 更新是异步的，不能靠它去重
  const inflight = useRef<Set<string>>(new Set());

  const isPending = useCallback((key: string) => pending[key] === true, [pending]);

  const withPending = useCallback(async <T,>(key: string, fn: () => Promise<T>): Promise<T | undefined> => {
    if (inflight.current.has(key)) return undefined;
    inflight.current.add(key);
    pendingSince.current[key] = Date.now();
    setPending((prev) => ({ ...prev, [key]: true }));
    try {
      return await fn();
    } finally {
      inflight.current.delete(key);
      delete pendingSince.current[key];
      setPending((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setSlowPending((prev) => (prev?.key === key ? null : prev));
    }
  }, []);

  // 每 1s 采样：只展示最久的一个长任务，少于 4s 的快操作不会打扰用户
  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = Date.now();
      let longest: { key: string; label: string; seconds: number } | null = null;
      for (const [key, startedAt] of Object.entries(pendingSince.current)) {
        const seconds = Math.max(0, Math.round((now - startedAt) / 1000));
        if (seconds < SLOW_THRESHOLD_SECONDS) continue;
        if (!longest || seconds > longest.seconds) {
          longest = { key, label: PENDING_LABELS[key] ?? "正在处理", seconds };
        }
      }
      setSlowPending(longest);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  useEffect(() => {
    if (!auth?.authenticated) return;
    refreshStatus();
    const t = setInterval(refreshStatus, 15000);
    return () => clearInterval(t);
  }, [auth?.authenticated, refreshStatus]);

  return (
    <AppContext.Provider value={{ status, refreshStatus, auth, refreshAuth, toasts, showToast, dismissToast, pending, withPending, isPending }}>
      {children}
      <div className="toast-area" role="status" aria-live="polite">
        {slowPending && (
          <div className="toast info">
            ◷ {slowPending.label} · 已用 {slowPending.seconds >= 60
              ? `${Math.floor(slowPending.seconds / 60)} 分 ${slowPending.seconds % 60} 秒`
              : `${slowPending.seconds} 秒`}
          </div>
        )}
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type || "info"}`}>
            <b>{t.type === "error" ? "需要处理" : t.type === "success" ? "已完成" : "提示"}</b>
            <span>{t.text}</span>
            <button
              type="button"
              className="toast-close"
              aria-label="关闭"
              onClick={() => dismissToast(t.id)}
              title="点击关闭"
            >✕</button>
          </div>
        ))}
      </div>
    </AppContext.Provider>
  );
}
