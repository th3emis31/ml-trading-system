---
name: chart-image
family: media
description: Render a chart or image from real data to a file, so a result can be looked at rather than read. Use when the user asks for a chart, a picture, a graph, a visual, or says show me.
---

# Chart image

A chart is a claim drawn to scale. Its job is to let someone see a shape a table hides — the smooth
hill that made SL 3000 credible, and the lone spike that made gold spacing 111 a fluke, look
identical in a table and obvious in a plot.

## Procedure

1. **Start from the real rows.** Never a summary, never a remembered figure. If the data has to be
   fetched, fetch it; if it cannot be fetched, do not draw anything.
2. **Plot every point, then check the count.** `points_plotted == len(rows)` is the single most
   useful check here, because silently dropping rows is the most common way a chart lies.
3. **Label the axes with values the data actually reaches.** A y-axis topping out at 100 when the
   series peaks at 63 wastes half the drawing and flattens the shape.
4. **Mark the thing being argued about.** The baseline, the threshold, the candidate — whatever the
   chart exists to compare against.
5. **Write to a file and state its path and size.** A chart nobody can find is not a deliverable.

## What good looks like

- The count of plotted points equals the count of source rows, and that is stated.
- Axis labels name real values; nothing runs outside the drawing's bounds.
- Colour carries meaning, not decoration — one hue for the baseline, one for the candidate, and a
  neutral for everything else.
- The caption says what the reader should conclude and over what window, because a chart without its
  window is a chart of an unknown period.
- Nothing is smoothed or interpolated without saying so. A smoothed line showing a trend the raw data
  does not have is the drawing equivalent of inventing data.

## Acceptance

```acceptance
number: points_plotted >= 1
number: rows_dropped == 0
file: data exists
ask: does the axis top match the data's real maximum rather than a round number above it?
```
