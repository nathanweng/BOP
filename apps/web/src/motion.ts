import { flushSync } from 'react-dom';

/** Native layout transitions keep the actual media elements mounted. */
export function transition(update: () => void) {
  if (document.startViewTransition && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.startViewTransition(() => flushSync(update));
  } else {
    update();
  }
}
