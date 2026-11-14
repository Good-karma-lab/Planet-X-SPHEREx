/** Plays the videos of the Method page only while they are on screen (a page of many videos must not decode them all at once). */
export function playWhenVisible(root: HTMLElement): () => void {
  const videos = [...root.querySelectorAll<HTMLVideoElement>('video.mf-media')];
  if (!videos.length || typeof IntersectionObserver === 'undefined') return () => {};
  const reduce = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        const v = e.target as HTMLVideoElement;
        if (reduce) continue;
        if (e.isIntersecting && e.intersectionRatio >= 0.4) void v.play().catch(() => {});
        else v.pause();
      }
    },
    { threshold: [0, 0.4, 0.7] },
  );
  for (const v of videos) io.observe(v);
  return () => io.disconnect();
}
