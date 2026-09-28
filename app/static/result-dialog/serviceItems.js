/**
 * 服务项目面板的静态映射（对应订单新增页的「服务项目（选填）」模块）。
 *
 * 三个板块 × 类别（分组）× 服务。这里定义的顺序就是面板展示顺序，也是提交报文
 * `serviceList` 的顺序 —— 前端发送前用 `orderServiceCodes()` 按这个顺序重排，
 * 后端按收到的顺序逐项上送。
 *
 * 数据来自需求给的对照表（序号 / 系统 / 类别 / 服务代码 / 服务名称），
 * 类别列与面板里的分组标题一一对应：
 *
 *   出港操作：主营（OA0010 配舱服务）、运单制作（AA0110/AA0120）、
 *             进仓及标签（AA0410/AG0135）、外场（AA0230/AA0240）
 *   出港配套：报关（AA0610）、仓库（AG0120/AA0850/AG0130/AG0145）、材料（AA0810）、
 *             鉴定（AG0115/AG0125）、运联（AA0510）、快递（AG0110）、
 *             发送（AA0140/AA0160/AA0150）
 *   进港服务：主营（OB0020）、报关（AB0620）、运联（AB0520）、仓库（AB0420）
 *
 * 三点说明：
 * - 对照表里第 1 行的代码写作「0A0010」，按 poOrder 的既有口径应为字母 O 的
 *   `OA0010`（我们此前提交成功用的也是 OA0010），这里按 OA0010 处理；
 * - OA0010 是特例：一个代码对应两个选项（唯凯配舱 / 唯凯代操作），显示名由
 *   「订舱操作」决定（报文 czlx：自货 / 代操作），见 poOrder 的 serviceItem.getTitle()；
 * - 对照表里所有服务的「系统」都写空出，但 OB/AB 开头那几项实际是进港（空进）服务。
 *   面板三个板块始终全部展示，勾哪些由操作员决定，提交时原样上送。
 */

/** 服务代码 → 面板显示名（用需求对照表的「服务名称」，OA0010 除外，见下）。 */
export const SERVICE_NAMES = {
  OA0010: "配舱服务",
  // 出港操作
  AA0110: "总单制作",
  AA0120: "分单制作",
  AA0410: "进唯凯仓库",
  AG0135: "标签制作",
  AA0230: "唯凯安检",
  AA0240: "唯凯交接",
  // 出港配套服务
  AA0610: "空出报关",
  AA0510: "提送货",
  AG0120: "改包装",
  AA0850: "大件装卸",
  AG0130: "挂衣",
  AG0145: "仓储",
  AA0810: "材料供应",
  AG0115: "磁检",
  AG0125: "化检",
  AG0110: "快递代付",
  AA0140: "AMS发送",
  AA0160: "海关联系单发送",
  AA0150: "天运通发送",
  // 进港服务
  OB0020: "到货通知",
  AB0620: "空进报关",
  AB0520: "提送货",
  AB0420: "空进进仓",
};

/** 配舱服务的服务代码（唯凯配舱 / 唯凯代操作共用它）。 */
export const BOOKING_SERVICE_CODE = "OA0010";

/** 订舱操作取值：自货 = 唯凯配舱、代操作 = 唯凯代操作（与工具条一致）。 */
export const BOOKING_PICKUP = "自货";
export const BOOKING_AGENT = "代操作";

const entry = (code) => ({ code, label: SERVICE_NAMES[code] });

export const SERVICE_BOARDS = [
  {
    title: "出港操作",
    groups: [
      {
        // 一个服务代码两个选项：勾任一都写成 OA0010，同时把「订舱操作」切到对应值
        title: "主营",
        items: [
          { code: BOOKING_SERVICE_CODE, label: "唯凯配舱", variant: BOOKING_PICKUP },
          { code: BOOKING_SERVICE_CODE, label: "唯凯代操作", variant: BOOKING_AGENT },
        ],
      },
      { title: "运单制作", items: ["AA0110", "AA0120"].map(entry) },
      { title: "进仓及标签", items: ["AA0410", "AG0135"].map(entry) },
      { title: "外场", items: ["AA0230", "AA0240"].map(entry) },
    ],
  },
  {
    title: "出港配套服务",
    groups: [
      { title: "报关", items: ["AA0610"].map(entry) },
      { title: "仓库", items: ["AG0120", "AA0850", "AG0130", "AG0145"].map(entry) },
      { title: "材料", items: ["AA0810"].map(entry) },
      { title: "鉴定", items: ["AG0115", "AG0125"].map(entry) },
      { title: "运联", items: ["AA0510"].map(entry) },
      { title: "快递", items: ["AG0110"].map(entry) },
      { title: "发送", items: ["AA0140", "AA0160", "AA0150"].map(entry) },
    ],
  },
  {
    title: "进港服务",
    groups: [
      { title: "主营", items: ["OB0020"].map(entry) },
      { title: "报关", items: ["AB0620"].map(entry) },
      { title: "运联", items: ["AB0520"].map(entry) },
      { title: "仓库", items: ["AB0420"].map(entry) },
    ],
  },
];

/** 全部服务代码（面板顺序，已去重）。 */
export const ALL_SERVICE_CODES = [
  ...new Set(
    SERVICE_BOARDS.flatMap((board) =>
      board.groups.flatMap((group) => group.items.map((item) => item.code)),
    ),
  ),
];

/** 该服务代码在面板里的显示名；未知代码原样返回（便于排查数据问题）。 */
export function serviceName(code) {
  return SERVICE_NAMES[code] || String(code || "");
}

/**
 * 把勾选的服务代码按面板顺序重排并去重。
 *
 * 勾选是「点一下加一个」，集合里的顺序等于点击顺序，直接用会让报文里的服务项顺序
 * 随操作次序变化；这里统一按面板顺序输出，报文与界面看起来一致。
 */
export function orderServiceCodes(codes) {
  const picked = new Set(codes || []);
  return ALL_SERVICE_CODES.filter((code) => picked.has(code));
}

/** 默认勾选：唯凯配舱（配舱服务），与 poOrder 订单新增页的默认值一致。 */
export const DEFAULT_SERVICE_CODES = [BOOKING_SERVICE_CODE];

/**
 * 归一服务代码：始终带上配舱服务（OA0010），并按面板顺序排列。
 *
 * OA0010 由「订舱操作」决定（唯凯配舱 / 唯凯代操作），面板里不单独勾选，
 * 所以进 state 与提交报文前统一用它，避免旧草稿或手工改状态时漏掉它。
 */
export function normalizeServiceCodes(codes) {
  return orderServiceCodes([BOOKING_SERVICE_CODE, ...(codes || [])]);
}

/**
 * 统计「操作员主动勾选」的服务数量。
 *
 * 不含配舱服务（OA0010）—— 它由工具条的「订舱操作」决定、在面板里是灰色不可点的，
 * 不该算进"已勾了几项"里，否则默认就显示 1 项。
 */
export function countPickedServices(codes) {
  return (codes || []).filter(
    (code) => code && code !== BOOKING_SERVICE_CODE,
  ).length;
}
