"use client";
import React from "react";
import { AlertCircle, RefreshCw, Home } from "lucide-react";

interface State { hasError: boolean; error: Error | null; }

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Report to server (best effort)
    try {
      const token = typeof localStorage !== "undefined" ? localStorage.getItem("hmm_token") : null;
      fetch("/api/client-errors", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          error: error.message,
          stack: error.stack?.slice(0, 500),
          component: info.componentStack?.slice(0, 300),
          url: typeof window !== "undefined" ? window.location.href : "",
          ts: Date.now(),
        }),
      }).catch(() => {});
    } catch (_e) { /* best effort */ }
  }

  render() {
    if (this.state.hasError) {
      const msg = this.state.error?.message || "未知错误";
      const isNetwork = msg.includes("fetch") || msg.includes("network") || msg.includes("Failed");

      return (
        <div className="error-boundary">
          <div className="error-boundary-icon">
            <AlertCircle size={28} />
          </div>
          <div className="error-boundary-title">
            {isNetwork ? "连接中断" : "出了点问题"}
          </div>
          <div className="error-boundary-msg">
            {isNetwork
              ? "无法连接到服务器，请检查网络连接后重试。"
              : `应用遇到了一个意外错误。如果问题持续出现，请尝试刷新页面或联系管理员。`}
          </div>
          {!isNetwork && (
            <pre style={{
              fontSize: 11, color: "#ef4444", background: "#fef2f2",
              padding: "8px 16px", borderRadius: 8, maxWidth: 500,
              overflow: "auto", maxHeight: 100, textAlign: "left",
            }}>
              {msg}
            </pre>
          )}
          <div className="error-boundary-actions">
            <button
              className="error-boundary-btn error-boundary-btn-primary"
              onClick={() => this.setState({ hasError: false, error: null })}
            >
              <RefreshCw size={14} style={{ display: "inline", marginRight: 6, verticalAlign: -2 }} />
              重试
            </button>
            <button
              className="error-boundary-btn error-boundary-btn-secondary"
              onClick={() => window.location.reload()}
            >
              <Home size={14} style={{ display: "inline", marginRight: 6, verticalAlign: -2 }} />
              重新加载
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
