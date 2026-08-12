// React component with inline SVG replacing the brand dot
export const EdgeSentinelBrand = () => {
  return (
    <div className="app-shell__brand">
      <svg 
        className="app-shell__brand-logo" 
        viewBox="0 0 100 100" 
        fill="none" 
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="esGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#00F2FE" />
            <stop offset="100%" stopColor="#4FACFE" />
          </linearGradient>
        </defs>
        <path 
          d="M 50 10 L 84 24 V 48 C 84 70 50 90 50 90 C 50 90 16 70 16 48 V 24 L 50 10 Z" 
          stroke="url(#esGradient)" 
          strokeWidth="5" 
          strokeLinecap="round" 
          strokeLinejoin="round"
        />
        <path 
          d="M 36 36 H 48 M 36 50 H 45 M 36 64 H 48 M 36 36 V 64" 
          stroke="url(#esGradient)" 
          strokeWidth="4" 
          strokeLinecap="round" 
          strokeLinejoin="round"
        />
        <path 
          d="M 64 36 H 54 V 50 H 64 V 64 H 54" 
          stroke="url(#esGradient)" 
          strokeWidth="4" 
          strokeLinecap="round" 
          strokeLinejoin="round"
        />
        <circle cx="50" cy="10" r="4" fill="#00F2FE" />
        <circle cx="84" cy="24" r="4" fill="#00F2FE" />
        <circle cx="16" cy="24" r="4" fill="#00F2FE" />
        <circle cx="50" cy="90" r="4" fill="#4FACFE" />
      </svg>
      
      <div>
        <div className="app-shell__brand-title">EdgeSentinel</div>
      </div>
    </div>
  );
};