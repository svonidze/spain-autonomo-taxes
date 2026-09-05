(function chartsModule(global) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const VIEW_WIDTH = 640;
  const CARTESIAN_HEIGHT = 240;
  // Full currency ticks need a wider gutter than the other chart types.
  const CARTESIAN_LEFT_MARGIN = 96;
  const ROW_HEIGHT = 30;
  const BULLET_HEIGHT = 84;
  const MARGIN = {top: 14, right: 16, bottom: 26, left: 68};
  const FILL_GAP = 2;
  const TICK_TARGET = 4;
  const TONES = new Set(["info", "warning", "accent", "danger", "muted"]);
  const ALLOWED_ATTRIBUTES = new Set([
    "class",
    "viewBox",
    "role",
    "aria-label",
    "x",
    "y",
    "width",
    "height",
    "x1",
    "y1",
    "x2",
    "y2",
    "points",
    "d",
    "cx",
    "cy",
    "r",
    "id",
    "patternUnits",
    "text-anchor",
    "fill",
    "stroke-width",
    "focusable",
  ]);

  function round2(value) {
    return Math.round(value * 100) / 100;
  }

  function sceneWidth(spec) {
    const requested = Number(spec && spec.width);
    if (!Number.isFinite(requested) || requested <= 0) return VIEW_WIDTH;
    return Math.min(Math.max(Math.round(requested), 280), 1600);
  }

  function isValue(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function toneClass(tone) {
    return TONES.has(tone) ? `chart-tone-${tone}` : "chart-tone-muted";
  }

  function niceStep(rawStep) {
    if (rawStep <= 0) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const scaled = rawStep / power;
    let base = 10;
    if (scaled <= 1) base = 1;
    else if (scaled <= 2) base = 2;
    else if (scaled <= 5) base = 5;
    return base * power;
  }

  function buildTicks(minValue, maxValue) {
    const step = niceStep((maxValue - minValue) / TICK_TARGET);
    const ticks = [];
    const first = Math.ceil(minValue / step) * step;
    for (let value = first; value <= maxValue + step / 1000; value += step) {
      ticks.push(round2(value));
    }
    return ticks;
  }

  function domainOf(numbers) {
    let minValue = 0;
    let maxValue = 0;
    numbers.forEach((value) => {
      if (!isValue(value)) return;
      if (value < minValue) minValue = value;
      if (value > maxValue) maxValue = value;
    });
    if (minValue === 0 && maxValue === 0) maxValue = 1;
    return [minValue, maxValue];
  }

  function scaleFactory(minValue, maxValue, pixelStart, pixelEnd) {
    const span = maxValue - minValue || 1;
    return function scale(value) {
      const ratio = (value - minValue) / span;
      return round2(pixelStart + (pixelEnd - pixelStart) * ratio);
    };
  }

  function seriesTitle(series, bucket, value, formatValue) {
    const rendered = isValue(value) ? formatValue(value) : "—";
    return `${series.label} · ${bucket}: ${rendered}`;
  }

  function markClass(series) {
    if (series.kind === "line") {
      return series.pattern === "dashed"
        ? `chart-line chart-line-dashed ${toneClass(series.tone)}`
        : `chart-line ${toneClass(series.tone)}`;
    }
    if (series.pattern === "outline") {
      return `chart-mark-outline ${toneClass(series.tone)}`;
    }
    return `chart-mark ${toneClass(series.tone)}`;
  }

  function buildCartesianScene(spec) {
    const buckets = spec.buckets || [];
    const series = (spec.series || []).map((entry) => ({
      key: String(entry.key),
      label: String(entry.label),
      kind: entry.kind === "line" ? "line" : "bar",
      stack: entry.stack ? String(entry.stack) : String(entry.key),
      tone: entry.tone,
      pattern: entry.pattern || "solid",
      values: (entry.values || []).slice(0, buckets.length),
    }));
    const hasData = series.some((entry) => entry.values.some(isValue));
    const viewWidth = sceneWidth(spec);
    const scene = {
      chartId: String(spec.chartId),
      kind: "cartesian",
      viewBox: {width: viewWidth, height: CARTESIAN_HEIGHT},
      buckets,
      series,
      legend: series.map((entry) => ({
        key: entry.key,
        label: entry.label,
        tone: entry.tone,
        pattern: entry.pattern,
        kind: entry.kind,
      })),
      hatchTones: [],
      grid: [],
      marks: [],
      bucketLabels: [],
      empty: !hasData || buckets.length === 0,
    };
    if (scene.empty) return scene;

    const barSeries = series.filter((entry) => entry.kind === "bar");
    const stackOrder = [];
    barSeries.forEach((entry) => {
      if (!stackOrder.includes(entry.stack)) stackOrder.push(entry.stack);
    });
    const stackTotals = [];
    buckets.forEach((bucket, index) => {
      stackOrder.forEach((stack) => {
        let positive = 0;
        let negative = 0;
        barSeries
          .filter((entry) => entry.stack === stack)
          .forEach((entry) => {
            const value = entry.values[index];
            if (!isValue(value)) return;
            if (value >= 0) positive += value;
            else negative += value;
          });
        stackTotals.push(positive, negative);
      });
    });
    const lineValues = series
      .filter((entry) => entry.kind === "line")
      .flatMap((entry) => entry.values.filter(isValue));
    const [minValue, maxValue] = domainOf(stackTotals.concat(lineValues));
    const plotTop = MARGIN.top;
    const plotBottom = CARTESIAN_HEIGHT - MARGIN.bottom;
    const plotLeft = CARTESIAN_LEFT_MARGIN;
    const plotRight = viewWidth - MARGIN.right;
    const yScale = scaleFactory(minValue, maxValue, plotBottom, plotTop);
    const bandWidth = (plotRight - plotLeft) / buckets.length;
    const columnWidth = round2(
      Math.max((bandWidth - FILL_GAP * (stackOrder.length + 1)) / Math.max(stackOrder.length, 1), 2)
    );

    buildTicks(minValue, maxValue).forEach((value) => {
      scene.grid.push({
        y: yScale(value),
        x1: plotLeft,
        x2: plotRight,
        label: spec.formatValue(value),
      });
    });
    scene.baselineY = yScale(0);
    scene.baselineX1 = plotLeft;
    scene.baselineX2 = plotRight;

    const labelStep = bandWidth >= 34 ? 1 : Math.max(1, Math.ceil(34 / bandWidth));
    buckets.forEach((bucket, index) => {
      const bandStart = plotLeft + bandWidth * index;
      if (index % labelStep === 0) {
        scene.bucketLabels.push({
          x: round2(bandStart + bandWidth / 2),
          y: plotBottom + 16,
          label: String(bucket),
        });
      }
      stackOrder.forEach((stack, stackIndex) => {
        let positiveBase = 0;
        let negativeBase = 0;
        const x = round2(bandStart + FILL_GAP + stackIndex * (columnWidth + FILL_GAP));
        barSeries
          .filter((entry) => entry.stack === stack)
          .forEach((entry) => {
            const value = entry.values[index];
            if (!isValue(value)) return;
            const from = value >= 0 ? positiveBase : negativeBase;
            const to = from + value;
            if (value >= 0) positiveBase = to;
            else negativeBase = to;
            const yTop = Math.min(yScale(from), yScale(to));
            const height = round2(Math.max(Math.abs(yScale(from) - yScale(to)) - FILL_GAP / 2, 0));
            if (entry.pattern === "hatched" && !scene.hatchTones.includes(entry.tone)) {
              scene.hatchTones.push(entry.tone);
            }
            scene.marks.push({
              kind: "rect",
              seriesKey: entry.key,
              className: markClass(entry),
              hatchTone: entry.pattern === "hatched" ? entry.tone : null,
              x,
              y: round2(yTop),
              width: columnWidth,
              height,
              title: seriesTitle(entry, bucket, value, spec.formatValue),
            });
          });
      });
    });

    series
      .filter((entry) => entry.kind === "line")
      .forEach((entry) => {
        let segment = [];
        const segments = [];
        entry.values.forEach((value, index) => {
          if (!isValue(value)) {
            if (segment.length) segments.push(segment);
            segment = [];
            return;
          }
          const x = round2(plotLeft + bandWidth * index + bandWidth / 2);
          segment.push([x, yScale(value)]);
        });
        if (segment.length) segments.push(segment);
        segments.forEach((points, segmentIndex) => {
          if (points.length === 1) {
            scene.marks.push({
              kind: "point",
              seriesKey: `${entry.key}-${segmentIndex}`,
              className: `chart-dot ${toneClass(entry.tone)}`,
              cx: points[0][0],
              cy: points[0][1],
              title: entry.label,
            });
            return;
          }
          scene.marks.push({
            kind: "polyline",
            seriesKey: `${entry.key}-${segmentIndex}`,
            className: markClass(entry),
            points: points.map((point) => point.join(",")).join(" "),
            title: entry.label,
          });
        });
      });
    return scene;
  }

  function buildHorizontalBarsScene(spec) {
    const rows = (spec.rows || []).map((row) => ({
      key: String(row.key),
      label: String(row.label),
      secondaryLabel: row.secondaryLabel ? String(row.secondaryLabel) : "",
      total: row.total,
      segments: (row.segments || []).map((segment) => ({
        key: String(segment.key),
        label: String(segment.label),
        tone: segment.tone,
        pattern: segment.pattern || "solid",
        value: segment.value,
      })),
    }));
    const legend = [];
    rows.forEach((row) => {
      row.segments.forEach((segment) => {
        if (!legend.some((entry) => entry.key === segment.key)) {
          legend.push({
            key: segment.key,
            label: segment.label,
            tone: segment.tone,
            pattern: segment.pattern,
            kind: "bar",
          });
        }
      });
    });
    const totals = rows.map((row) =>
      row.segments.reduce((sum, segment) => sum + (isValue(segment.value) ? Math.max(segment.value, 0) : 0), 0)
    );
    const requestedRowHeight = Number(spec.rowHeight);
    const rowHeight = Number.isFinite(requestedRowHeight)
      ? Math.min(Math.max(Math.round(requestedRowHeight), ROW_HEIGHT), 56)
      : ROW_HEIGHT;
    const height = MARGIN.top + rows.length * rowHeight + 24;
    const viewWidth = sceneWidth(spec);
    const scene = {
      chartId: String(spec.chartId),
      kind: "horizontal-bars",
      viewBox: {width: viewWidth, height},
      rows,
      legend,
      hatchTones: [],
      marks: [],
      rowLabels: [],
      valueLabels: [],
      empty: rows.length === 0 || !rows.some((row) => row.segments.some((segment) => isValue(segment.value))),
    };
    if (scene.empty) return scene;
    const requestedLabelWidth = Number(spec.rowLabelWidth);
    const maxLabelWidth = Number.isFinite(requestedLabelWidth)
      ? Math.min(Math.max(Math.round(requestedLabelWidth), 90), 320)
      : 150;
    const labelWidth = Math.min(maxLabelWidth, Math.max(90, Math.round(viewWidth * 0.28)));
    const plotLeft = labelWidth + 8;
    const valueLabelWidth = (spec.totalValueLabel || spec.totalLabel) ? 112 : 64;
    const plotRight = Math.max(plotLeft + 2, viewWidth - MARGIN.right - valueLabelWidth);
    const maxTotal = Math.max(...totals, 1);
    const xScale = scaleFactory(0, maxTotal, plotLeft, plotRight);
    rows.forEach((row, index) => {
      const y = MARGIN.top + index * rowHeight;
      const hasSecondaryLabel = Boolean(row.secondaryLabel);
      const barHeight = rowHeight === ROW_HEIGHT ? ROW_HEIGHT - 10 : 20;
      const barY = rowHeight === ROW_HEIGHT ? y : round2(y + (rowHeight - barHeight) / 2);
      // The label column is right-anchored, so an overlong name would be
      // clipped at its start; shorten the end and keep the full text as a title.
      const maxChars = Math.max(6, Math.floor(labelWidth / 6.5));
      const clipped = row.label.length > maxChars;
      const rowTitle = hasSecondaryLabel ? `${row.label} (${row.secondaryLabel})` : row.label;
      scene.rowLabels.push({
        x: labelWidth,
        y: round2(y + rowHeight / 2 + (hasSecondaryLabel ? -2 : 4)),
        label: clipped ? `${row.label.slice(0, maxChars - 1)}…` : row.label,
        title: clipped ? rowTitle : null,
        className: "chart-axis-text",
      });
      if (hasSecondaryLabel) {
        scene.rowLabels.push({
          x: labelWidth,
          y: round2(y + rowHeight / 2 + 11),
          label: row.secondaryLabel,
          title: null,
          className: "chart-axis-subtext",
        });
      }
      let base = 0;
      row.segments.forEach((segment) => {
        if (!isValue(segment.value)) return;
        const value = Math.max(segment.value, 0);
        const xStart = xScale(base);
        const xEnd = xScale(base + value);
        base += value;
        if (segment.pattern === "hatched" && !scene.hatchTones.includes(segment.tone)) {
          scene.hatchTones.push(segment.tone);
        }
        scene.marks.push({
          kind: "rect",
          seriesKey: `${row.key}-${segment.key}`,
          className: markClass(segment),
          hatchTone: segment.pattern === "hatched" ? segment.tone : null,
          x: xStart,
          y: barY,
          width: round2(Math.max(xEnd - xStart - FILL_GAP / 2, 0)),
          height: barHeight,
          title: seriesTitle(segment, rowTitle, segment.value, spec.formatValue),
        });
      });
      const displayedTotal = isValue(row.total) ? row.total : totals[index];
      const totalValue = spec.formatValue(displayedTotal);
      scene.valueLabels.push({
        x: round2(xScale(base) + 6),
        y: round2(barY + barHeight / 2 + 4),
        label: (spec.totalValueLabel || spec.totalLabel)
          ? `${spec.totalValueLabel || spec.totalLabel}: ${totalValue}`
          : totalValue,
      });
    });
    return scene;
  }

  function buildBulletScene(spec) {
    const ranges = (spec.ranges || []).filter((range) => isValue(range.value));
    const measure = spec.measure || null;
    const measureValue = measure && isValue(measure.value) ? measure.value : null;
    const values = ranges
      .map((range) => range.value)
      .concat(measureValue === null ? [] : [measureValue]);
    const viewWidth = sceneWidth(spec);
    const scene = {
      chartId: String(spec.chartId),
      kind: "bullet",
      viewBox: {width: viewWidth, height: BULLET_HEIGHT},
      legend: ranges
        .map((range) => ({
          key: range.key,
          label: range.label,
          tone: range.tone,
          pattern: "solid",
          kind: "bar",
        }))
        .concat(
          measure
            ? [{key: measure.key, label: measure.label, tone: measure.tone, pattern: "solid", kind: "bar"}]
            : []
        ),
      hatchTones: [],
      marks: [],
      valueLabels: [],
      measureUnavailable: measure !== null && measureValue === null,
      empty: values.length === 0,
    };
    if (scene.empty) return scene;
    const plotLeft = MARGIN.left;
    const plotRight = viewWidth - MARGIN.right;
    const maxValue = Math.max(...values) * 1.05 || 1;
    const xScale = scaleFactory(0, maxValue, plotLeft, plotRight);
    const bandTop = 18;
    ranges.forEach((range, index) => {
      scene.marks.push({
        kind: "rect",
        seriesKey: range.key,
        className: `chart-band ${toneClass(range.tone)}`,
        hatchTone: null,
        x: plotLeft,
        y: round2(bandTop + index * 4),
        width: round2(xScale(range.value) - plotLeft),
        height: round2(34 - index * 8),
        title: `${range.label}: ${spec.formatValue(range.value)}`,
      });
      scene.valueLabels.push({
        x: round2(xScale(range.value)),
        y: 14,
        label: spec.formatValue(range.value),
      });
    });
    if (measureValue !== null) {
      scene.marks.push({
        kind: "rect",
        seriesKey: measure.key,
        className: `chart-mark ${toneClass(measure.tone)}`,
        hatchTone: null,
        x: plotLeft,
        y: 30,
        width: round2(xScale(measureValue) - plotLeft),
        height: 10,
        title: `${measure.label}: ${spec.formatValue(measureValue)}`,
      });
    }
    return scene;
  }

  function setAttributes(element, attributes) {
    Object.keys(attributes).forEach((name) => {
      if (!ALLOWED_ATTRIBUTES.has(name)) return;
      element.setAttribute(name, String(attributes[name]));
    });
  }

  function createSvgElement(doc, tag, attributes) {
    const element = doc.createElementNS(SVG_NS, tag);
    if (attributes) setAttributes(element, attributes);
    return element;
  }

  function appendTitle(doc, element, text) {
    if (!text) return;
    const title = createSvgElement(doc, "title");
    title.textContent = text;
    element.appendChild(title);
  }

  function hatchPatternId(chartId, tone) {
    return `${chartId}-hatch-${tone}`;
  }

  function mountScene(container, scene) {
    const doc = container.ownerDocument;
    while (container.firstChild) container.removeChild(container.firstChild);
    const svg = createSvgElement(doc, "svg", {
      class: "chart-svg",
      viewBox: `0 0 ${scene.viewBox.width} ${scene.viewBox.height}`,
      role: "img",
      "aria-label": scene.ariaLabel || "",
      focusable: "false",
    });
    if (scene.hatchTones.length) {
      const defs = createSvgElement(doc, "defs");
      scene.hatchTones.forEach((tone) => {
        const pattern = createSvgElement(doc, "pattern", {
          id: hatchPatternId(scene.chartId, tone),
          class: toneClass(tone),
          width: 6,
          height: 6,
          patternUnits: "userSpaceOnUse",
        });
        pattern.appendChild(
          createSvgElement(doc, "path", {
            d: "M0 6 L6 0",
            class: "chart-hatch-line",
          })
        );
        defs.appendChild(pattern);
      });
      svg.appendChild(defs);
    }
    (scene.grid || []).forEach((line) => {
      svg.appendChild(
        createSvgElement(doc, "line", {
          class: "chart-grid",
          x1: line.x1,
          x2: line.x2,
          y1: line.y,
          y2: line.y,
        })
      );
      const label = createSvgElement(doc, "text", {
        class: "chart-axis-text",
        x: line.x1 - 6,
        y: line.y + 3,
        "text-anchor": "end",
      });
      label.textContent = line.label;
      svg.appendChild(label);
    });
    if (typeof scene.baselineY === "number") {
      svg.appendChild(
        createSvgElement(doc, "line", {
          class: "chart-baseline",
          x1: scene.baselineX1,
          x2: scene.baselineX2,
          y1: scene.baselineY,
          y2: scene.baselineY,
        })
      );
    }
    scene.marks.forEach((mark) => {
      let element;
      if (mark.kind === "rect") {
        element = createSvgElement(doc, "rect", {
          class: mark.className,
          x: mark.x,
          y: mark.y,
          width: mark.width,
          height: mark.height,
        });
        if (mark.hatchTone) {
          element.setAttribute(
            "fill",
            `url(#${hatchPatternId(scene.chartId, mark.hatchTone)})`
          );
        }
      } else if (mark.kind === "point") {
        element = createSvgElement(doc, "circle", {
          class: mark.className,
          cx: mark.cx,
          cy: mark.cy,
          r: 3.5,
        });
      } else {
        element = createSvgElement(doc, "polyline", {
          class: mark.className,
          points: mark.points,
        });
      }
      appendTitle(doc, element, mark.title);
      svg.appendChild(element);
    });
    (scene.rowLabels || []).forEach((entry) => {
      const label = createSvgElement(doc, "text", {
        class: entry.className || "chart-axis-text",
        x: entry.x,
        y: entry.y,
        "text-anchor": "end",
      });
      label.textContent = entry.label;
      appendTitle(doc, label, entry.title);
      svg.appendChild(label);
    });
    (scene.valueLabels || []).forEach((entry) => {
      const label = createSvgElement(doc, "text", {
        class: "chart-value-text",
        x: entry.x,
        y: entry.y,
      });
      label.textContent = entry.label;
      svg.appendChild(label);
    });
    (scene.bucketLabels || []).forEach((entry) => {
      const label = createSvgElement(doc, "text", {
        class: "chart-axis-text",
        x: entry.x,
        y: entry.y,
        "text-anchor": "middle",
      });
      label.textContent = entry.label;
      svg.appendChild(label);
    });
    container.appendChild(svg);
    return svg;
  }

  function buildLegend(doc, legend) {
    const list = doc.createElement("ul");
    list.className = "chart-legend";
    legend.forEach((entry) => {
      const item = doc.createElement("li");
      const swatch = doc.createElement("span");
      swatch.className = `chart-swatch chart-swatch-${entry.pattern} chart-swatch-${entry.kind} ${toneClass(entry.tone)}`;
      const label = doc.createElement("span");
      label.textContent = entry.label;
      item.appendChild(swatch);
      item.appendChild(label);
      list.appendChild(item);
    });
    return list;
  }

  function buildDataTable(doc, spec, headers, rows) {
    const details = doc.createElement("details");
    details.className = "chart-data";
    details.open = Boolean(spec.tableInitiallyOpen);
    const summary = doc.createElement("summary");
    summary.textContent = spec.tableLabel || "Data";
    details.appendChild(summary);
    const wrap = doc.createElement("div");
    wrap.className = "table-wrap";
    const table = doc.createElement("table");
    const head = doc.createElement("thead");
    const headRow = doc.createElement("tr");
    headers.forEach((header) => {
      const cell = doc.createElement("th");
      cell.textContent = header;
      headRow.appendChild(cell);
    });
    head.appendChild(headRow);
    table.appendChild(head);
    const body = doc.createElement("tbody");
    rows.forEach((cells) => {
      const bodyRow = doc.createElement("tr");
      cells.forEach((value, index) => {
        const cell = doc.createElement("td");
        if (index > 0) cell.className = "amount";
        cell.setAttribute("data-label", headers[index] || "");
        cell.textContent = value;
        bodyRow.appendChild(cell);
      });
      body.appendChild(bodyRow);
    });
    table.appendChild(body);
    wrap.appendChild(table);
    details.appendChild(wrap);
    return details;
  }

  function renderFigure(container, spec, scene, headers, rows) {
    const doc = container.ownerDocument;
    while (container.firstChild) container.removeChild(container.firstChild);
    const figure = doc.createElement("figure");
    figure.className = spec.tablePrimaryOnNarrow
      ? "chart-figure chart-table-primary-on-narrow"
      : "chart-figure";
    const caption = doc.createElement("figcaption");
    if (scene.empty || !spec.expandAction) {
      caption.textContent = spec.title;
    } else {
      const title = doc.createElement("span");
      title.textContent = spec.title;
      caption.appendChild(title);
      const expand = doc.createElement("button");
      expand.type = "button";
      expand.className = "chart-expand-button";
      expand.textContent = spec.expandAction.label;
      expand.addEventListener("click", () => spec.expandAction.handler(expand));
      caption.appendChild(expand);
    }
    figure.appendChild(caption);
    if (scene.empty) {
      const empty = doc.createElement("div");
      empty.className = "empty-state chart-empty-state";
      empty.textContent = spec.emptyMessage;
      figure.appendChild(empty);
      container.appendChild(figure);
      return figure;
    }
    scene.ariaLabel = spec.ariaLabel || spec.title;
    const host = doc.createElement("div");
    host.className = "chart-host";
    figure.appendChild(host);
    mountScene(host, scene);
    if (scene.measureUnavailable && spec.unavailableMessage) {
      const note = doc.createElement("p");
      note.className = "chart-note chart-tone-muted";
      note.textContent = spec.unavailableMessage;
      figure.appendChild(note);
    }
    if (spec.note) {
      const info = doc.createElement("p");
      info.className = "chart-note chart-tone-muted";
      info.textContent = spec.note;
      figure.appendChild(info);
    }
    if (scene.legend.length > 1) {
      figure.appendChild(buildLegend(doc, scene.legend));
    }
    figure.appendChild(buildDataTable(doc, spec, headers, rows));
    container.appendChild(figure);
    return figure;
  }

  function formatCell(spec, value) {
    return isValue(value) ? spec.formatValue(value) : "—";
  }

  function horizontalRowTotal(row) {
    if (isValue(row.total)) return row.total;
    return (row.segments || []).reduce(
      (sum, segment) => sum + (isValue(segment.value) ? segment.value : 0),
      0
    );
  }

  function horizontalRowHasShares(row) {
    const total = horizontalRowTotal(row);
    const segments = row.segments || [];
    return total > 0
      && segments.length > 0
      && segments.every((segment) => isValue(segment.value) && segment.value >= 0)
      && segments.reduce((sum, segment) => sum + segment.value, 0) === total;
  }

  function formatHorizontalSegment(spec, row, segment) {
    const amount = formatCell(spec, segment ? segment.value : null);
    if (!spec.showSegmentShares) return amount;
    if (!segment || !horizontalRowHasShares(row)) {
      return `${amount} · ${spec.shareUnavailableLabel || "—"}`;
    }
    const formatPercent = typeof spec.formatPercent === "function"
      ? spec.formatPercent
      : (ratio) => `${round2(ratio * 100)}%`;
    return `${amount} · ${formatPercent(segment.value / horizontalRowTotal(row))}`;
  }

  function renderCartesian(container, spec) {
    const scene = buildCartesianScene(spec);
    const headers = [spec.bucketLabel || ""].concat(
      scene.legend.map((entry) => entry.label)
    );
    const rows = (spec.buckets || []).map((bucket, index) =>
      [String(bucket)].concat(
        scene.series.map((entry) => formatCell(spec, entry.values[index]))
      )
    );
    return renderFigure(container, spec, scene, headers, rows);
  }

  function renderHorizontalBars(container, spec) {
    const scene = buildHorizontalBarsScene(spec);
    const headers = [spec.bucketLabel || ""].concat(
      scene.legend.map((entry) => entry.label)
    );
    if (spec.totalLabel) headers.push(spec.totalLabel);
    const rows = (spec.rows || []).map((row) =>
      [row.secondaryLabel ? `${row.label} · ${row.secondaryLabel}` : row.label].concat(
        scene.legend.map((entry) => {
          const segment = (row.segments || []).find(
            (candidate) => String(candidate.key) === entry.key
          );
          return formatHorizontalSegment(spec, row, segment);
        }),
        spec.totalLabel ? [formatCell(spec, horizontalRowTotal(row))] : []
      )
    );
    return renderFigure(container, spec, scene, headers, rows);
  }

  function renderBullet(container, spec) {
    const scene = buildBulletScene(spec);
    const rows = (spec.ranges || [])
      .concat(spec.measure ? [spec.measure] : [])
      .map((entry) => [entry.label, formatCell(spec, entry.value)]);
    return renderFigure(container, spec, scene, [spec.bucketLabel || "", ""], rows);
  }

  global.AutonomoCharts = {
    buildCartesianScene,
    buildHorizontalBarsScene,
    buildBulletScene,
    mountScene,
    renderCartesian,
    renderHorizontalBars,
    renderBullet,
  };
})(typeof window !== "undefined" ? window : globalThis);
