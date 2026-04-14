function renderSunburst(containerId, data, formatBytes) {
  const width = 700;
  const radius = width / 2;

  const RED = "#d9534f";
  const BLUE = "#5bc0de";
  const DARK_RED = "#ab3a3a";
  const COMPRESSIBLE_THRESHOLD = 0.3;

  function isCompressible(d) {
    return d.data.compressibleRatio != null && d.data.compressibleRatio >= COMPRESSIBLE_THRESHOLD;
  }

  function isCompressibleDir(d) {
    return !!d.children && isCompressible(d);
  }

  function isOutermostCompressibleDirectory(d) {
    if (!isCompressibleDir(d)) return false;
    const relDepth = d.depth - focus.depth;
    const visible = visibleNodes(focus);
    const outermostDepth = d3.max(visible, node => node.depth - focus.depth);
    return relDepth === outermostDepth;
  }

  function deltaColor(d) {
    if (isCompressible(d) && (!d.children || isOutermostCompressibleDirectory(d))) return DARK_RED;
    if (d.data.previousBytes != null) {
      return d.value > d.data.previousBytes ? RED : BLUE;
    }
    // No previous data (new, or no --previous given) -> red
    return RED;
  }

  function deltaOpacity(d) {
    if (isCompressible(d) && (!d.children || isOutermostCompressibleDirectory(d))) return 1;
    if (d.data.previousBytes == null) return 0.5;
    const cur = d.value;
    const prev = d.data.previousBytes;
    if (cur === prev) return 0.25;
    if (cur > prev) {
      // Growth: ratio of old/new → 0 for huge growth, 1 for tiny growth
      return 0.25 + 0.75 * (1 - prev / cur);
    }
    // Shrinkage: ratio of new/old → 0 for huge shrinkage, 1 for tiny shrinkage
    return 0.25 + 0.75 * (1 - cur / prev);
  }

  const root = d3.hierarchy(data)
    .sum(d => d.value || 0)
    .sort((a, b) => b.value - a.value);

  // Only render maxVisibleRings rings; deeper data exists for drill-down.
  const maxVisibleRings = 4;
  d3.partition().size([2 * Math.PI, radius])(root);
  const defaultBand = radius / (maxVisibleRings + 1);
  const centerRadius = defaultBand * 0.67;
  const outerBand = (radius - centerRadius) / maxVisibleRings;
  root.each(d => {
    if (d.depth === 0) {
      d.y0 = 0;
      d.y1 = centerRadius;
    } else if (d.depth <= maxVisibleRings) {
      d.y0 = centerRadius + (d.depth - 1) * outerBand;
      d.y1 = centerRadius + d.depth * outerBand;
    } else {
      d.y0 = 0;
      d.y1 = 0;
    }
    d.current = d;
  });

  const arc = d3.arc()
    .startAngle(d => d.x0)
    .endAngle(d => d.x1)
    .padAngle(d => Math.min((d.x1 - d.x0) / 2, 0.005))
    .padRadius(radius / 2)
    .innerRadius(d => d.y0)
    .outerRadius(d => d.y1 - 1);

  const currentArc = d3.arc()
    .startAngle(d => d.current.x0)
    .endAngle(d => d.current.x1)
    .padAngle(d => Math.min((d.current.x1 - d.current.x0) / 2, 0.005))
    .padRadius(radius / 2)
    .innerRadius(d => d.current.y0)
    .outerRadius(d => d.current.y1 - 1);

  const container = d3.select("#" + containerId);

  // Breadcrumb showing current drilldown path
  const breadcrumb = container.append("p")
    .style("text-align", "center")
    .style("font-size", "0.9em")
    .style("color", "#666")
    .style("margin", "0.5em 0");
  breadcrumb.text(root.data.name + " (" + formatBytes(root.value) + ")");

  const deltaLine = container.append("p")
    .style("text-align", "center")
    .style("font-size", "0.85em")
    .style("color", "#888")
    .style("margin", "0 0 0.5em 0");
  deltaLine.text(formatDelta(root));

  const resetBtn = container.append("div")
    .style("text-align", "center")
    .style("margin", "0 0 0.5em 0")
    .append("button")
    .text("Reset to top level")
    .style("display", "none")
    .style("cursor", "pointer");

  const svg = container.append("svg")
    .attr("viewBox", [-radius, -radius, width, width])
    .style("max-width", width + "px")
    .style("font", "10px sans-serif");

  let focus = root;

  const pathGroup = svg.append("g");
  const labelGroup = svg.append("g")
    .attr("pointer-events", "none");

  function labelTransform(d) {
    const angle = (d.current.x0 + d.current.x1) / 2;
    const angleDeg = angle * 180 / Math.PI;
    const r = d.current.y0 + 4;
    const flip = angleDeg > 180;
    return "rotate(" + (angleDeg - 90) + ") translate(" + r + ",0) rotate(" + (flip ? 180 : 0) + ")";
  }

  function labelAnchor(d) {
    const angle = (d.current.x0 + d.current.x1) / 2;
    return angle * 180 / Math.PI > 180 ? "end" : "start";
  }

  function labelVisible(d) {
    const relDepth = d.depth - focus.depth;
    return relDepth > 0 && relDepth <= maxVisibleRings
      && d.current.y1 > 0 && d.current.y0 > 0
      && (d.current.y0 + d.current.y1) / 2 * (d.current.x1 - d.current.x0) > 10;
  }

  // Approximate character width at 10px sans-serif
  const charWidth = 6;

  function truncateLabel(d) {
    // Limit to ring thickness, but also to the space before the diagram edge
    const ringThickness = d.current.y1 - d.current.y0 - 8;
    const spaceToEdge = radius - d.current.y0 - 4;
    const maxChars = Math.floor(Math.min(ringThickness, spaceToEdge) / charWidth);
    if (maxChars < 1) return "";
    const name = d.data.name;
    if (name.length <= maxChars) return name;
    if (maxChars <= 3) return name.slice(0, maxChars);
    return name.slice(0, maxChars - 1) + "\u2026";
  }

  function fullPath(d) {
    return d.ancestors().map(a => a.data.name).reverse().join("/");
  }

  function nodeKey(d) {
    return fullPath(d);
  }

  function visibleNodes(p) {
    return p.descendants().filter(d => d !== p && d.depth - p.depth <= maxVisibleRings);
  }

  function visiblePathNodes(p) {
    if (p === root) return visibleNodes(p);
    return [p].concat(visibleNodes(p));
  }

  function titleText(d) {
    let t = fullPath(d) + "\n" + formatBytes(d.value);
    const dt = formatDelta(d);
    if (dt) t += "\n" + dt;
    return t;
  }

  function handlePathClick(event, p) {
    if (focus === p) { p = p.parent || root; }
    // Don't zoom into leaf nodes (no children to show); zoom to parent instead
    else if (!p.children || p.children.length === 0) { p = p.parent || root; }
    zoomTo(p);
  }

  function updatePaths(t) {
    const paths = pathGroup.selectAll("path")
      .data(visiblePathNodes(focus), nodeKey);

    const exiting = paths.exit();
    const entered = paths.enter().append("path")
      .attr("fill", deltaColor)
      .style("cursor", "pointer")
      .on("click", handlePathClick)
      .each(d => {
        if (d.current !== d) return;
        const target = d.target || d;
        if (target === d) return;
        const angle = (target.x0 + target.x1) / 2;
        d.current = {x0: angle, x1: angle, y0: target.y0, y1: target.y0};
      });

    entered.append("title");

    const merged = entered.merge(paths)
      .attr("fill", deltaColor)
      .on("click", handlePathClick);

    merged.select("title").text(titleText);

    if (t) {
      exiting.transition(t)
        .tween("data", d => {
          const angle = (d.target.x0 + d.target.x1) / 2;
          const collapsed = {x0: angle, x1: angle, y0: d.target.y0, y1: d.target.y0};
          const i = d3.interpolate(d.current, collapsed);
          return t => { d.current = i(t); };
        })
        .attrTween("d", d => () => currentArc(d))
        .attr("fill-opacity", 0)
        .remove();

      merged.transition(t)
        .tween("data", d => {
          const i = d3.interpolate(d.current, d.target);
          return t => { d.current = i(t); };
        })
        .attrTween("d", d => () => currentArc(d))
        .attr("fill-opacity", d => d === focus ? 0 : deltaOpacity(d));
    } else {
      exiting.remove();
      merged
        .attr("fill-opacity", d => d === focus ? 0 : deltaOpacity(d))
        .attr("d", d => {
          d.current = d.target || d.current;
          return currentArc(d);
        });
    }
  }

  function updateLabels() {
    const labels = labelGroup.selectAll("text")
      .data(visibleNodes(focus), nodeKey);

    labels.exit().remove();

    const entered = labels.enter().append("text")
      .attr("dy", "0.35em");

    const merged = entered.merge(labels)
      .text(truncateLabel);

    merged
      .attr("transform", labelTransform)
      .attr("text-anchor", labelAnchor)
      .attr("fill-opacity", d => labelVisible(d) ? 1 : 0);
  }

  updatePaths(null);
  updateLabels();

  function zoomTo(p) {
    focus = p;

    breadcrumb.text(fullPath(p) + " (" + formatBytes(p.value) + ")");
    deltaLine.text(formatDelta(p));
    resetBtn.style("display", p === root ? "none" : "inline-block");

    // Remap y positions: show at most maxVisibleRings relative to the
    // clicked node, with the center circle shrunk to 2/3.
    const zoomCenter = centerRadius;
    const zoomOuter = outerBand;

    root.each(d => {
      const relDepth = d.depth - p.depth;
      let ty0, ty1;
      if (relDepth <= 0) {
        ty0 = 0;
        ty1 = relDepth === 0 ? zoomCenter : 0;
      } else if (relDepth <= maxVisibleRings) {
        ty0 = zoomCenter + (relDepth - 1) * zoomOuter;
        ty1 = zoomCenter + relDepth * zoomOuter;
      } else {
        ty0 = 0;
        ty1 = 0;
      }
      // Nodes outside the clicked subtree get collapsed to zero
      const inSubtree = d.x0 >= p.x0 && d.x1 <= p.x1;
      d.target = {
        x0: Math.max(0, Math.min(1, (d.x0 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
        x1: Math.max(0, Math.min(1, (d.x1 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
        y0: inSubtree || relDepth <= 0 ? ty0 : 0,
        y1: inSubtree || relDepth <= 0 ? ty1 : 0
      };
    });

    const t = svg.transition().duration(500);

    updatePaths(t);
    labelGroup.selectAll("text").transition(t).attr("fill-opacity", 0);
    t.end().then(() => updateLabels());
  }

  resetBtn.on("click", function() { zoomTo(root); });
}

function formatBytes(n) {
  if (n < 1000) return n + " B";
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let u = -1;
  do { n /= 1000; u++; } while (n >= 1000 && u < units.length - 1);
  while (true) {
    const d = n >= 100 ? 1 : n >= 10 ? 2 : 3;
    const rounded = Number(n.toFixed(d));
    if (rounded < 1000 || u >= units.length - 1) {
      return rounded.toFixed(d) + " " + units[u];
    }
    n = rounded / 1000;
    u++;
  }
}

function formatDelta(d) {
  if (d.data.previousBytes == null) return "";
  const current = d.value;
  const previous = d.data.previousBytes;
  const delta = current - previous;
  if (delta === 0) return "No change since previous scan";
  const absDelta = Math.abs(delta);
  if (delta > 0) {
    const pct = ((absDelta / current) * 100).toFixed(0);
    return "Grown by " + formatBytes(absDelta) + " (" + pct + "% of current size)";
  } else {
    const pct = ((absDelta / previous) * 100).toFixed(0);
    return "Shrank by " + formatBytes(absDelta) + " (" + pct + "% of original size)";
  }
}
