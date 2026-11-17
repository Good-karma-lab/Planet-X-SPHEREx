// The windows the mobile and tablet layouts are captured and walked at (CSS px; dsf = device pixels per px).
import { devices } from '@playwright/test';

export const SIZES = {
  p375: { width: 375, height: 812, dsf: 3, mobile: true },
  p390: { width: 390, height: 844, dsf: 3, mobile: true },
  l812: { width: 812, height: 375, dsf: 3, mobile: true },
  l844: { width: 844, height: 390, dsf: 3, mobile: true },
  t768: { width: 768, height: 1024, dsf: 2, mobile: true },
  t820: { width: 820, height: 1180, dsf: 2, mobile: true },
  t1024: { width: 1024, height: 768, dsf: 2, mobile: true },
  t1180: { width: 1180, height: 820, dsf: 2, mobile: true },
  // Playwright's own device profiles (the window Safari really gives a page, its user agent, touch)
  i13: { device: 'iPhone 13' },
  i13l: { device: 'iPhone 13 landscape' },
  ipad: { device: 'iPad (gen 7)' },
  ipadl: { device: 'iPad (gen 7) landscape' },
  d1280: { width: 1280, height: 800, dsf: 1, mobile: false },
  d1512: { width: 1512, height: 860, dsf: 2, mobile: false },
  d1920: { width: 1920, height: 1080, dsf: 1, mobile: false },
};

for (const v of Object.values(SIZES)) {
  if (!v.device) continue;
  const d = devices[v.device];
  Object.assign(v, { width: d.viewport.width, height: d.viewport.height, dsf: d.deviceScaleFactor, mobile: true, userAgent: d.userAgent });
}
