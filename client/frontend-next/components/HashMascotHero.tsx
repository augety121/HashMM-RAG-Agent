// HashMM 首页 hero 版吉祥物「小哈」——在原 Mascot 基础上做了氛围增强：
//   柔光（红/中性双层径向光）+ 极淡轨道环 + 漂浮的品牌点 + 地面投影。
// 目的：首页不再是"一个孤零零的小图标"，而是有呼吸感、有层次的品牌主视觉。
// 仍是纯 SVG、无文字、可任意缩放；保持 #16161A 近黑 + #EF3E36 红的品牌色。
// 注：普通 logo 位（侧栏/登录/气泡）继续用轻量的 HashMascot，本组件只用于首页空态。
export default function HashMascotHero({ size = 150, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 200 200"
      fill="none"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      aria-label="小哈"
      role="img"
    >
      <defs>
        <radialGradient id="hmHeroGlowWarm" cx="50%" cy="44%" r="50%">
          <stop offset="0%" stopColor="#ef3e36" stopOpacity="0.18" />
          <stop offset="55%" stopColor="#ef3e36" stopOpacity="0.05" />
          <stop offset="100%" stopColor="#ef3e36" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="hmHeroGlowNeutral" cx="50%" cy="46%" r="52%">
          <stop offset="0%" stopColor="#16161a" stopOpacity="0.10" />
          <stop offset="70%" stopColor="#16161a" stopOpacity="0.03" />
          <stop offset="100%" stopColor="#16161a" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="hmHeroHead" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#1f1f25" />
          <stop offset="100%" stopColor="#121216" />
        </linearGradient>
      </defs>

      <style>{`
        @keyframes hmHeroBreathe { 0%,100% { opacity:.85; transform:scale(1) } 50% { opacity:1; transform:scale(1.05) } }
        @keyframes hmHeroSpin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
        .hmHeroGlow { transform-box: fill-box; transform-origin: center; animation: hmHeroBreathe 4.5s ease-in-out infinite; }
        .hmHeroOrbit { transform-box: fill-box; transform-origin: 100px 92px; animation: hmHeroSpin 26s linear infinite; }
        @media (prefers-reduced-motion: reduce) {
          .hmHeroGlow, .hmHeroOrbit { animation: none; }
        }
      `}</style>

      {/* 双层柔光（呼吸） */}
      <circle className="hmHeroGlow" cx="100" cy="90" r="84" fill="url(#hmHeroGlowNeutral)" />
      <circle className="hmHeroGlow" cx="100" cy="86" r="76" fill="url(#hmHeroGlowWarm)" style={{ animationDelay: "-2.2s" }} />

      {/* 极淡轨道环 + 环上的品牌点（缓慢旋转） */}
      <g className="hmHeroOrbit">
        <circle cx="100" cy="92" r="74" fill="none" stroke="#16161a" strokeOpacity="0.07" strokeWidth="1.5" strokeDasharray="2 7" />
        <circle cx="100" cy="18" r="3.2" fill="#ef3e36" />
        <circle cx="174" cy="92" r="2.4" fill="#16161a" opacity="0.5" />
        <circle cx="100" cy="166" r="2.2" fill="#ef3e36" opacity="0.7" />
        <circle cx="26" cy="92" r="2.4" fill="#16161a" opacity="0.45" />
      </g>

      {/* 地面投影 */}
      <ellipse cx="100" cy="168" rx="46" ry="8.5" fill="#16161a" opacity="0.10" />

      {/* —— 小哈本体（沿用原 Mascot 几何，等比放进 200 画布并居中：translate(52,44)·scale(0.96)）—— */}
      <g transform="translate(52,44) scale(0.96)">
        {/* 天线 + 红点 */}
        <rect x="48" y="18" width="4" height="11" rx="2" fill="#ef3e36" />
        <circle cx="50" cy="15" r="4" fill="#ef3e36" />
        {/* 头 */}
        <rect x="27" y="27" width="46" height="46" rx="15" fill="url(#hmHeroHead)" />
        {/* 腮红 */}
        <circle cx="35" cy="56" r="3" fill="#ef3e36" opacity="0.5" />
        <circle cx="65" cy="56" r="3" fill="#ef3e36" opacity="0.5" />
        {/* 眼睛 + 高光 */}
        <circle cx="42.5" cy="47" r="5.4" fill="#ffffff" />
        <circle cx="57.5" cy="47" r="5.4" fill="#ffffff" />
        <circle cx="43.5" cy="47.8" r="2.8" fill="#16161a" />
        <circle cx="58.5" cy="47.8" r="2.8" fill="#16161a" />
        <circle cx="44.7" cy="45.6" r="0.9" fill="#ffffff" />
        <circle cx="59.7" cy="45.6" r="0.9" fill="#ffffff" />
        {/* 围巾 + 飘带 */}
        <rect x="30" y="73" width="40" height="9.5" rx="4.5" fill="#ef3e36" />
        <rect x="55" y="79" width="8" height="14" rx="3" fill="#ef3e36" />
      </g>
    </svg>
  );
}
