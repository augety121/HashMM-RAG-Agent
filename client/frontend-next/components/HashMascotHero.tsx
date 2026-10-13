// Observer V4 hero: the production app tile is enlarged inside a restrained
// ambient field. Motion is decorative and disabled for reduced-motion users.
export default function HashMascotHero({ size = 176, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 200 200" fill="none" className={className}
      xmlns="http://www.w3.org/2000/svg" aria-label="HashMM Observer" role="img">
      <defs>
        <radialGradient id="hmV4Warm" cx="50%" cy="44%" r="50%">
          <stop offset="0%" stopColor="#ef4148" stopOpacity="0.15" />
          <stop offset="60%" stopColor="#ef4148" stopOpacity="0.04" />
          <stop offset="100%" stopColor="#ef4148" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="hmV4Neutral" cx="50%" cy="46%" r="52%">
          <stop offset="0%" stopColor="#18191c" stopOpacity="0.08" />
          <stop offset="100%" stopColor="#18191c" stopOpacity="0" />
        </radialGradient>
      </defs>
      <style>{`
        @keyframes hmV4Breathe { 0%,100% { opacity:.86; transform:scale(1) } 50% { opacity:1; transform:scale(1.035) } }
        @keyframes hmV4Spin { from { transform:rotate(0deg) } to { transform:rotate(360deg) } }
        .hmV4Glow { transform-box:fill-box; transform-origin:center; animation:hmV4Breathe 5s ease-in-out infinite; }
        .hmV4Orbit { transform-box:fill-box; transform-origin:100px 92px; animation:hmV4Spin 32s linear infinite; }
        @media (prefers-reduced-motion: reduce) { .hmV4Glow,.hmV4Orbit { animation:none; } }
      `}</style>
      <circle className="hmV4Glow" cx="100" cy="91" r="88" fill="url(#hmV4Neutral)" />
      <circle className="hmV4Glow" cx="100" cy="87" r="79" fill="url(#hmV4Warm)" style={{ animationDelay: "-2.5s" }} />
      <g className="hmV4Orbit" opacity=".75">
        <circle cx="100" cy="92" r="79" fill="none" stroke="#18191c" strokeOpacity=".07" strokeWidth="1.25" strokeDasharray="2 8" />
        <circle cx="100" cy="13" r="2.8" fill="#ef4148" />
        <circle cx="179" cy="92" r="2" fill="#18191c" opacity=".45" />
        <circle cx="21" cy="92" r="2" fill="#18191c" opacity=".38" />
      </g>
      <ellipse cx="100" cy="171" rx="43" ry="7" fill="#18191c" opacity=".09" />
      <g transform="translate(31 20) scale(.54)">
        <path d="M128 9C47 9 9 47 9 128s38 119 119 119 119-38 119-119S209 9 128 9Z" fill="#f4f3ef" />
        <path d="M128 68V47" stroke="#ef4148" strokeWidth="8" strokeLinecap="round" />
        <circle cx="128" cy="39" r="7" fill="#ef4148" />
        <path d="M128 67c-40 0-61 18-61 57s21 57 61 57 61-18 61-57-21-57-61-57Z" fill="#18191c" />
        <path d="M88 124c0-9 7-15 17-15s17 6 17 15-7 15-17 15-17-6-17-15Z" fill="#fff" />
        <path d="M136 123c0-10 8-17 18-17s18 7 18 17-8 17-18 17-18-7-18-17Z" fill="#fff" />
        <circle cx="110" cy="122" r="5.5" fill="#18191c" /><circle cx="159" cy="121" r="5.5" fill="#18191c" />
        <circle cx="112" cy="120" r="1.8" fill="#fff" /><circle cx="161" cy="119" r="1.8" fill="#fff" />
        <path d="M128 201h15v16c0 6-3 11-8 14-5-3-7-7-7-13Z" fill="#ef4148" />
        <rect x="73" y="188" width="110" height="18" rx="9" fill="#ef4148" />
      </g>
    </svg>
  );
}
