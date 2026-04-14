function renderStackedArea(containerId, legendId, seriesData, formatBytes) {
  const container = d3.select("#" + containerId);
  const legendContainer = d3.select("#" + legendId);
  const margin = {top: 20, right: 200, bottom: 40, left: 80};
  const width = 900 - margin.left - margin.right;
  const height = 400 - margin.top - margin.bottom;

  // Parse dates
  const parseDate = d3.timeParse("%Y-%m-%d");
  const dates = seriesData.dates.map(d => parseDate(d));
  const projects = seriesData.projects;
  const values = seriesData.values;
  const measured = seriesData.measured;

  // Sort projects by latest size (largest first = bottom of stack)
  const projectGrowth = projects.map((p, j) => {
    const vals = values[j];
    const last = vals[vals.length - 1];
    return {project: p, latestSize: last};
  });
  projectGrowth.sort((a, b) => b.latestSize - a.latestSize);
  const sortedProjects = projectGrowth.map(pg => pg.project);

  const svg = container.append("svg")
    .attr("viewBox", [0, 0, 900, 400])
    .style("max-width", "900px")
    .append("g")
    .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

  const color = d3.scaleOrdinal()
    .domain(sortedProjects)
    .range(d3.schemeTableau10.concat(d3.schemePaired));

  const projectIndex = new Map(projects.map((p, i) => [p, i]));

  const x = d3.scaleTime()
    .domain(d3.extent(dates))
    .range([0, width]);

  const y = d3.scaleLinear().range([height, 0]);

  const area = d3.area()
    .x((d, i) => x(dates[i]))
    .y0(d => y(d[0]))
    .y1(d => y(d[1]));

  // Groups for layered drawing order
  const areaGroup = svg.append("g");
  const dotGroup = svg.append("g");

  // Axes
  const spanDays = (dates[dates.length - 1] - dates[0]) / (1000 * 60 * 60 * 24);
  let tickInterval, tickFmt;
  if (spanDays <= 30) {
    tickInterval = d3.timeWeek.every(1);
    tickFmt = d3.timeFormat("%b %d");
  } else if (spanDays <= 180) {
    tickInterval = d3.timeWeek.every(2);
    tickFmt = d3.timeFormat("%b %d");
  } else if (spanDays <= 365) {
    tickInterval = d3.timeMonth.every(1);
    tickFmt = d3.timeFormat("%b %Y");
  } else {
    tickInterval = d3.timeMonth.every(3);
    tickFmt = d3.timeFormat("%b %Y");
  }

  svg.append("g")
    .attr("transform", "translate(0," + height + ")")
    .call(d3.axisBottom(x).ticks(tickInterval).tickFormat(tickFmt));

  const yAxis = svg.append("g");
  const yAxisRight = svg.append("g")
    .attr("transform", "translate(" + width + ",0)");

  // Hidden project tracking and redraw
  const hidden = new Set();

  function updateStats() {
    let totalSize = 0, totalGrowth = 0, totalFiles = 0, totalCompressible = 0;
    projects.forEach((p, j) => {
      if (hidden.has(p)) return;
      totalSize += seriesData.latestBytes[j];
      totalGrowth += seriesData.latestBytes[j] - seriesData.earliestBytes[j];
      totalFiles += seriesData.files[j];
      totalCompressible += seriesData.compressible[j];
    });
    document.getElementById("stat-size").textContent = formatBytes(totalSize);
    document.getElementById("stat-growth").textContent = formatBytes(totalGrowth);
    document.getElementById("stat-files").textContent = totalFiles.toLocaleString();
    document.getElementById("stat-compressible").textContent = formatBytes(totalCompressible);
  }

  function redraw() {
    updateStats();
    const visibleKeys = sortedProjects.filter(p => !hidden.has(p));

    const tableData = dates.map((d, i) => {
      const row = {date: d};
      projects.forEach((p, j) => { row[p] = hidden.has(p) ? 0 : values[j][i]; });
      return row;
    });

    const stack = d3.stack()
      .keys(visibleKeys)
      .order(d3.stackOrderNone)
      .offset(d3.stackOffsetNone);

    const stackedData = stack(tableData);

    const yMax = d3.max(stackedData, layer => d3.max(layer, d => d[1])) || 0;
    y.domain([0, yMax]).nice();
    yAxis.transition().duration(300).call(d3.axisLeft(y).ticks(6).tickFormat(d => formatBytes(d)));
    yAxisRight.transition().duration(300).call(
      d3.axisRight(y).ticks(6).tickFormat(d => formatBytes(d))
    );

    // Areas
    const paths = areaGroup.selectAll("path").data(stackedData, d => d.key);
    paths.exit().remove();
    paths.enter().append("path")
      .attr("fill", d => color(d.key))
      .attr("fill-opacity", 0.8)
      .merge(paths)
      .on("mousemove", function(event, d) {
        showProjectTooltip(event, d.key);
      })
      .on("mouseleave", function() {
        hideTooltip();
      })
      .transition().duration(300)
      .attr("d", area);

    // Dots
    dotGroup.selectAll("*").remove();
    stackedData.forEach(layer => {
      const projKey = layer.key;
      const pi = projects.indexOf(projKey);
      const projMeasured = measured[pi];
      const dots = [];
      layer.forEach((d, i) => {
        if (projMeasured[i] && d[1] > d[0]) dots.push({i: i, d: d});
      });
      dotGroup.selectAll(null)
        .data(dots)
        .join("circle")
        .attr("cx", pt => x(dates[pt.i]))
        .attr("cy", pt => y(pt.d[1]))
        .attr("r", 3)
        .attr("fill", color(projKey))
        .attr("stroke", "#fff")
        .attr("stroke-width", 0.5);
    });
  }

  redraw();

  // Tooltip
  const tooltip = container.append("div")
    .style("position", "absolute")
    .style("background", "rgba(255,255,255,0.95)")
    .style("border", "1px solid #ccc")
    .style("border-radius", "4px")
    .style("padding", "8px")
    .style("font-size", "12px")
    .style("pointer-events", "none")
    .style("display", "none");

  function projectHistoryHtml(project) {
    const pi = projectIndex.get(project);
    const projectValues = values[pi];
    const projectMeasured = measured[pi];
    const rows = [];
    for (let i = 0; i < dates.length; i++) {
      if (projectMeasured[i]) {
        rows.push(
          "<div>" +
          d3.timeFormat("%Y-%m-%d")(dates[i]) +
          ": " +
          formatBytes(projectValues[i]) +
          "</div>"
        );
      }
    }
    return (
      "<strong>" + project + "</strong><br>" +
      "<span style='color:" + color(project) + "'>■</span> Observed sizes<br>" +
      rows.join("")
    );
  }

  function positionTooltip(event) {
    const rect = container.node().getBoundingClientRect();
    tooltip.style("left", (event.clientX - rect.left + 15) + "px")
           .style("top", (event.clientY - rect.top - 10) + "px");
  }

  function showProjectTooltip(event, project) {
    tooltip.style("display", "block").html(projectHistoryHtml(project));
    positionTooltip(event);
  }

  function hideTooltip() {
    tooltip.style("display", "none");
  }

  // Group projects by owner (if available)
  const owners = seriesData.owners;
  const hasOwners = owners != null;

  function renderProjectButton(container, project) {
    const btn = container.append("button")
      .attr("type", "button")
      .attr("class", "legend-item")
      .datum(project)
      .on("click", function(event) {
        event.preventDefault();
        if (hidden.has(project)) hidden.delete(project);
        else hidden.add(project);
        syncLegend();
        redraw();
      });

    btn.append("span")
      .attr("class", "legend-swatch")
      .style("background-color", color(project));

    btn.append("span")
      .attr("class", "legend-label")
      .text(project);
  }

  if (hasOwners) {
    const ownerOrder = [];
    const ownerGroups = new Map();
    sortedProjects.forEach(p => {
      const owner = owners[p] || p;
      if (!ownerGroups.has(owner)) {
        ownerGroups.set(owner, []);
        ownerOrder.push(owner);
      }
      ownerGroups.get(owner).push(p);
    });

    ownerOrder.forEach(owner => {
      const groupProjects = ownerGroups.get(owner);
      const group = legendContainer.append("div").attr("class", "owner-group");

      group.append("button")
        .attr("type", "button")
        .attr("class", "owner-header")
        .text(owner)
        .on("click", function(event) {
          event.preventDefault();
          const allHidden = groupProjects.every(p => hidden.has(p));
          groupProjects.forEach(p => {
            if (allHidden) hidden.delete(p); else hidden.add(p);
          });
          syncLegend();
          redraw();
        });

      const projectsDiv = group.append("div").attr("class", "owner-projects");
      groupProjects.forEach(project => renderProjectButton(projectsDiv, project));
    });
  } else {
    const projectsDiv = legendContainer.append("div").attr("class", "owner-projects");
    sortedProjects.forEach(project => renderProjectButton(projectsDiv, project));
  }

  function syncLegend() {
    legendContainer.selectAll(".legend-item")
      .classed("is-hidden", function() { return hidden.has(d3.select(this).datum()); });
    if (hasOwners) {
      legendContainer.selectAll(".owner-group").each(function() {
        const grp = d3.select(this);
        const items = grp.selectAll(".legend-item");
        const allHidden = items.filter(function() {
          return !hidden.has(d3.select(this).datum());
        }).empty();
        grp.select(".owner-header").classed("is-hidden", allHidden);
      });
    }
  }

  syncLegend();

  d3.select("#legend-select-all").on("click", function() {
    hidden.clear();
    syncLegend();
    redraw();
  });

  d3.select("#legend-deselect-all").on("click", function() {
    sortedProjects.forEach(p => hidden.add(p));
    syncLegend();
    redraw();
  });
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
