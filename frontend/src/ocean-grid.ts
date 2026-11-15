export function oceanGrid(compact: boolean) {
  const columns = compact ? 112 : 186;
  const rows = compact ? 100 : 148;
  const points = new Float32Array(columns * rows * 3);
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const index = (row * columns + column) * 3;
      points[index] = 2 * column / (columns - 1) - 1;
      points[index + 1] = row / (rows - 1);
      points[index + 2] = ((row * 83 + column * 29) % 101) / 100;
    }
  }
  return points;
}

export function drawStill(container: HTMLElement) {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) return;
  container.append(canvas);
  const observer = new ResizeObserver(() => {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (!width || !height) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = getComputedStyle(container).getPropertyValue('--mint').trim();
    const points = oceanGrid(width < 700);
    for (let i = 0; i < points.length; i += 3) {
      const u = points[i];
      const depth = points[i + 1];
      const seed = points[i + 2];
      const x = u * (.3 + depth * 1.42);
      const crest = Math.sin(x * 5.1 + depth * 8.5) * .10
        + Math.cos(x * 9.2 - depth * 12.5) * .064
        + Math.sin(x * 17 - depth * 18) * .018;
      const y = .12 + depth ** 1.48 * .97 + crest * (.42 + depth * .93);
      const radius = (.42 + depth * .98) * (.55 + seed * .55);
      const light = (.2 + depth * .48) * (.55 + seed * .42);
      context.globalAlpha = light;
      context.beginPath();
      context.arc((x * .5 + .5) * width, y * height, radius, 0, Math.PI * 2);
      context.fill();
    }
  });
  observer.observe(container);
  return () => { observer.disconnect(); canvas.remove(); };
}

