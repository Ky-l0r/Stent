// 折线图：只依赖字符串拼装，不引第三方图表库。
// 数据量（按天，最多 1800 点）与交互需求（看趋势，不做 zoom）都不值得拉一个 200KB 的依赖。

const PAD = { left: 52, right: 14, top: 16, bottom: 28 };

function niceMax(value) {
  const raw = Math.max(1, Number(value) || 1);
  const power = 10 ** Math.floor(Math.log10(raw));
  const scaled = raw / power;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return step * power;
}

function shortNumber(value) {
  const n = Number(value) || 0;
  if (n >= 100000000) return `${(n / 100000000).toFixed(1)} 亿`;
  if (n >= 10000) return `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)} 万`;
  return String(Math.round(n));
}

function xLabel(date) {
  const parts = String(date || "").split("-");
  return parts.length >= 3 ? `${parts[1]}-${parts[2]}` : String(date || "");
}

/**
 * 渲染折线图 SVG。
 * @param {Array<{date:string,value:number,count?:number}>} series
 * @param {{width?:number,height?:number,tip?:string}} options
 */
export function lineChart(series, options = {}) {
  const width = options.width || 760;
  const height = options.height || 240;
  const rows = (series || []).filter((row) => row && row.date);
  if (!rows.length) return "";

  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const max = niceMax(Math.max(...rows.map((row) => Number(row.value) || 0)));
  const stepX = rows.length > 1 ? innerW / (rows.length - 1) : 0;

  const points = rows.map((row, index) => {
    const value = Number(row.value) || 0;
    return {
      x: PAD.left + index * stepX,
      y: PAD.top + innerH - (value / max) * innerH,
      value,
      date: row.date,
      count: row.count,
    };
  });

  const line = points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = `${line} L${points[points.length - 1].x.toFixed(1)},${(PAD.top + innerH).toFixed(1)} L${points[0].x.toFixed(1)},${(PAD.top + innerH).toFixed(1)} Z`;

  const grid = [0, 0.25, 0.5, 0.75, 1]
    .map((ratio) => {
      const y = PAD.top + innerH * ratio;
      const label = shortNumber(max * (1 - ratio));
      return `<line class="chart__grid" x1="${PAD.left}" y1="${y.toFixed(1)}" x2="${width - PAD.right}" y2="${y.toFixed(1)}"/>
        <text class="chart__axis" x="${PAD.left - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end">${label}</text>`;
    })
    .join("");

  // 只标 4 个日期：点太密时标签会糊成一片
  const tickIndexes = rows.length <= 4
    ? rows.map((_, index) => index)
    : [0, Math.round((rows.length - 1) / 3), Math.round(((rows.length - 1) * 2) / 3), rows.length - 1];
  const ticks = [...new Set(tickIndexes)]
    .map((index) => {
      const p = points[index];
      const anchor = index === 0 ? "start" : index === rows.length - 1 ? "end" : "middle";
      return `<text class="chart__axis" x="${p.x.toFixed(1)}" y="${height - 8}" text-anchor="${anchor}">${xLabel(p.date)}</text>`;
    })
    .join("");

  const dots = points
    .map(
      (p) =>
        `<circle class="chart__dot" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="2.5">
          <title>${p.date}：${shortNumber(p.value)}${p.count ? `（${p.count} 条有数据）` : ""}</title>
        </circle>`
    )
    .join("");

  return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="趋势折线图">
    <defs>
      <linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--primary)" stop-opacity=".22"/>
        <stop offset="100%" stop-color="var(--primary)" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${grid}${ticks}
    <path class="chart__area" d="${area}" fill="url(#chart-fill)"/>
    <path class="chart__line" d="${line}"/>
    ${dots}
  </svg>`;
}
