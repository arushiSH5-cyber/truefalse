// The background. Fixed positions, so server and browser render the same.
const STAR = "M12 1.8l3 6.5 7.1.8-5.3 4.8 1.5 7-6.3-3.6-6.3 3.6 1.5-7L1.9 9.1 9 8.3Z";
const SPARK = "M12 0Q13.3 10.7 24 12Q13.3 13.3 12 24Q10.7 13.3 0 12Q10.7 10.7 12 0Z";
const COLORS = ["#eeb0bd", "#8a6a60", "#f0dfa6", "#b9a39b"];

const DOTS = Array.from({ length: 46 }, (_, i) => ({
  left: (i * 37 + 11) % 100,
  top: (i * 61 + 7) % 100,
  size: 9 + ((i * 7) % 12),
  color: COLORS[i % COLORS.length],
  tilt: (i * 29) % 50,
}));
const BLURRY = [
  [4, 16, 92, "#eeb0bd"],
  [82, 9, 70, "#6f5148"],
  [86, 56, 110, "#eeb0bd"],
  [8, 78, 78, "#6f5148"],
  [64, 88, 60, "#eeb0bd"],
] as const;

export default function Sky() {
  return (
    <div className="sky" aria-hidden="true">
      <div className="blob" style={{ right: -90, top: "6%" }} />
      <div className="blob" style={{ left: -120, top: "58%" }} />
      <div className="day-only">
        {BLURRY.map(([left, top, size, color]) => (
          <svg key={`${left}-${top}`} viewBox="0 0 24 24" width={size} height={size}
            style={{ left: `${left}%`, top: `${top}%`, filter: "blur(6px)", opacity: 0.45 }}>
            <path d={STAR} fill={color} />
          </svg>
        ))}
        {DOTS.map((d, i) => (
          <svg key={i} viewBox="0 0 24 24" width={d.size} height={d.size}
            style={{ left: `${d.left}%`, top: `${d.top}%`, transform: `rotate(${d.tilt}deg)`, opacity: 0.6 }}>
            <path d={STAR} fill={d.color} />
          </svg>
        ))}
      </div>
      <div className="night-only" style={{ color: "#d9cfc4" }}>
        <svg viewBox="0 0 400 400" width="380" height="380" fill="none" stroke="currentColor"
          style={{ left: -110, top: -90, opacity: 0.3 }}>
          <circle cx="200" cy="200" r="190" strokeDasharray="2 7" />
          <circle cx="200" cy="200" r="166" />
          <path d="M226 58a146 146 0 1 0 0 284a164 164 0 0 1 0-284Z" fill="currentColor" fillOpacity=".18" />
          <path d="M290 90v250M330 130v120" strokeDasharray="1 5" strokeWidth="2" />
          <circle cx="290" cy="352" r="11" fill="currentColor" />
          <circle cx="330" cy="260" r="6" fill="currentColor" />
        </svg>
        <svg viewBox="0 0 300 300" width="340" height="340" fill="none" stroke="currentColor"
          style={{ right: -130, bottom: -70, opacity: 0.25 }}>
          {[30, 62, 92, 120, 146].map((r) => (
            <circle key={r} cx="150" cy="150" r={r} />
          ))}
          <path d="M4 150h292M150 4v292M47 47l206 206M253 47L47 253" />
          <path d="M70 100l50-30 60 22 52 70" strokeDasharray="3 4" />
        </svg>
        {DOTS.map((d, i) => (
          <svg key={i} viewBox="0 0 24 24" width={d.size} height={d.size}
            style={{ left: `${d.left}%`, top: `${d.top}%`, opacity: 0.2 + (i % 4) * 0.12 }}>
            <path d={i % 3 ? SPARK : STAR} fill="currentColor" />
          </svg>
        ))}
      </div>
    </div>
  );
}
