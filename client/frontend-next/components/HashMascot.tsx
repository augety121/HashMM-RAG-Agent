// HashMM 吉祥物「小哈」logo —— 与 App 端 Mascot 一致（深色头 + 红天线/围巾 + 白眼）。
// 用于欢迎区、侧栏、登录页等所有 logo 位置，统一品牌。
export default function HashMascot({ size = 56, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      aria-label="小哈"
    >
      {/* 天线 + 红点 */}
      <rect x="48" y="18" width="4" height="11" rx="2" fill="#ef3e36" />
      <circle cx="50" cy="15" r="4" fill="#ef3e36" />
      {/* 头 */}
      <rect x="27" y="27" width="46" height="46" rx="15" fill="#16161a" />
      {/* 腮红 */}
      <circle cx="35" cy="56" r="3" fill="#ef3e36" opacity="0.5" />
      <circle cx="65" cy="56" r="3" fill="#ef3e36" opacity="0.5" />
      {/* 眼睛 */}
      <circle cx="42.5" cy="47" r="5.4" fill="#ffffff" />
      <circle cx="57.5" cy="47" r="5.4" fill="#ffffff" />
      <circle cx="43.5" cy="47.8" r="2.8" fill="#16161a" />
      <circle cx="58.5" cy="47.8" r="2.8" fill="#16161a" />
      {/* 围巾 + 飘带 */}
      <rect x="30" y="73" width="40" height="9.5" rx="4.5" fill="#ef3e36" />
      <rect x="55" y="79" width="8" height="14" rx="3" fill="#ef3e36" />
    </svg>
  );
}
