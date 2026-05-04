function renderStackedArea(containerId, projectLegendId, filterPanelId, seriesData, formatBytes) {
  const container = d3.select("#" + containerId);
  const projectLegendContainer = d3.select("#" + projectLegendId);
  const filterPanelContainer = d3.select("#" + filterPanelId);
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

  // Single active metadata filter. Null means all projects are visible.
  let activeFilter = null;

  function projectIsVisible(project) {
    if (activeFilter == null) return true;
    if (activeFilter.type === "legalOwner") {
      return legalOwners[project] === activeFilter.value;
    }
    if (activeFilter.type === "projectLead") {
      return (projectLeads[project] || []).includes(activeFilter.value);
    }
    return true;
  }

  function updateStats() {
    let totalSize = 0, totalGrowth = 0, totalFiles = 0, totalCompressible = 0;
    projects.forEach((p, j) => {
      if (!projectIsVisible(p)) return;
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
    const visibleKeys = sortedProjects.filter(projectIsVisible);

    const tableData = dates.map((d, i) => {
      const row = {date: d};
      projects.forEach((p, j) => { row[p] = projectIsVisible(p) ? values[j][i] : 0; });
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

  // Filter projects by metadata (if available)
  const legalOwners = seriesData.legalOwners || {};
  const projectLeads = seriesData.projectLeads || {};
  const hasMetadata = seriesData.legalOwners != null || seriesData.projectLeads != null;

  function renderProjectButton(container, project) {
    const item = container.append("div")
      .attr("class", "legend-item")
      .datum(project);

    item.append("span")
      .attr("class", "legend-swatch")
      .style("background-color", color(project));

    item.append("span")
      .attr("class", "legend-label")
      .text(project);
  }

  function renderProjectList() {
    projectLegendContainer.selectAll("*").remove();
    projectLegendContainer.append("h3").text("Projects");
    const projectsDiv = projectLegendContainer.append("div").attr("class", "project-list");
    sortedProjects.filter(projectIsVisible).forEach(project => {
      renderProjectButton(projectsDiv, project);
    });
  }

  function countVisibleForFilter(filter) {
    return sortedProjects.filter(p => {
      if (filter.type === "legalOwner") return legalOwners[p] === filter.value;
      return (projectLeads[p] || []).includes(filter.value);
    }).length;
  }

  function renderFilterOption(container, label, filter) {
    const btn = container.append("button")
      .attr("type", "button")
      .attr("class", "filter-option")
      .datum(filter)
      .on("click", function(event) {
        event.preventDefault();
        if (
          activeFilter &&
          activeFilter.type === filter.type &&
          activeFilter.value === filter.value
        ) {
          activeFilter = null;
        } else {
          activeFilter = filter;
        }
        syncLegend();
        redraw();
      });

    btn.append("span").text(label);
    btn.append("span")
      .attr("class", "filter-count")
      .text(countVisibleForFilter(filter));
  }

  if (hasMetadata) {
    const legalOwnerOrder = [];
    const seenLegalOwners = new Set();
    sortedProjects.forEach(p => {
      const owner = legalOwners[p];
      if (owner && !seenLegalOwners.has(owner)) {
        seenLegalOwners.add(owner);
        legalOwnerOrder.push(owner);
      }
    });

    if (legalOwnerOrder.length > 0) {
      const ownerSection = filterPanelContainer.append("section").attr("class", "filter-panel");
      ownerSection.append("h3").text("Legal owners");
      const ownerList = ownerSection.append("div").attr("class", "filter-list");
      legalOwnerOrder.sort().forEach(owner => {
        renderFilterOption(ownerList, owner, {type: "legalOwner", value: owner});
      });
    }

    const projectLeadOrder = [];
    const seenProjectLeads = new Set();
    sortedProjects.forEach(p => {
      (projectLeads[p] || []).forEach(lead => {
        if (!seenProjectLeads.has(lead)) {
          seenProjectLeads.add(lead);
          projectLeadOrder.push(lead);
        }
      });
    });

    if (projectLeadOrder.length > 0) {
      const leadSection = filterPanelContainer.append("section").attr("class", "filter-panel");
      leadSection.append("h3").text("Project leads");
      const leadList = leadSection.append("div").attr("class", "filter-list");
      projectLeadOrder.sort().forEach(lead => {
        renderFilterOption(leadList, lead, {type: "projectLead", value: lead});
      });
    }

  }
  renderProjectList();

  function syncLegend() {
    renderProjectList();
    filterPanelContainer.selectAll(".filter-option")
      .classed("is-active", function() {
        const filter = d3.select(this).datum();
        return activeFilter &&
          activeFilter.type === filter.type &&
          activeFilter.value === filter.value;
      });
  }

  syncLegend();
  redraw();
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
