import { useEffect, useRef } from 'react';

/** Decorative point field; it never represents incident or location data. */
export function SignalField() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let visible = true;
    let width = 0;
    let height = 0;
    const draw = (time: number) => {
      if (!visible || document.hidden) { frame = 0; return; }
      context.clearRect(0, 0, width, height);
      const phase = motion.matches ? 0 : time / 2800;
      for (let row = 0; row < 24; row++) {
        for (let col = 0; col < 58; col++) {
          const x = col / 57 * width;
          const wave = Math.sin(col * .1 + phase + row * .13) * 25 + Math.cos(col * .055 - phase) * 20;
          const y = height * .18 + row * height / 35 + wave * (row / 24 + .3);
          const alpha = (.16 + row / 40) * Math.min(1, col / 12);
          context.fillStyle = `rgba(55, 66, 47, ${alpha * .6})`;
          context.beginPath();
          context.arc(x, y, .7 + row / 38, 0, Math.PI * 2);
          context.fill();
        }
      }
      frame = motion.matches ? 0 : requestAnimationFrame(draw);
    };
    const start = () => { if (!frame && visible && !document.hidden) frame = requestAnimationFrame(draw); };
    const resize = new ResizeObserver(([entry]) => {
      width = entry.contentRect.width;
      height = entry.contentRect.height;
      const scale = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = width * scale;
      canvas.height = height * scale;
      context.setTransform(scale, 0, 0, scale, 0, 0);
      start();
    });
    resize.observe(canvas);
    const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; start(); });
    observer.observe(canvas);
    document.addEventListener('visibilitychange', start);
    motion.addEventListener('change', start);
    return () => { cancelAnimationFrame(frame); resize.disconnect(); observer.disconnect(); document.removeEventListener('visibilitychange', start); motion.removeEventListener('change', start); };
  }, []);
  return <canvas ref={ref} className="signal-field" aria-hidden="true" />;
}
