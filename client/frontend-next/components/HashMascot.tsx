// HashMM Observer V4 brand mark. Functional icons keep their own semantics;
// this component is reserved for product identity, assistant avatars and About.
export default function HashMascot({ size = 56, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      aria-label="HashMM Observer"
      role="img"
    >
      <path d="M32 17V11.75" stroke="#ef4148" strokeWidth="2" strokeLinecap="round" />
      <circle cx="32" cy="9.75" r="1.75" fill="#ef4148" />
      <path d="M32 16.75c-10 0-15.25 4.5-15.25 14.25S22 45.25 32 45.25 47.25 40.75 47.25 31 42 16.75 32 16.75Z" fill="#18191c" />
      <ellipse cx="26.25" cy="31" rx="4.25" ry="3.75" fill="#fff" />
      <ellipse cx="38.5" cy="30.75" rx="4.5" ry="4.25" fill="#fff" />
      <circle cx="27.5" cy="30.5" r="1.375" fill="#18191c" />
      <circle cx="39.75" cy="30.25" r="1.375" fill="#18191c" />
      <circle cx="28" cy="30" r=".45" fill="#fff" />
      <circle cx="40.25" cy="29.75" r=".45" fill="#fff" />
      <path d="M32 50.25h3.75v4c0 1.5-.75 2.75-2 3.5-1.25-.75-1.75-1.75-1.75-3.25Z" fill="#ef4148" />
      <rect x="18.25" y="47" width="27.5" height="4.5" rx="2.25" fill="#ef4148" />
    </svg>
  );
}
