"use client";
/** LoginModal — V93 Marvis 式登录：居中遮罩弹窗。
 *  软件打开即主工作台（游客可浏览），发送/上传等操作或点「登录」时弹出；
 *  登录成功即关闭，原地继续。已登录则永不渲染。 */
import { useStore } from "@/lib/store";
import { LoginForm } from "./LoginForm";
import { X } from "lucide-react";

export function LoginModal() {
  const open = useStore(s => s.loginOpen);
  const token = useStore(s => s.token);
  const set = useStore(s => s.set);
  if (!open || token) return null;
  const close = () => set({ loginOpen: false });

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center anim-fade-up"
         style={{ background: "rgba(0,0,0,.4)", backdropFilter: "blur(2px)" }}
         onClick={close}>
      <div className="relative rounded-2xl px-8 py-9 w-[400px] max-w-[92vw]"
           style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", boxShadow: "var(--shadow-lg)" }}
           onClick={(e) => e.stopPropagation()}>
        <button onClick={close} className="absolute right-3.5 top-3.5 p-1 rounded-lg hover:opacity-70"
                style={{ color: "var(--text-tertiary)" }}><X size={16} /></button>
        <div className="flex justify-center">
          <LoginForm onSuccess={close} />
        </div>
      </div>
    </div>
  );
}
