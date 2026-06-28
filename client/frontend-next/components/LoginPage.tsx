"use client";
/** LoginPage — web 直链场景的全屏登录（内核在 LoginForm）。 */
import { LoginForm } from "./LoginForm";

export function LoginPage() {
  return (
    <div className="flex h-screen w-screen items-center justify-center px-6" style={{ background: "var(--bg-primary)" }}>
      <LoginForm />
    </div>
  );
}
