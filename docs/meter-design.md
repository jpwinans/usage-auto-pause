# Meter design and verification

The gauges use an upper semicircle: −8h left, 0h at the top, +8h right.
Needles clamp at the endpoints; numeric readings preserve the actual lead.
Glass reflections stay behind the dial, with an opaque dark readout background.
Warning arcs use red `#ff5148`; warning text uses `#ff5b4d`.

## Contrast

The portable test suite checks the sRGB relative-luminance formula against
[WCAG text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
(4.5:1) and [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
(3:1). Neutral labels use a conservative bright-glass background bound; the
warning text sits below the reflections. Colored arcs have a dark under-stroke.

| Element | Foreground / background | Contrast |
|---|---|---:|
| Small neutral labels | `#c3d1c9` / `#42584e` | 4.85:1 |
| Warning text | `#ff5b4d` / `#1f2e27` | 4.64:1 |
| Red arc | `#ff5148` / `#08110f` | 5.94:1 |
| Minor ticks | `#93aa9c` / `#42584e` | 3.09:1 |

Signed numbers, AHEAD/BEHIND labels, and textual HOLD/STALE/UNAVAILABLE statuses
supplement color. These checks cover the designed palette, not full WCAG
conformance. Screen-reader coverage has not been audited; OS controls and terminal
colors depend on the user's environment.

## Manual visual checks

The native synthetic demo was inspected in normal, loading, unavailable,
expired, stale, weekly hold, hard-cutoff, behind-pace, and on-pace states.
Both allowance and period selectors were exercised. Minimum, default, and wide
windows were checked, including the longest stale and Fable hold labels.
Labels and controls remained visible; upper semicircle direction, zero-up
needle orientation, endpoint clamping, and signed out-of-range readings were
verified. The final red palette and readout/status spacing were rechecked at
minimum and default sizes.

The browser shares the palette and corrected gauge geometry, but its visual
inspection was blocked by UI approval review. Automated checks do not substitute
for that remaining browser visual check. The published native screenshot uses
only synthetic readings.

## Responsive native layout

The default content size is 772 × 446 points, with Codex and Claude side by
side. The cards stack when content width / height falls below 1.2. This
shape-based breakpoint preserves orientation during proportional corner
resizing. The minimum content size is 297 × 223 points. Both layouts scale uniformly
to fit the available height, preserving circular dials and keeping selectors
inside the Claude card. Very short stacked windows have smaller text; enlarge
the height for easier reading. Window geometry uses a new saved preference so
the previous vertical default does not override the first horizontal launch.

The responsive demo was manually checked in all nine display states, with both
Claude selectors, the full-size vertical stack, and the smallest window. Long
stale/hold labels and controls remained inside their cards. User testing
confirmed the corrected corner-resize behavior.

Corner resizing was subsequently verified by dragging the live demo from
772 × 446 to approximately 579 × 335 points and back, preserving the horizontal
row and scaling both gauges. Dragging only the side edge inward stacked the
cards; widening restored the row. Regression tests cover proportional resizing
in both orientations and width-only reflow. The earlier fixed-width breakpoint
and 446-point minimum height prevented useful horizontal shrinking; the
shape-based breakpoint and lower minimum height correct that behavior.
