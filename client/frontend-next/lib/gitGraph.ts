// lib/gitGraph.ts — 提交图泳道布局算法（借鉴 Ridge 的 Git Graph）。
//
// 纯逻辑、无 DOM/Canvas 依赖，可单测：给定按拓扑序（新→旧）排列的提交（含 parents），
// 给每个提交分配一条泳道（列），并算出它到各父提交的连线落在哪条泳道、哪些泳道在此汇入。
// Canvas 组件照这个布局画圆点 + 连线即可。

export interface RawCommit {
  hash: string;
  parents: string[];
  refs?: string[];      // 分支/标签名
  subject?: string;
  author?: string;
  date?: string;
}

export interface LaidOutCommit {
  hash: string;
  parents: string[];
  refs: string[];
  subject: string;
  author: string;
  date: string;
  lane: number;                              // 本提交所在列
  color: number;                             // 颜色索引（按泳道循环）
  parentLanes: { parent: string; lane: number }[];   // 到各父的连线落在哪条泳道
  closingLanes: number[];                    // 在本行汇入本提交、随后释放的泳道
  laneCount: number;                         // 本行存在的最大列数（用于算宽度）
}

const PALETTE_SIZE = 8;   // 颜色循环数（与组件调色板一致）

/** 计算提交图布局。commits 须按新→旧排列（git log 默认序）。 */
export function computeGraphLayout(commits: RawCommit[]): LaidOutCommit[] {
  // lanes[i] = 该泳道当前“期待”的提交 hash（即沿这条线往下走会遇到的下一个提交）；null=空闲
  const lanes: (string | null)[] = [];
  const laneColor: number[] = [];   // 每条泳道的颜色索引（创建时固定）
  let colorSeq = 0;

  const firstFreeLane = (): number => {
    for (let i = 0; i < lanes.length; i++) if (lanes[i] == null) return i;
    lanes.push(null);
    laneColor.push(0);
    return lanes.length - 1;
  };

  const rows: LaidOutCommit[] = [];

  for (const c of commits) {
    // 找出当前期待本提交的所有泳道
    const expecting: number[] = [];
    for (let i = 0; i < lanes.length; i++) if (lanes[i] === c.hash) expecting.push(i);

    let lane: number;
    if (expecting.length > 0) {
      lane = expecting[0];   // 取最左的那条作为本提交的列
    } else {
      lane = firstFreeLane();          // 没人期待 → 分支顶端，新开一列
      laneColor[lane] = colorSeq++ % PALETTE_SIZE;
    }

    // 其余期待本提交的泳道（expecting[1..]）在此行汇入本提交，随后释放
    const closingLanes = expecting.slice(1);
    for (const l of closingLanes) lanes[l] = null;

    // 给父提交分配泳道
    const parentLanes: { parent: string; lane: number }[] = [];
    if (c.parents.length === 0) {
      lanes[lane] = null;              // 根提交：这条线到此结束
    } else {
      // 第一个父沿用本列
      lanes[lane] = c.parents[0];
      parentLanes.push({ parent: c.parents[0], lane });
      // 其余父（合并提交）→ 已有期待该父的列则复用，否则新开
      for (let pi = 1; pi < c.parents.length; pi++) {
        const ph = c.parents[pi];
        let pl = lanes.indexOf(ph);
        if (pl === -1) {
          pl = firstFreeLane();
          laneColor[pl] = colorSeq++ % PALETTE_SIZE;
        }
        lanes[pl] = ph;
        parentLanes.push({ parent: ph, lane: pl });
      }
    }

    // 本行的列数 = 当前 lanes 数组里最后一个非空之后的长度（用最大已用列 +1）
    let used = 0;
    for (let i = 0; i < lanes.length; i++) if (lanes[i] != null) used = i + 1;
    used = Math.max(used, lane + 1);
    for (const l of closingLanes) used = Math.max(used, l + 1);

    rows.push({
      hash: c.hash,
      parents: c.parents,
      refs: c.refs || [],
      subject: c.subject || "",
      author: c.author || "",
      date: c.date || "",
      lane,
      color: laneColor[lane] ?? 0,
      parentLanes,
      closingLanes,
      laneCount: used,
    });
  }

  return rows;
}

/** 整张图用到的最大列数（决定 Canvas 宽度）。 */
export function maxLanes(rows: LaidOutCommit[]): number {
  let m = 1;
  for (const r of rows) m = Math.max(m, r.laneCount);
  return m;
}
