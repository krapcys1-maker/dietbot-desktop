/** Original Porcja identity: a plate, a leaf and a warm dot. */
export default function Brand() {
  return <span className="brand">
    <svg className="brand-mark" width="42" height="42" viewBox="0 0 48 48" fill="none" aria-hidden="true">
      <rect width="48" height="48" rx="16" fill="#244D3D" />
      <circle cx="24" cy="25" r="14" stroke="#F9F5EA" strokeWidth="2" />
      <path d="M14 27c0-9 7-13 14-13 1 8-3 15-11 15l-3-2Z" fill="#C4D98C" />
      <path d="m17 27 7-8" stroke="#244D3D" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="31" cy="29" r="5" fill="#ECA67F" />
    </svg>
    <span><span className="brand-name">porcja<span>.</span></span><span className="brand-sub">Dobrze mieć plan.</span></span>
  </span>;
}
