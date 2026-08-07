// Pixel-art SVG icons extracted verbatim from monolayer.dev HTML.
// All are currentColor-filled; style via font-size / color on parents.

export function LogoNav(props) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 96 18" fill="none" aria-hidden="true" className="monolayer__logo is--nav" {...props}>
      <path d="M23.1344 4.84616H0V8.30769H23.1344V4.84616Z" fill="currentColor" />
      <path d="M23.1344 9.69231H0V13.1538H23.1344V9.69231Z" fill="currentColor" />
      <path d="M23.1344 14.5385H0V18H23.1344V14.5385Z" fill="currentColor" />
      <path d="M20.0636 0H3.0708V3.46154H20.0636V0Z" fill="currentColor" />
      <path d="M47.4017 4.84615H24.2675V8.30769H47.4017V4.84615Z" fill="currentColor" />
      <path d="M47.4017 9.69231H24.2675V13.1538H47.4017V9.69231Z" fill="currentColor" />
      <path d="M43.1401 2.16114e-06H28.5291V3.46154H43.1401V2.16114e-06Z" fill="currentColor" />
      <path d="M43.1401 14.5385H28.5291V18H43.1401V14.5385Z" fill="currentColor" />
      <path d="M71.669 4.84615H48.5348V8.30769H71.669V4.84615Z" fill="currentColor" />
      <path d="M71.669 9.69231H48.5348V13.1538H71.669V9.69231Z" fill="currentColor" />
      <path d="M71.669 14.5385H61.3398V18H71.669V14.5385Z" fill="currentColor" />
      <path d="M58.864 2.16114e-06H48.5348V3.46154H58.864V2.16114e-06Z" fill="currentColor" />
      <path d="M54.9905 14.5385H48.5348V18H54.9905V14.5385Z" fill="currentColor" />
      <path d="M71.669 2.16114e-06H65.2132V3.46154H71.669V2.16114e-06Z" fill="currentColor" />
      <path d="M95.9363 4.84615H72.802V8.30769H95.9363V4.84615Z" fill="currentColor" />
      <path d="M95.9363 9.69231H72.802V13.1538H95.9363V9.69231Z" fill="currentColor" />
      <path d="M91.6747 2.16114e-06H77.0636V3.46154H91.6747V2.16114e-06Z" fill="currentColor" />
      <path d="M91.6747 14.5385H77.0636V18H91.6747V14.5385Z" fill="currentColor" />
    </svg>
  );
}

export function LogoFooter(props) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 560 105" fill="none" aria-hidden="true" className="monolayer__logo is--footer" {...props}>
      <path d="M134.807 28.2391H0V48.4099H134.807V28.2391Z" fill="currentColor" />
      <path d="M134.807 56.4782H0V76.6489H134.807V56.4782Z" fill="currentColor" />
      <path d="M134.807 84.7172H0V104.888H134.807V84.7172Z" fill="currentColor" />
      <path d="M116.913 0H17.8939V20.1708H116.913V0Z" fill="currentColor" />
      <path d="M276.215 28.2391H141.409V48.4098H276.215V28.2391Z" fill="currentColor" />
      <path d="M276.215 56.4781H141.409V76.6489H276.215V56.4781Z" fill="currentColor" />
      <path d="M251.382 1.25932e-05H166.242V20.1708H251.382V1.25932e-05Z" fill="currentColor" />
      <path d="M251.382 84.7172H166.242V104.888H251.382V84.7172Z" fill="currentColor" />
      <path d="M417.623 28.2391H282.818V48.4098H417.623V28.2391Z" fill="currentColor" />
      <path d="M417.623 56.4781H282.818V76.6489H417.623V56.4781Z" fill="currentColor" />
      <path d="M417.623 84.7172H357.434V104.888H417.623V84.7172Z" fill="currentColor" />
      <path d="M343.007 1.25932e-05H282.818V20.1708H343.007V1.25932e-05Z" fill="currentColor" />
      <path d="M320.436 84.7172H282.818V104.888H320.436V84.7172Z" fill="currentColor" />
      <path d="M417.623 1.25932e-05H380.005V20.1708H417.623V1.25932e-05Z" fill="currentColor" />
      <path d="M559.031 28.2391H424.226V48.4098H559.031V28.2391Z" fill="currentColor" />
      <path d="M559.031 56.4781H424.226V76.6489H559.031V56.4781Z" fill="currentColor" />
      <path d="M534.199 1.25932e-05H449.058V20.1708H534.199V1.25932e-05Z" fill="currentColor" />
      <path d="M534.199 84.7172H449.058V104.888H534.199V84.7172Z" fill="currentColor" />
    </svg>
  );
}

// 14×14 diagonal pixel arrow ↗ (26 rects, verbatim)
export function ArrowIcon({ className = "arrow-icon", ...props }) {
  const rects = [
    [6, 0], [9, 0], [12, 0],
    [0, 3], [3, 3], [6, 3], [9, 3], [12, 3],
    [0, 6], [3, 6], [6, 6], [9, 6], [12, 6],
    [0, 9], [3, 9], [6, 9], [9, 9], [12, 9],
    [0, 12], [3, 12], [6, 12], [9, 12], [12, 12],
    [3, 0], [0, 0],
  ];
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 14 14" fill="none" className={className} {...props}>
      {rects.map(([x, y], i) => (
        <rect key={i} x={x} y={y} width="2" height="2" fill="currentColor" />
      ))}
    </svg>
  );
}

// 6×6 L-corner cropmark (rotated for 4 corners)
export function Cropmark({ corner = "top-left", ...props }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 6 6" fill="none" className={`btn-tabs__cropmark ${corner}`} {...props}>
      <path d="M0.5 5.5V0.5H4.5H5.5" stroke="currentColor" />
    </svg>
  );
}

export function CropmarkSet() {
  return (
    <>
      <Cropmark corner="btm-right" />
      <Cropmark corner="btm-left" />
      <Cropmark corner="top-right" />
      <Cropmark corner="top-left" />
    </>
  );
}

// modal close X
export function ModalCloseIcon(props) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 24 24" fill="none" className="modal-close__icon" {...props}>
      <path d="M19.7339 3.00047L21.0039 4.27047L4.27391 21.0005L3.00391 19.7305L19.7339 3.00047Z" fill="currentColor" />
      <path d="M21.0034 19.73L19.7334 21L3.00344 4.27L4.27344 3L21.0034 19.73Z" fill="currentColor" />
    </svg>
  );
}

// 17×17 pixel "enter" key icon
export function EnterIcon(props) {
  const rects = [
    [3, 7.5], [6, 7.5], [9, 7.5], [12, 7.5],
    [0, 4.5], [0, 1.5], [15, 7.5], [12, 4.5], [12, 10.5], [9, 1.5], [9, 13.5],
  ];
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 17 17" fill="none" className="enter-icon" {...props}>
      {rects.map(([x, y], i) => (
        <rect key={i} x={x} y={y} width="2" height="2" fill="currentColor" />
      ))}
    </svg>
  );
}
