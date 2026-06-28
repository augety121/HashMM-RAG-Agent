/**
 * lib/artifact.ts — 右侧 Artifact 面板的统一入口（V50）
 *
 * 背景：此前打开右栏的代码散落三处（MsgBubble 两处 + ChatArea 文档自动打开），
 * 而流式区的文件 chip 走的是另一条旧弹窗路径，且面板状态不带会话归属——
 * 新开会话右栏还挂着上个会话的文件。统一收口：
 *   openArtifact(convId, f)        —— 唯一打开入口，自动推断类型、记录归属会话
 *   closeArtifactIfNotConv(convId) —— 会话切换钩子（App.tsx 对 sid 变化调用）
 */
import { useStore } from "@/lib/store";

export interface ArtifactFile {
  filename: string;
  download_url: string;
  pages?: number;
  outline?: string[];
}

/** 归档/不可面板预览的类型（保持下载入口，不开右栏） */
const NON_PREVIEWABLE = new Set(["zip", "gz", "tar", "7z", "rar", "pdf"]);

export function artifactTypeOf(filename: string): string {
  const ext = (filename || "").split(".").pop()?.toLowerCase() || "";
  if (["pptx", "docx", "xlsx", "html"].includes(ext)) return ext;
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return "image";
  return "code";
}

export function isPanelPreviewable(filename: string): boolean {
  const ext = (filename || "").split(".").pop()?.toLowerCase() || "";
  return !NON_PREVIEWABLE.has(ext);
}

/** 打开右栏（带会话归属）。不可预览类型直接忽略（调用方应只对 previewable 文件绑定）。 */
export function openArtifact(convId: string | null | undefined, f: ArtifactFile): void {
  if (!isPanelPreviewable(f.filename)) return;
  useStore.getState().set({
    artifactPanel: {
      convId: convId || "",
      type: artifactTypeOf(f.filename),
      filename: f.filename,
      download_url: f.download_url,
      pages: f.pages,
      outline: f.outline,
    },
  });
}

/** 会话切换/新建时调用：右栏里挂的不是当前会话的文件 → 关闭。 */
export function closeArtifactIfNotConv(convId: string | null | undefined): void {
  const ap = useStore.getState().artifactPanel as { convId?: string } | null;
  if (ap && (ap.convId || "") !== (convId || "")) {
    useStore.getState().set({ artifactPanel: null });
  }
}
