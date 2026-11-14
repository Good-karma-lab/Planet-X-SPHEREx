# Spectrum tab: quick look, dotted line, catalogue references (2026-11-14)

Before (deployed site) and after (this branch, dev server against the deployed API; `/api/v1/xmatch` from the extras branch for the references):

| Place | Before | After |
|---|---|---|
| saturated star 18h00m15.80s +04d22m06.6s, 5′ | `before_saturated_18h00m15.80s.jpg`: grey jagged cube line on a second MJy/sr axis, white dotted line | `after_saturated_18h00m15.80s.jpg`: no cube line, no dotted line, saturated message kept, J H Ks W1 W2 as open diamonds in mJy and listed below |
| Barnard's Star, 5′ (saturated) | `before_barnard_5arcmin.jpg` | `after_barnard_5arcmin.jpg`: W1 and W2 references (2MASS has no usable J H Ks for it) |
| normal faint source 18h00m14.67s +04d21m32.7s | `before_faint_18h00m14.67s.jpg` | `after_faint_18h00m14.67s.jpg` (417 points, unchanged) and `after_normal_18h00m08.30s.jpg` |

The placeholder stage of a normal source (cube line before the first measured point) was not captured: the measurement starts within a
second; `web/tests/unit/quicklook.test.ts` covers the rule for what is drawn.
