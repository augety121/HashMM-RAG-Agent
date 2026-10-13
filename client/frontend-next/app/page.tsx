"use client";
import dynamic from "next/dynamic";

const App = dynamic(() => import("@/components/App"), {
  ssr: false,
  loading: () => (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", background: "#09090b" }}>
      <div style={{ textAlign: "center" }}>
        <div style={{ width: 40, height: 40, borderRadius: 12, background: "linear-gradient(135deg, #2563eb, #7c3aed)", margin: "0 auto 12px", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <span style={{ color: "#fff", fontWeight: 700, fontSize: 16 }}>H</span>
        </div>
        <div style={{ color: "#52525b", fontSize: 13, fontFamily: "system-ui" }}>加载中...</div>
      </div>
    </div>
  ),
});

export default function Page() {
  return <App />;
}
