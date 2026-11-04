/** 全局状态：路由、主题、提示、跨页面草稿。数据本身各页面自己拉（本地 SQLite 很快）。 */

import { create } from "zustand";

export type Route = "hot" | "create" | "publish" | "profile" | "settings";
export type ToastLevel = "info" | "ok" | "warn" | "error";

export interface CreationPlatform {
  key: string;
  label: string;
  content_type: string;
}

export interface PublishPlatform {
  key: string;
  label: string;
  available: boolean;
  needs_media: boolean;
  limits: Record<string, unknown>;
}

export interface Meta {
  version: string;
  theme: string;
  llm_ready: boolean;
  hot_platforms: { key: string; label: string }[];
  creation_platforms: CreationPlatform[];
  publish_platforms: PublishPlatform[];
}

export interface PublishDraft {
  platform: string;
  title: string;
  titles: string[];
  body: string;
  tags: string[];
  summary: string;
}

interface Toast {
  id: number;
  text: string;
  level: ToastLevel;
}

interface Store {
  route: Route;
  theme: "light" | "dark";
  meta: Meta | null;
  toasts: Toast[];
  /** 顶栏状态灯：busy / ok / warn / error / idle */
  status: { text: string; level: "idle" | "busy" | "ok" | "warn" | "error" };
  /** 热点页双击后带过来的主题，创作页读一次即清空 */
  pendingTopic: string;
  publishDraft: PublishDraft | null;
  setRoute: (route: Route) => void;
  setTheme: (theme: "light" | "dark") => void;
  setMeta: (meta: Meta) => void;
  setStatus: (text: string, level?: Store["status"]["level"]) => void;
  toast: (text: string, level?: ToastLevel) => void;
  dismiss: (id: number) => void;
  setPendingTopic: (topic: string) => void;
  consumePendingTopic: () => string;
  setPublishDraft: (draft: PublishDraft) => void;
}

let toastId = 0;

export const useStore = create<Store>((set, get) => ({
  route: "hot",
  theme: "light",
  meta: null,
  toasts: [],
  status: { text: "启动中…", level: "busy" },
  pendingTopic: "",
  publishDraft: null,

  setRoute: (route) => {
    set({ route });
    // 状态灯是全局的：切页先归位，避免上一页的「正在拉取…」跟过来
    set({ status: { text: "就绪", level: "idle" } });
  },
  setTheme: (theme) => {
    document.documentElement.dataset.theme = theme;
    set({ theme });
  },
  setMeta: (meta) => set({ meta }),
  setStatus: (text, level = "idle") => set({ status: { text, level } }),

  toast: (text, level = "info") => {
    const id = ++toastId;
    set({ toasts: [...get().toasts, { id, text, level }] });
    window.setTimeout(() => get().dismiss(id), 3600);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),

  setPendingTopic: (topic) => set({ pendingTopic: topic }),
  consumePendingTopic: () => {
    const topic = get().pendingTopic;
    if (topic) set({ pendingTopic: "" });
    return topic;
  },
  setPublishDraft: (draft) => set({ publishDraft: draft }),
}));
