import {
  AdditiveBlending, BufferAttribute, BufferGeometry, Camera, Points, Scene,
  ShaderMaterial, Vector2, WebGLRenderer,
} from 'three';
import { gsap } from 'gsap';
import { oceanGrid } from './ocean-grid';

const vertexShader = `
  uniform float time;
  uniform float pixelRatio;
  uniform vec2 pointer;
  varying float ink;
  varying float tint;
  void main() {
    float d = position.y;
    float s = position.z;
    float t = time * .65;
    float x = position.x * (.3 + d * 1.42);
    float crest = sin(x * 5.1 + d * 8.5 - t) * .10
      + cos(x * 9.2 - d * 12.5 + t * .71) * .064
      + sin(x * 17.0 - d * 18.0 + t * .56) * .018;
    float y = .12 + pow(d, 1.48) * .97 + crest * (.42 + d * .93);
    vec2 offset = vec2(x, y) - pointer;
    float wake = exp(-dot(offset, offset) * 12.0);
    y += wake * sin(length(offset) * 18.0 - t) * .031;
    gl_Position = vec4(x, 1.0 - y * 2.0, 0.0, 1.0);
    gl_PointSize = pixelRatio * (1.08 + d * 2.8) * (.64 + s * .59);
    float peak = smoothstep(-.085, .17, crest);
    ink = (.25 + d * .49) * (.50 + s * .49) * (.51 + peak * .65);
    tint = peak * .75 + wake * .25;
  }
`;
const fragmentShader = `
  varying float ink;
  varying float tint;
  void main() {
    float radius = length(gl_PointCoord - .5);
    float opacity = (1.0 - smoothstep(.11, .5, radius)) * ink;
    vec3 color = mix(vec3(.18, .72, .53), vec3(.69, 1.0, .88), tint);
    gl_FragColor = vec4(color, opacity);
  }
`;

export function mountOcean(container: HTMLElement, onLoss: () => void) {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('webgl2', { alpha: true, antialias: false, depth: false });
  if (!context) return null;
  let renderer: WebGLRenderer;
  try {
    renderer = new WebGLRenderer({ canvas, context, alpha: true, antialias: false });
  } catch {
    context.getExtension('WEBGL_lose_context')?.loseContext();
    return null;
  }
  const geometry = new BufferGeometry();
  const material = new ShaderMaterial({
    uniforms: {
      time: { value: 0 },
      pixelRatio: { value: 1 },
      pointer: { value: new Vector2(-9, -9) },
    },
    vertexShader,
    fragmentShader,
    depthTest: false,
    depthWrite: false,
    transparent: true,
    blending: AdditiveBlending,
  });
  const scene = new Scene();
  const camera = new Camera();
  const points = new Points(geometry, material);
  points.frustumCulled = false;
  scene.add(points);

  let compact: boolean | undefined;
  let active = false;
  let running = false;
  let visible = true;
  const pointer = new Vector2(-9, -9);
  function draw(_time = 0, elapsed = 0) {
    material.uniforms.time.value += Math.min(elapsed, 50) / 1000;
    material.uniforms.pointer.value.lerp(pointer, 1 - Math.exp(-elapsed / 85));
    renderer.render(scene, camera);
  }
  function resize() {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if ((width < 700) !== compact) {
      compact = width < 700;
      geometry.dispose();
      geometry.setAttribute('position', new BufferAttribute(oceanGrid(compact), 3));
    }
    const pixelRatio = Math.min(window.devicePixelRatio || 1, compact ? 1.25 : 1.5);
    material.uniforms.pixelRatio.value = pixelRatio;
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(width, height, false);
    draw();
  }
  function sync() {
    const shouldRun = active && visible && !document.hidden;
    if (shouldRun === running) return;
    running = shouldRun;
    if (running) gsap.ticker.add(draw);
    else gsap.ticker.remove(draw);
  }
  const hero = container.closest('section') ?? container;
  const move = (event: PointerEvent) => {
    if (event.pointerType === 'touch') return;
    const area = container.getBoundingClientRect();
    if (!area.width || !area.height) return;
    pointer.set(2 * (event.clientX - area.left) / area.width - 1, (event.clientY - area.top) / area.height);
  };
  const leave = () => pointer.set(-9, -9);
  const sizeObserver = new ResizeObserver(resize);
  const visibilityObserver = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    sync();
  });
  function loseContext() {
    // Switch to the static scene and release the lost renderer.
    onLoss();
  }

  // Render before swapping out the static scene.
  try {
    resize();
  } catch {
    geometry.dispose();
    material.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    return null;
  }
  container.append(canvas);
  sizeObserver.observe(container);
  visibilityObserver.observe(container);
  hero.addEventListener('pointermove', move);
  hero.addEventListener('pointerleave', leave);
  document.addEventListener('visibilitychange', sync);
  canvas.addEventListener('webglcontextlost', loseContext);
  return {
    setActive(value: boolean) { active = value; sync(); },
    dispose() {
      active = false;
      sync();
      sizeObserver.disconnect();
      visibilityObserver.disconnect();
      hero.removeEventListener('pointermove', move);
      hero.removeEventListener('pointerleave', leave);
      document.removeEventListener('visibilitychange', sync);
      canvas.removeEventListener('webglcontextlost', loseContext);
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      if (!context.isContextLost()) renderer.forceContextLoss();
      canvas.remove();
    },
  };
}

